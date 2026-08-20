from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import tempfile
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping

from ..local_permission_broker import LocalWorkflowApprovalBrokerClient
from ..protocol import RequestContext
from ..schemas import validate_value
from .artifacts import ArtifactRef, ArtifactStore, RUN_ID_PATTERN
from .engine import EngineState, WorkflowEngine
from .models import ResourceScope
from .schema import validate_workbench_schema
from .registry import WorkflowRegistry
from .workflows import WorkflowDefinition, WorkflowNode


LOGGER = logging.getLogger(__name__)


RUN_STATUSES = frozenset(
    {
        "pending",
        "running",
        "waiting_model",
        "waiting_approval",
        "succeeded",
        "failed",
        "cancelled",
    }
)


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    approval_id: str
    run_id: str
    node_id: str
    title: str
    description: str
    status: str
    requested_at: int
    resolved_at: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "run_id": self.run_id,
            "node_id": self.node_id,
            "title": self.title,
            "description": self.description,
            "status": self.status,
            "requested_at": self.requested_at,
            "resolved_at": self.resolved_at,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ApprovalRequest":
        return cls(
            approval_id=str(value.get("approval_id") or ""),
            run_id=str(value.get("run_id") or ""),
            node_id=str(value.get("node_id") or ""),
            title=str(value.get("title") or ""),
            description=str(value.get("description") or ""),
            status=str(value.get("status") or "pending"),
            requested_at=int(value.get("requested_at") or 0),
            resolved_at=int(value.get("resolved_at") or 0),
        )


@dataclass(frozen=True, slots=True)
class WorkflowRun:
    run_id: str
    workflow_id: str
    workflow_version: int
    workflow_scope: str
    workflow_snapshot: dict[str, Any]
    workspace: str
    status: str
    engine_state: EngineState
    inputs: dict[str, Any] = field(default_factory=dict)
    node_states: dict[str, dict[str, Any]] = field(default_factory=dict)
    artifacts: tuple[ArtifactRef, ...] = ()
    approvals: tuple[ApprovalRequest, ...] = ()
    pending_action: dict[str, Any] | None = None
    retry_state: EngineState | None = None
    error: str = ""
    created_at: int = 0
    updated_at: int = 0
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "workflow_id": self.workflow_id,
            "workflow_version": self.workflow_version,
            "workflow_scope": self.workflow_scope,
            "workflow_snapshot": dict(self.workflow_snapshot),
            "workspace": self.workspace,
            "status": self.status,
            "engine_state": self.engine_state.to_dict(),
            "inputs": dict(self.inputs),
            "node_states": {key: dict(value) for key, value in self.node_states.items()},
            "artifacts": [item.to_dict() for item in self.artifacts],
            "approvals": [item.to_dict() for item in self.approvals],
            "pending_action": dict(self.pending_action) if self.pending_action else None,
            "retry_state": self.retry_state.to_dict() if self.retry_state else None,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def public_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "workflow_id": self.workflow_id,
            "workflow_version": self.workflow_version,
            "workflow_scope": self.workflow_scope,
            "workspace": self.workspace,
            "status": self.status,
            "engine_state": self.engine_state.to_dict(),
            "inputs": dict(self.inputs),
            "node_states": {key: dict(value) for key, value in self.node_states.items()},
            "artifacts": [item.to_dict() for item in self.artifacts],
            "approvals": [item.to_dict() for item in self.approvals],
            "pending_action": dict(self.pending_action) if self.pending_action else None,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "WorkflowRun":
        value = validate_workbench_schema(value, resource_type="workflow_run")
        schema_version = int(value.get("schema_version", 1))
        if schema_version != 1:
            raise ValueError(f"unsupported workflow run schema_version: {schema_version}")
        status = str(value.get("status") or "pending")
        if status not in RUN_STATUSES:
            raise ValueError(f"unsupported workflow run status: {status}")
        raw_pending = value.get("pending_action")
        raw_retry = value.get("retry_state")
        return cls(
            run_id=str(value.get("run_id") or ""),
            workflow_id=str(value.get("workflow_id") or ""),
            workflow_version=int(value.get("workflow_version") or 1),
            workflow_scope=str(value.get("workflow_scope") or ResourceScope.WORKSPACE.value),
            workflow_snapshot=dict(value.get("workflow_snapshot") or {}),
            workspace=str(value.get("workspace") or ""),
            status=status,
            engine_state=EngineState.from_mapping(dict(value.get("engine_state") or {})),
            inputs=dict(value.get("inputs") or {}),
            node_states={
                str(key): dict(item)
                for key, item in dict(value.get("node_states") or {}).items()
                if isinstance(item, Mapping)
            },
            artifacts=tuple(
                ArtifactRef.from_mapping(item)
                for item in value.get("artifacts", [])
                if isinstance(item, Mapping)
            ),
            approvals=tuple(
                ApprovalRequest.from_mapping(item)
                for item in value.get("approvals", [])
                if isinstance(item, Mapping)
            ),
            pending_action=dict(raw_pending) if isinstance(raw_pending, Mapping) else None,
            retry_state=(
                EngineState.from_mapping(dict(raw_retry))
                if isinstance(raw_retry, Mapping)
                else None
            ),
            error=str(value.get("error") or ""),
            created_at=int(value.get("created_at") or 0),
            updated_at=int(value.get("updated_at") or 0),
            schema_version=schema_version,
        )


class RunStore:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.directory = self.workspace / ".micromatrix-workbench" / "runs"

    @staticmethod
    def _validate_run_id(run_id: str) -> str:
        value = run_id.strip()
        if not RUN_ID_PATTERN.fullmatch(value):
            raise ValueError(f"invalid workflow run id: {run_id!r}")
        return value

    def _path(self, run_id: str) -> Path:
        return self.directory / self._validate_run_id(run_id) / "run.json"

    def get(self, run_id: str) -> WorkflowRun | None:
        path = self._path(run_id)
        if not path.is_file():
            return None
        return self._read(path)

    def list(self) -> tuple[WorkflowRun, ...]:
        if not self.directory.is_dir():
            return ()
        runs: list[WorkflowRun] = []
        for directory in self.directory.iterdir():
            if not directory.is_dir() or directory.is_symlink():
                continue
            if not RUN_ID_PATTERN.fullmatch(directory.name):
                continue
            path = directory / "run.json"
            if path.is_file():
                try:
                    runs.append(self._read(path))
                except RuntimeError as exc:
                    LOGGER.warning("Skipping invalid Workflow Run %s: %s", path, exc)
        return tuple(sorted(runs, key=lambda item: (item.created_at, item.run_id), reverse=True))

    def save(self, run: WorkflowRun) -> WorkflowRun:
        path = self._path(run.run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(path, run.to_dict())
        return run

    def prune(self, *, max_runs: int = 100) -> tuple[str, ...]:
        limit = max(1, int(max_runs))
        runs = list(self.list())
        if len(runs) <= limit:
            return ()
        removable = sorted(
            (
                run
                for run in runs
                if run.status in {"succeeded", "cancelled"}
            ),
            key=lambda item: (item.created_at, item.run_id),
        )
        deleted: list[str] = []
        remaining = len(runs)
        for run in removable:
            if remaining <= limit:
                break
            directory = self.directory / run.run_id
            shutil.rmtree(directory, ignore_errors=True)
            if not directory.exists():
                deleted.append(run.run_id)
                remaining -= 1
        return tuple(deleted)

    def _read(self, path: Path) -> WorkflowRun:
        if path.is_symlink():
            raise RuntimeError(f"Workflow Run 文件不允许是符号链接: {path}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Workflow Run 文件损坏: {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"Workflow Run 文件必须是 JSON object: {path}")
        try:
            return WorkflowRun.from_mapping(payload)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"Workflow Run 定义无效: {path}: {exc}") from exc

    @staticmethod
    def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
        fd, raw_temporary = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            text=True,
        )
        temporary = Path(raw_temporary)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


class WorkflowRunManager:
    def __init__(
        self,
        workspace: Path,
        *,
        engine: WorkflowEngine,
        registry: WorkflowRegistry,
        approval_broker: LocalWorkflowApprovalBrokerClient | None = None,
    ) -> None:
        self.workspace = workspace.resolve()
        self.engine = engine
        self.registry = registry
        self.store = RunStore(self.workspace)
        self.artifact_store = ArtifactStore(self.workspace)
        self.approval_broker = approval_broker
        self._republish_pending_approvals()

    @staticmethod
    def _pending_approval(run: WorkflowRun) -> ApprovalRequest | None:
        for approval in reversed(run.approvals):
            if approval.status == "pending":
                return approval
        return None

    def _publish_approval_request(
        self,
        run: WorkflowRun,
        approval: ApprovalRequest,
    ) -> str:
        broker = self.approval_broker
        if broker is None:
            return ""
        return broker.publish(
            run_id=run.run_id,
            node_id=approval.node_id,
            approval_id=approval.approval_id,
            title=approval.title,
            description=approval.description,
        )

    def _ensure_approval_request(self, run: WorkflowRun) -> WorkflowRun:
        if run.status != "waiting_approval" or self.approval_broker is None:
            return run
        approval = self._pending_approval(run)
        if approval is None:
            return run
        pending = dict(run.pending_action or {})
        request_id = str(pending.get("request_id") or "")
        if request_id and self.approval_broker.request_is_current(
            request_id,
            run_id=run.run_id,
            node_id=approval.node_id,
            approval_id=approval.approval_id,
        ):
            return run
        try:
            request_id = self._publish_approval_request(run, approval)
        except OSError:
            request_id = ""
        pending.update(
            {
                "type": "approval",
                "approval_id": approval.approval_id,
                "node_id": approval.node_id,
                "title": approval.title,
                "description": approval.description,
                "request_id": request_id,
            }
        )
        refreshed = replace(
            run,
            pending_action=pending,
            updated_at=int(time.time()),
        )
        self.store.save(refreshed)
        return refreshed

    def _republish_pending_approvals(self) -> None:
        if self.approval_broker is None:
            return
        for run in self.store.list():
            if run.status == "waiting_approval":
                self._ensure_approval_request(run)

    def _workflow(self, run: WorkflowRun) -> WorkflowDefinition:
        try:
            scope = ResourceScope(run.workflow_scope)
        except ValueError:
            scope = ResourceScope.WORKSPACE
        return WorkflowDefinition.from_mapping(
            run.workflow_snapshot,
            scope=scope,
            source=f"run:{run.run_id}",
        )

    @staticmethod
    def _node(workflow: WorkflowDefinition, node_id: str) -> WorkflowNode:
        for node in workflow.nodes:
            if node.id == node_id:
                return node
        raise KeyError(node_id)

    @staticmethod
    def _node_states(
        run: WorkflowRun,
        node_id: str,
        **changes: Any,
    ) -> dict[str, dict[str, Any]]:
        states = {key: dict(value) for key, value in run.node_states.items()}
        current = dict(states.get(node_id, {}))
        current.update(changes)
        states[node_id] = current
        return states

    def _save(self, run: WorkflowRun, **changes: Any) -> WorkflowRun:
        persisted = replace(
            run,
            updated_at=int(time.time()),
            **changes,
        )
        self.store.save(persisted)
        return persisted

    def _fail(
        self,
        run: WorkflowRun,
        *,
        node_id: str,
        message: str,
        retry_state: EngineState | None,
    ) -> WorkflowRun:
        return self._save(
            run,
            status="failed",
            pending_action=None,
            retry_state=retry_state,
            error=message,
            node_states=self._node_states(
                run,
                node_id,
                status="failed",
                error=message,
                updated_at=int(time.time()),
            ),
        )

    @staticmethod
    def _has_edge(
        workflow: WorkflowDefinition,
        node_id: str,
        outcome: str,
    ) -> bool:
        return any(
            edge.source == node_id and edge.condition == outcome
            for edge in workflow.edges
        )

    def start(
        self,
        workflow_id: str,
        *,
        inputs: Mapping[str, Any] | None = None,
        context: RequestContext | None = None,
    ) -> WorkflowRun:
        workflow = self.registry.get(workflow_id)
        if workflow is None:
            raise KeyError(workflow_id)
        input_values = dict(inputs or {})
        try:
            validate_value(input_values, workflow.inputs_schema, path="inputs")
        except ValueError as exc:
            raise ValueError(f"workflow inputs invalid: {exc}") from exc
        state = self.engine.start(workflow)
        now = int(time.time())
        run = WorkflowRun(
            run_id=secrets.token_hex(12),
            workflow_id=workflow.id,
            workflow_version=workflow.version,
            workflow_scope=workflow.scope.value,
            workflow_snapshot=workflow.to_dict(),
            workspace=str(self.workspace),
            status="running",
            engine_state=state,
            inputs=input_values,
            created_at=now,
            updated_at=now,
        )
        self.store.save(run)
        self.store.prune(max_runs=100)
        return self._drive(run, context=context)

    def get(self, run_id: str) -> WorkflowRun | None:
        run = self.store.get(run_id)
        return self._ensure_approval_request(run) if run is not None else None

    def list(self) -> tuple[WorkflowRun, ...]:
        return tuple(self._ensure_approval_request(run) for run in self.store.list())

    def continue_pending(
        self,
        run_id: str,
        *,
        node_id: str = "",
        outcome: str = "",
        output: Any = None,
        context: RequestContext | None = None,
    ) -> WorkflowRun:
        run = self.get(run_id)
        if run is None:
            raise KeyError(run_id)
        if run.status == "waiting_model":
            if not node_id or outcome not in {"success", "failure"}:
                raise ValueError(
                    "waiting_model requires node_id and outcome=success|failure"
                )
            return self.continue_model(
                run_id,
                node_id=node_id,
                outcome=outcome,
                output=output,
                context=context,
            )
        if run.status == "waiting_approval":
            if node_id or outcome or output is not None:
                raise ValueError(
                    "waiting_approval does not accept node_id/outcome/output; "
                    "the decision must come from the Desktop signed broker"
                )
            return self._consume_approval_response(run, context=context)
        raise ValueError(f"workflow run cannot continue from status: {run.status}")

    def _consume_approval_response(
        self,
        run: WorkflowRun,
        *,
        context: RequestContext | None,
    ) -> WorkflowRun:
        broker = self.approval_broker
        if broker is None:
            raise ValueError("Desktop Workflow Approval Broker is unavailable")
        approval = self._pending_approval(run)
        if approval is None:
            raise ValueError("workflow run has no pending ApprovalRequest")
        run = self._ensure_approval_request(run)
        request_id = str((run.pending_action or {}).get("request_id") or "")
        if not request_id:
            raise ValueError("Workflow approval request could not be published")
        decision = broker.consume_response(
            request_id,
            run_id=run.run_id,
            node_id=approval.node_id,
            approval_id=approval.approval_id,
        )
        if decision is None:
            raise ValueError("Workflow approval decision is still pending")
        return self.resolve_approval(
            run.run_id,
            approved=decision,
            context=context,
        )

    def continue_model(
        self,
        run_id: str,
        *,
        node_id: str,
        outcome: str,
        output: Any,
        context: RequestContext | None = None,
    ) -> WorkflowRun:
        if outcome not in {"success", "failure"}:
            raise ValueError("workflow model outcome must be success or failure")
        run = self.store.get(run_id)
        if run is None:
            raise KeyError(run_id)
        if run.status != "waiting_model" or not run.pending_action:
            raise ValueError("workflow run is not waiting for a model result")
        pending_node = str(run.pending_action.get("node_id") or "")
        if pending_node != node_id:
            raise ValueError("workflow continue node_id does not match pending action")

        workflow = self._workflow(run)
        node = self._node(workflow, node_id)
        checkpoint = run.engine_state
        next_state = self.engine.complete(
            workflow,
            run.engine_state,
            node_id,
            outcome=outcome,
            output=output,
        )
        run = self._save(
            run,
            status="running",
            engine_state=next_state,
            pending_action=None,
            retry_state=(checkpoint if outcome == "failure" else None),
            error="",
            node_states=self._node_states(
                run,
                node_id,
                status="failed" if outcome == "failure" else "succeeded",
                outcome=outcome,
                updated_at=int(time.time()),
            ),
        )
        if (
            outcome == "failure"
            and node.policy.on_error == "stop"
            and not self._has_edge(workflow, node_id, "failure")
        ):
            return self._fail(
                run,
                node_id=node_id,
                message=f"Workflow model node failed: {node_id}",
                retry_state=checkpoint,
            )
        return self._drive(run, context=context)

    def resolve_approval(
        self,
        run_id: str,
        *,
        approved: bool,
        context: RequestContext | None = None,
    ) -> WorkflowRun:
        run = self.store.get(run_id)
        if run is None:
            raise KeyError(run_id)
        if run.status != "waiting_approval" or not run.pending_action:
            raise ValueError("workflow run is not waiting for approval")
        node_id = str(run.pending_action.get("node_id") or "")
        approval_id = str(run.pending_action.get("approval_id") or "")
        workflow = self._workflow(run)
        outcome = "approved" if approved else "rejected"
        next_state = self.engine.complete(
            workflow,
            run.engine_state,
            node_id,
            outcome=outcome,
            output={"approved": approved},
        )
        approvals = tuple(
            replace(
                item,
                status=outcome,
                resolved_at=int(time.time()),
            )
            if item.approval_id == approval_id
            else item
            for item in run.approvals
        )
        run = self._save(
            run,
            status="running",
            engine_state=next_state,
            pending_action=None,
            approvals=approvals,
            node_states=self._node_states(
                run,
                node_id,
                status=outcome,
                outcome=outcome,
                updated_at=int(time.time()),
            ),
        )
        if not approved and not self._has_edge(workflow, node_id, "rejected"):
            return self._save(run, status="cancelled")
        return self._drive(run, context=context)

    def cancel(self, run_id: str) -> WorkflowRun:
        run = self.store.get(run_id)
        if run is None:
            raise KeyError(run_id)
        if run.status in {"succeeded", "cancelled"}:
            return run
        return self._save(run, status="cancelled", pending_action=None)

    def retry(
        self,
        run_id: str,
        *,
        context: RequestContext | None = None,
    ) -> WorkflowRun:
        run = self.store.get(run_id)
        if run is None:
            raise KeyError(run_id)
        if run.status != "failed" or run.retry_state is None:
            raise ValueError("workflow run has no retry checkpoint")
        retry_nodes = set(run.retry_state.ready)
        states = {
            key: dict(value)
            for key, value in run.node_states.items()
            if key not in retry_nodes
        }
        run = self._save(
            run,
            status="running",
            engine_state=run.retry_state,
            retry_state=None,
            pending_action=None,
            node_states=states,
            error="",
        )
        return self._drive(run, context=context)

    def _wait_for_model(
        self,
        run: WorkflowRun,
        workflow: WorkflowDefinition,
        node: WorkflowNode,
    ) -> WorkflowRun:
        state = run.engine_state
        try:
            action = self.engine.model_action(
                workflow,
                state,
                node.id,
                inputs=run.inputs,
            )
        except Exception as exc:
            return self._fail(
                run,
                node_id=node.id,
                message=str(exc),
                retry_state=state,
            )
        return self._save(
            run,
            status="waiting_model",
            pending_action=action.to_dict(),
            retry_state=state,
            node_states=self._node_states(
                run,
                node.id,
                status="waiting_model",
                updated_at=int(time.time()),
            ),
        )

    def _wait_for_approval(
        self,
        run: WorkflowRun,
        node: WorkflowNode,
    ) -> WorkflowRun:
        now = int(time.time())
        approval = ApprovalRequest(
            approval_id=secrets.token_hex(8),
            run_id=run.run_id,
            node_id=node.id,
            title=str(node.config.get("title") or node.name),
            description=str(node.config.get("description") or ""),
            status="pending",
            requested_at=now,
        )
        request_id = ""
        if self.approval_broker is not None:
            try:
                request_id = self._publish_approval_request(run, approval)
            except OSError:
                request_id = ""
        return self._save(
            run,
            status="waiting_approval",
            approvals=(*run.approvals, approval),
            pending_action={
                "type": "approval",
                "approval_id": approval.approval_id,
                "node_id": node.id,
                "title": approval.title,
                "description": approval.description,
                "request_id": request_id,
            },
            node_states=self._node_states(
                run,
                node.id,
                status="waiting_approval",
                updated_at=now,
            ),
        )

    def _execute_artifact_node(
        self,
        run: WorkflowRun,
        workflow: WorkflowDefinition,
        node: WorkflowNode,
    ) -> WorkflowRun:
        state = run.engine_state
        source_node_id = str(node.config.get("source_node_id") or "")
        artifact_id = str(node.config.get("artifact_id") or "")
        artifact_format = str(node.config.get("format") or "json")
        if source_node_id not in state.outputs:
            return self._fail(
                run,
                node_id=node.id,
                message=f"Artifact source output is not available: {source_node_id}",
                retry_state=state,
            )
        try:
            reference = self.artifact_store.write(
                run_id=run.run_id,
                artifact_id=artifact_id,
                producer_node_id=node.id,
                value=state.outputs[source_node_id],
                format=artifact_format,
            )
            next_state = self.engine.complete(
                workflow,
                state,
                node.id,
                outcome="success",
                output=reference.to_dict(),
            )
        except Exception as exc:
            return self._fail(
                run,
                node_id=node.id,
                message=str(exc),
                retry_state=state,
            )
        return self._save(
            run,
            status="running",
            engine_state=next_state,
            artifacts=(*run.artifacts, reference),
            node_states=self._node_states(
                run,
                node.id,
                status="succeeded",
                outcome="success",
                updated_at=int(time.time()),
            ),
        )

    def _execute_local_node(
        self,
        run: WorkflowRun,
        workflow: WorkflowDefinition,
        node: WorkflowNode,
        *,
        context: RequestContext | None,
    ) -> WorkflowRun:
        checkpoint = run.engine_state
        try:
            result = self.engine.execute_local(
                workflow,
                checkpoint,
                node.id,
                context=context,
                values={"inputs": run.inputs},
            )
        except Exception as exc:
            return self._fail(
                run,
                node_id=node.id,
                message=str(exc),
                retry_state=checkpoint,
            )

        run = self._save(
            run,
            status="running",
            engine_state=result.state,
            retry_state=(checkpoint if result.outcome == "failure" else None),
            node_states=self._node_states(
                run,
                node.id,
                status="failed" if result.outcome == "failure" else "succeeded",
                outcome=result.outcome,
                updated_at=int(time.time()),
            ),
        )
        if (
            result.outcome == "failure"
            and node.policy.on_error == "stop"
            and not self._has_edge(workflow, node.id, "failure")
        ):
            return self._fail(
                run,
                node_id=node.id,
                message=f"Workflow tool node failed: {node.id}",
                retry_state=checkpoint,
            )
        return run

    def _drive(
        self,
        run: WorkflowRun,
        *,
        context: RequestContext | None,
    ) -> WorkflowRun:
        workflow = self._workflow(run)
        while True:
            state = run.engine_state
            if state.done:
                return self._save(
                    run,
                    status="succeeded",
                    pending_action=None,
                    retry_state=None,
                    error="",
                )
            if not state.ready:
                return self._fail(
                    run,
                    node_id="",
                    message="Workflow has no ready node but is not complete",
                    retry_state=None,
                )

            node_id = state.ready[0]
            node = self._node(workflow, node_id)

            if node.type in {"prompt", "skill"}:
                return self._wait_for_model(run, workflow, node)

            if node.type == "approval":
                return self._wait_for_approval(run, node)

            if node.type == "artifact":
                run = self._execute_artifact_node(run, workflow, node)
                if run.status != "running":
                    return run
                continue

            run = self._execute_local_node(run, workflow, node, context=context)
            if run.status != "running":
                return run

