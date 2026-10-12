---
name: baidu-mcp-transfer
description: Copy files directly from an SSH-accessible Linux source server to Baidu Netdisk through the official stdio MCP uploader, with file-level resume and exact destination metadata verification. Use for server-to-Baidu transfers that must avoid routing file bodies through the Codex computer.
---

# 百度 MCP 服务器直传

源服务器执行随包提供的官方 MCP 分片上传器；Codex 本机仅部署程序、传递控制请求和读取元数据。不要把源文件下载到本机再上传。

先阅读 [运行说明](references/operations.md)，使用本技能目录的 `scripts/transfer.py`。它与全局 MCP 配置相互独立，安装或检查状态不会启动上传或授权弹窗。

## 工作流程

1. 根据用户要求确定 SSH 主机、端口、源目录、网盘目标、排除范围、最小文件大小、是否跟随文件软链接和是否允许覆盖。沿用明确的授权；缺少有影响的信息时询问。不要沿用另一任务的路径、例外许可或令牌。
2. 建立用户信任的 SSH 密钥或 ControlMaster 连接。工具使用 `BatchMode=yes` 和严格主机密钥检查；认证失败时报告一次，不重复弹窗。
3. `init` 只生成无凭据配置。`deploy` 仅部署本包程序；`setup` 在源服务器安装 Python 依赖。`plan` 只检查源元数据，核对有效范围后再 `start`。
4. `start` 通过隐藏输入框（macOS）或隐藏终端输入获取 AccessToken，经加密 SSH stdin 传到源进程；不把令牌放进聊天、命令行、配置、日志或包内。检查状态、停止都不需要令牌。
5. 用 `status` 检查进度。只有目标完整路径、名称、`isdir` 为整数 0、`size` 为精确整数且全部相符的记录才算完成；SDK success、分片响应或请求已提交都不算完成。
6. 同一源/目标只允许一个控制器。文件上传前落盘记录 attempting；连接失败、中断、超时后仅核验原结果，无法证明完成则隔离 unknown，不自动重传。新的重传须获得用户对具体条目的授权并人工保留审计记录。
7. 用户要求停止时立即执行 `stop`，确认控制器及对应 SDK 子进程停止，暂停为该任务创建的定时任务。保留云盘文件和证明；不要自动接续。没有明确周期汇报要求时，不创建自动化。

## 范围与限制

- 默认不排除任何普通文件；按用户要求设置 `--exclude-root`、`--exclude-glob` 或 `--min-bytes`。不要把过去的 input/output/user/Wan2.2 排除规则强加给新任务。
- 源文件只读，目录软链接不递归；有效文件软链接只有显式允许才上传。大小/mtime 改变的条目暂停，不能静默覆盖旧证明。
- 这是**文件级接续**。已严格核验的文件可复用；不支持进程重启后的分片级续传。授权只在活动进程内存中；退出后需重新输入，不能承诺跨重启保留密码。
- 网盘目标的父目录需已存在。覆盖默认关闭；`--overwrite` 仅在用户授权时使用。不会删除云盘或源文件。
- 不读或分析文件内容，只让源服务器官方上传器读取实体并按协议计算 MD5。不要展示源图片、视频或把文件送给大模型。
- 默认 `start` 为 SSH 前台长进程。关机/断网可能打断控制连接；不要承诺本机关闭后任务必定继续。先停止核实，恢复时用同一配置和同一远端状态，未知结果不重复提交。
- 官方原始代码位于 `vendor/`，固定来源见 `vendor/UPSTREAM.json`；任务控制与守卫在 `scripts/`，不安装故障的旧 URL/XGC 流程或原生客户端点击脚本。

运行前可用 `python3 scripts/transfer.py --help`。安装与离线测试见包外 `INSTALL.md`；技能自身的操作说明包含完整示例。
