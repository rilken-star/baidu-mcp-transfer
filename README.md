# 百度 MCP 与服务器文件直传

保存可迁移的程序和操作说明，覆盖三条传输通道：

| 方向 | 实现 | 文档 |
| --- | --- | --- |
| 百度网盘 → Linux 服务器 | 社区 Node.js MCP + 受限目录下载控制器 | [下载操作](docs/baidu-to-server.md) |
| Linux 服务器 → 百度网盘 | Python MCP + baidu-tui 安装包 | [上传操作](docs/server-to-baidu.md) |
| 服务器 A → 服务器 B | SSH + rsync，文件直接在两台服务器之间传输 | [服务器互传](docs/server-to-server.md) |

本机负责控制和授权，网盘直传的文件实体不经过本机。服务器互传使用 rsync，不经过百度 MCP。

## 目录

- `baidu-mcp-transfer/`、`install.py`、`INSTALL.md`、`MANIFEST.json`：1.1.0 安装包，包含终端上传/下载界面、安装器及校验清单。
- `community-download/`：从实际使用的下载控制器整理出的可配置版本，依赖外部社区 MCP 项目。
- `docs/`：三种方向的安装、启动、状态、恢复与验证说明。

## 终端界面

运行 `python3 install.py` 安装后，按 [安装说明](INSTALL.md) 配置 Python 环境并启动 `baidu-tui`。界面可显示兼容进度记录的上传与下载任务；第三方 MCP 不提供进度时无法显示实际速度。上传默认采用 4 MiB 分片，可根据账号能力调整，超过 2048 个分片时会提前提示。

## 授权与凭据

百度桌面客户端登录不等于 OpenAPI Access Token 授权。需要具备相应网盘权限的 Access Token。通过隐藏输入或进程标准输入传递，不写进命令参数、仓库、配置、日志或聊天。

SSH 必须先建立可信连接，保留主机密钥验证。密码在自己的终端输入，禁止在命令中明文填写。所有示例地址是占位符，使用前替换。

## 验证范围

- 百度 → 服务器：本次实际部署版本已完成目录递归下载，并逐项核对文件数量和大小。这里的可配置版本需要按下载文档先完成离线检查和小样本验证；不声称其已完成新的真实网盘传输。
- 服务器 → 百度：1.1.0 在 Linux 隔离环境通过离线测试与 MCP 握手；曾使用小测试文件实际上传和下载，SHA-256 一致。新服务器和账号仍需自行进行小样本验证。
- 服务器 → 服务器：提供 rsync 标准操作流程；本次发布没有执行新的服务器互传。

数量和大小相符不等于内容散列一致。只有另行完成哈希校验，才能声称内容一致。

## 来源

原始上传包的依赖来源及许可证见 [vendor/UPSTREAM.json](baidu-mcp-transfer/vendor/UPSTREAM.json) 和 [vendor/LICENSE](baidu-mcp-transfer/vendor/LICENSE)。

社区下载依赖：[capwitf/baidu-netdisk-knowledge-mcp](https://github.com/capwitf/baidu-netdisk-knowledge-mcp)，固定提交 `ed7359624f82b77bb80e28839eb11e807518f23c`。保留其上游身份，不将本仓库描述为百度官方发行包。

