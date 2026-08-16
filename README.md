## 使用方式
[查看文档](https://blog.micromatrix.org/archives/F8crFkU6)

## 桌面版

项目现已拆分为可复用核心、CLI 和 PySide6 桌面端。桌面版会优先使用随应用一起打包的 `cloudflared`，最终用户无需自行安装 Cloudflare Tunnel。

### 开发环境启动

安装桌面依赖：

```bash
pip install -r requirements-desktop.txt
```

启动桌面程序：

```bash
python desktop.py
```

CLI 入口保持兼容：

```bash
python start.py /path/to/workspace
```

### cloudflared

正式桌面安装包必须包含当前平台对应的 `cloudflared`。构建前执行：

```bash
python scripts/fetch_cloudflared.py
```

二进制会被下载到：

```text
vendor/cloudflared/<platform>/cloudflared
```

开发环境如果该文件不存在，会回退到系统 `PATH` 中的 `cloudflared`；打包后的桌面程序不会回退到系统环境。

### 构建桌面应用

```bash
python build_desktop.py
```

macOS 会生成 `Coding Tools MCP.app`。Windows/Linux 需要在对应系统上分别构建。

### URL 模式

- 配置 `CODING_TOOLS_MCP_SERVER_URL`：OAuth 和 Public MCP URL 使用固定地址。
- 未配置：自动使用 Cloudflare Quick Tunnel 随机地址。

桌面端同样支持这两种模式；`Public URL` 留空即可使用 Quick Tunnel。

## 协议核心要点

**AGPL-3.0 (GNU Affero General Public License)** 是 GPL-3.0 的扩展版，主要针对**网络服务**场景：

### 关键条款（第13条）

> **13. Remote Network Interaction; Use with the GNU General Public License.**

> You may convey a covered work via a network, only if you ensure that anyone who receives the program in object code form receives also all the corresponding source code, to the extent that it is required to exercise the freedoms granted in the license. This applies in particular to the remote use of a network server to provide the functionality of the program to third parties under the control of them.

**翻译为：**  
如果你通过网络提供本程序的功能（无论是否收费），那么**任何使用你提供的服务的用户**都必须能够获得**与本程序相同的源代码**。

---

## 本项目协议应用

本项目采用 **AGPL-3.0** 协议，适用场景如下：

| 场景 | 是否适用 AGPL |
|------|---------------|
| 内部使用 | 不适用 |
| 内部 SaaS 服务 | **适用**（必须开源） |
| 二次开发提供服务 | **适用**（必须开源） |
| 作为库被引用 | 不适用 |
| 作为工具被用户下载 | **适用**（必须开源） |

---

## 协议核心义务

当你以以下方式使用本项目时，必须：

1. **提供源代码**：任何访问你服务的用户必须能下载源代码
2. **保留版权声明**：必须保留 AGPL 版权声明
3. **提供修改源**：允许用户修改并重新分发
4. **不限制用户使用**：不限制用户使用、研究、修改

---

## 适用场景总结

| 情况 | 说明 | 风险 |
|------|------|------|
| 内部开发 | 直接使用 | 无 |
| 内部 SaaS | 通过本项目提供服务 | **必须开源** |
| 二次开发 SaaS | 基于本项目开发服务 | **必须开源** |
| 作为工具分发 | 用户下载使用 | **必须开源** |
| 作为库使用 | 不提供服务 | 无 |

---

## 建议

如果你的项目是**订阅制 SaaS 服务**，建议：

1. **继续使用 AGPL-3.0**（符合要求）
2. **公开源代码**（至少提供代码仓库链接）
3. **标注使用 AGPL**（在 README 中明确说明）

---

**协议已切换为 AGPL-3.0**

- `LICENSE` 文件已更新为 AGPL-3.0 内容
- README.md 已同步更新协议说明

需要我再补充什么内容吗？（如添加安装步骤、配置示例、额度管理接口说明等）