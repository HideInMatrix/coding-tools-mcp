"""OAuth HTTP controller for authorization-server endpoints."""

from __future__ import annotations

import base64
import html
import json
import secrets
import urllib.parse
from typing import Any, Protocol

from .cimd import CIMDClientResolver, DEFAULT_CIMD_CLIENT_RESOLVER
from .oauth import (
    OAUTH_MAX_BODY_BYTES,
    valid_pkce_challenge,
    verify_pkce,
)
from .oauth_service import OAuthService


class OAuthHTTPContext(Protocol):
    runtime: Any
    headers: Any
    path: str

    def _base_url(self) -> str: ...

    def _json(
        self,
        status: int,
        payload: Any,
        headers: dict[str, str] | None = None,
    ) -> None: ...

    def _html(self, status: int, body: str) -> None: ...

    def _read(self, limit: int) -> bytes: ...

    def _form(self, limit: int = OAUTH_MAX_BODY_BYTES) -> dict[str, str]: ...

    def send_response(self, code: int, message: str | None = None) -> None: ...

    def send_header(self, keyword: str, value: str) -> None: ...

    def end_headers(self) -> None: ...


class OAuthHTTPController:
    """Translate OAuth HTTP requests into the runtime OAuth domain model."""

    def __init__(self, resolver: CIMDClientResolver | None = None) -> None:
        self._resolver = resolver or DEFAULT_CIMD_CLIENT_RESOLVER

    def authorization_metadata(self, handler: OAuthHTTPContext) -> dict[str, Any]:
        base = handler._base_url()
        metadata: dict[str, Any] = {
            "issuer": base,
            "authorization_endpoint": f"{base}/oauth/authorize",
            "token_endpoint": f"{base}/oauth/token",
            "registration_endpoint": f"{base}/oauth/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": [
                "none",
                "client_secret_post",
                "client_secret_basic",
            ],
            "scopes_supported": ["mcp", "offline_access"],
        }
        config = handler.runtime.oauth_service
        if config and config.cimd_enabled:
            metadata["client_id_metadata_document_supported"] = True
        if config and config.resource:
            metadata["protected_resources"] = [config.resource]
        return metadata

    def protected_resource_metadata(
        self,
        handler: OAuthHTTPContext,
    ) -> dict[str, Any]:
        base = handler._base_url()
        config = handler.runtime.oauth_service
        resource = config.resource if config and config.resource else base
        return {
            "resource": resource,
            "resource_name": "MicroMatrix Workbench",
            "authorization_servers": [base],
            "bearer_methods_supported": ["header"],
        }

    def register_post(self, handler: OAuthHTTPContext) -> None:
        config = handler.runtime.oauth_service
        if config is None:
            handler._json(404, {"error": "oauth_not_enabled"})
            return
        try:
            metadata = json.loads(handler._read(OAUTH_MAX_BODY_BYTES))
            if not isinstance(metadata, dict):
                raise ValueError("registration body must be an object")
            response = config.registry.register(metadata)
        except (ValueError, json.JSONDecodeError) as exc:
            handler._json(
                400,
                {
                    "error": "invalid_client_metadata",
                    "error_description": str(exc),
                },
            )
            return
        handler._json(201, response)

    def authorize_page(
        self,
        handler: OAuthHTTPContext,
        params: dict[str, str],
        error: str = "",
    ) -> str:
        hidden = "".join(
            f'<input type="hidden" name="{html.escape(key)}" '
            f'value="{html.escape(value, quote=True)}">'
            for key, value in params.items()
            if key != "password"
        )
        error_html = (
            f'<p style="color:#b42318">{html.escape(error)}</p>' if error else ""
        )
        authorize_action = html.escape(
            f"{handler._base_url()}/oauth/authorize",
            quote=True,
        )
        return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Authorize MicroMatrix Workbench</title>
<style>body{{font-family:system-ui,sans-serif;max-width:520px;margin:60px auto;padding:0 20px}}input,button{{box-sizing:border-box;width:100%;padding:12px;margin:8px 0}}small{{color:#666}}</style></head>
<body><h1>Authorize MicroMatrix Workbench</h1><p>A client is requesting access to the configured workspace.</p>{error_html}
<form method="post" action="{authorize_action}">{hidden}<label>Password<input type="password" name="password" autocomplete="current-password" required></label><button type="submit">Authorize</button></form>
<small>Only authorize clients you trust. MCP tools may read files, modify source code, and execute commands according to the configured permission mode.</small></body></html>"""

    def authorize_get(self, handler: OAuthHTTPContext) -> None:
        query = urllib.parse.parse_qs(
            urllib.parse.urlparse(handler.path).query,
            keep_blank_values=True,
        )
        params = {key: values[-1] if values else "" for key, values in query.items()}
        _, error = self._validate_authorize(handler, params)
        if error:
            handler._html(400, self.authorize_page(handler, params, error))
            return
        handler._html(200, self.authorize_page(handler, params))

    def authorize_post(self, handler: OAuthHTTPContext) -> None:
        config = handler.runtime.oauth_service
        if config is None:
            handler._json(404, {"error": "oauth_not_enabled"})
            return
        try:
            params = handler._form()
        except (ValueError, UnicodeDecodeError) as exc:
            handler._json(
                400,
                {"error": "invalid_request", "error_description": str(exc)},
            )
            return
        _, error = self._validate_authorize(handler, params)
        if error:
            handler._html(400, self.authorize_page(handler, params, error))
            return
        if not secrets.compare_digest(params.get("password", ""), config.password):
            handler._html(
                401,
                self.authorize_page(handler, params, "Incorrect password"),
            )
            return
        requested_resource = params.get("resource", "").strip()
        normalized_resource = config.normalize_resource(requested_resource)
        if (
            requested_resource
            and config.resource
            and normalized_resource != config.resource
        ):
            handler._html(
                400,
                self.authorize_page(
                    handler,
                    params,
                    "resource does not match this MCP server: "
                    f"expected {config.resource}, received {requested_resource}",
                ),
            )
            return
        code = config.issue_code(
            params["client_id"],
            params["redirect_uri"],
            params["code_challenge"],
            normalized_resource,
        )
        parsed = urllib.parse.urlparse(params["redirect_uri"])
        query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        query.append(("code", code))
        if params.get("state"):
            query.append(("state", params["state"]))
        if config.issuer:
            query.append(("iss", config.issuer))
        location = urllib.parse.urlunparse(
            parsed._replace(query=urllib.parse.urlencode(query))
        )
        handler.send_response(302)
        handler.send_header("Location", location)
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Content-Length", "0")
        handler.end_headers()

    def token_post(self, handler: OAuthHTTPContext) -> None:
        config = handler.runtime.oauth_service
        if config is None:
            handler._json(404, {"error": "oauth_not_enabled"})
            return
        try:
            params = handler._form()
        except (ValueError, UnicodeDecodeError) as exc:
            handler._json(
                400,
                {"error": "invalid_request", "error_description": str(exc)},
            )
            return
        grant_type = params.get("grant_type")
        if grant_type == "refresh_token":
            self._refresh_token_post(handler, config, params)
            return
        if grant_type != "authorization_code":
            handler._json(400, {"error": "unsupported_grant_type"})
            return
        code = config.consume_code(params.get("code", ""))
        if code is None:
            handler._json(
                400,
                {
                    "error": "invalid_grant",
                    "error_description": "authorization code is invalid or expired",
                },
            )
            return
        client_id, client, client_secret, auth_method = self._token_client(
            handler,
            config,
            params,
        )
        if client_id != code.client_id:
            handler._json(400, {"error": "invalid_grant"})
            return
        if not self._token_client_is_valid(
            handler,
            config,
            client_id,
            client,
            client_secret,
            auth_method,
        ):
            return
        if params.get("redirect_uri") != code.redirect_uri:
            handler._json(
                400,
                {"error": "invalid_grant", "error_description": "redirect_uri mismatch"},
            )
            return
        requested_resource = params.get("resource", "").strip()
        normalized_resource = config.normalize_resource(requested_resource)
        if requested_resource and code.resource and normalized_resource != code.resource:
            handler._json(
                400,
                {"error": "invalid_target", "error_description": "resource mismatch"},
            )
            return
        verifier = params.get("code_verifier", "")
        if not verify_pkce(verifier, code.challenge):
            handler._json(
                400,
                {
                    "error": "invalid_grant",
                    "error_description": "PKCE verification failed",
                },
            )
            return
        self._send_token_pair(
            handler,
            config,
            client_id=client_id,
            resource=code.resource,
        )

    def _validate_authorize(
        self,
        handler: OAuthHTTPContext,
        params: dict[str, str],
    ) -> tuple[Any, str] | tuple[None, str]:
        config = handler.runtime.oauth_service
        if config is None:
            return None, "OAuth is not enabled"
        client_id = params.get("client_id", "")
        try:
            client = self._resolver.resolve(config, client_id)
        except ValueError as exc:
            return None, f"Invalid client metadata: {exc}"
        if client is None:
            return None, "Unknown client_id"
        redirect_uri = params.get("redirect_uri", "")
        if redirect_uri not in client.redirect_uris:
            return None, "redirect_uri is not registered"
        if params.get("response_type") != "code":
            return None, "response_type must be code"
        if params.get("code_challenge_method") != "S256":
            return None, "code_challenge_method must be S256"
        challenge = params.get("code_challenge", "")
        if not valid_pkce_challenge(challenge):
            return None, "invalid code_challenge"
        return client, ""

    @staticmethod
    def _basic_client(handler: OAuthHTTPContext) -> tuple[str | None, str | None]:
        authorization = handler.headers.get("Authorization", "")
        if not authorization.lower().startswith("basic "):
            return None, None
        try:
            decoded = base64.b64decode(authorization[6:].strip()).decode("utf-8")
            client_id, client_secret = decoded.split(":", 1)
            return (
                urllib.parse.unquote(client_id),
                urllib.parse.unquote(client_secret),
            )
        except (ValueError, UnicodeDecodeError):
            return None, None

    def _token_client(
        self,
        handler: OAuthHTTPContext,
        config: OAuthService,
        params: dict[str, str],
    ) -> tuple[str, Any, str | None, str]:
        client_id = params.get("client_id", "")
        basic_id, basic_secret = self._basic_client(handler)
        if basic_id:
            client_id = basic_id
            client_secret = basic_secret
            auth_method = "client_secret_basic"
        else:
            client_secret = params.get("client_secret")
            auth_method = "none"
        try:
            client = self._resolver.resolve(config, client_id)
        except ValueError:
            client = None
        if not basic_id and client is not None:
            auth_method = client.token_endpoint_auth_method
        return client_id, client, client_secret, auth_method

    @staticmethod
    def _token_client_is_valid(
        handler: OAuthHTTPContext,
        config: OAuthService,
        client_id: str,
        client: Any,
        client_secret: str | None,
        auth_method: str,
    ) -> bool:
        if client is None:
            handler._json(401, {"error": "invalid_client"})
            return False
        if (
            client.token_endpoint_auth_method != "none"
            and not config.registry.authenticates(
                client_id,
                client_secret,
                auth_method,
            )
        ):
            handler._json(401, {"error": "invalid_client"})
            return False
        return True

    @staticmethod
    def _send_token_pair(
        handler: OAuthHTTPContext,
        config: OAuthService,
        *,
        client_id: str,
        resource: str,
    ) -> None:
        handler._json(
            200,
            {
                "access_token": config.tokens.issue_access_token(client_id),
                "refresh_token": config.tokens.issue_refresh_token(client_id, resource),
                "token_type": "Bearer",
                "expires_in": config.token_ttl,
            },
        )

    def _refresh_token_post(
        self,
        handler: OAuthHTTPContext,
        config: OAuthService,
        params: dict[str, str],
    ) -> None:
        client_id, client, client_secret, auth_method = self._token_client(
            handler,
            config,
            params,
        )
        if not self._token_client_is_valid(
            handler,
            config,
            client_id,
            client,
            client_secret,
            auth_method,
        ):
            return
        refresh_token = params.get("refresh_token", "")
        requested_resource = config.normalize_resource(
            params.get("resource", "").strip()
        )
        grant = config.tokens.consume_refresh_token(
            refresh_token,
            client_id=client_id,
            resource=requested_resource,
        )
        if grant is None:
            handler._json(
                400,
                {
                    "error": "invalid_grant",
                    "error_description": (
                        "refresh token is invalid, expired, or already used"
                    ),
                },
            )
            return
        self._send_token_pair(
            handler,
            config,
            client_id=client_id,
            resource=grant.resource,
        )


__all__ = ["OAuthHTTPController"]
