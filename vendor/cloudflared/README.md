# Bundled cloudflared

桌面安装包会把 Cloudflare 官方发布的 `cloudflared` 二进制放在这里。

目录约定：

```text
vendor/cloudflared/
├── darwin-arm64/cloudflared
├── darwin-amd64/cloudflared
├── windows-amd64/cloudflared.exe
├── windows-arm64/cloudflared.exe
├── linux-amd64/cloudflared
└── linux-arm64/cloudflared
```

开发环境允许回退到系统 `PATH` 中的 `cloudflared`；PyInstaller 打包后的正式桌面应用不会回退，确保最终用户无需自行安装 Cloudflare Tunnel。

构建发布包前运行：

```bash
python scripts/fetch_cloudflared.py
```

该脚本只下载当前构建平台对应的官方 Release 资产。
