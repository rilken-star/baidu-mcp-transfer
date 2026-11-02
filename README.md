# 百度 MCP 与服务器文件直传

当前上传程序版本为 **1.3.0**，源码已同步更新。[下载安装包与校验文件](releases/v1.3.0/) · [模型上传策略](baidu-mcp-transfer/references/model-upload-policy.md)

保存可迁移的程序和操作说明，覆盖三条传输通道：

| 方向 | 实现 | 文档 |
| --- | --- | --- |
| 百度网盘 → Linux 服务器 | 社区 Node.js MCP + 受限目录下载控制器 | [下载操作](docs/baidu-to-server.md) |
| Linux 服务器 → 百度网盘 | Python MCP + baidu-tui 安装包 | [上传操作](docs/server-to-baidu.md) |
| 服务器 A → 服务器 B | SSH + rsync，文件直接在两台服务器之间传输 | [服务器互传](docs/server-to-server.md) |

本机负责控制和授权，网盘直传的文件实体不经过本机。服务器互传使用 rsync，不经过百度 MCP。

## 目录

- `baidu-mcp-transfer/`、`install.py`、`INSTALL.md`、`MANIFEST.json`：1.3.0 安装包，包含模型同步策略、自动续期、安全重试、终端上传/下载界面、安装器及校验清单。
- `community-download/`：从实际使用的下载控制器整理出的可配置版本，依赖外部社区 MCP 项目。
- `docs/`：三种方向的安装、启动、状态、恢复与验证说明。

## 终端界面

运行 `python3 install.py` 安装后，按 [安装说明](INSTALL.md) 配置 Python 环境并启动 `baidu-tui`。界面可显示兼容进度记录的上传与下载任务；第三方 MCP 不提供进度时无法显示实际速度。上传默认仅模型权重，排除 TXT、图片、JSON 等辅助文件；目标路径忽略大小写，同名同大小跳过，冲突保留且不覆盖。4/8 MiB 分片按账号信息与文件大小选择，超过 2048 分片仍保护停止。同一部分上传会话可续传，已确认分片不重传；最终提交未知时不盲目重交。

```bash
baidu-tui --sync-models /服务器模型目录 --to /网盘模型目录 --follow-file-links
baidu-tui --resume-models /任务目录
```

文件链接须明确允许，目录链接不跟随。普通文件若确需上传，显式使用 `--upload ... --all-files`。已上传的辅助文件不删除。

## 授权与凭据

百度桌面客户端登录不等于 OpenAPI Access Token 授权。需要具备相应网盘权限的 Access Token。通过隐藏输入或进程标准输入传递，或由用户显式选择受保护的服务器凭据文件（目录700、文件600）。访问令牌、刷新令牌及应用密钥不得写入任务配置、源码、安装包、日志或聊天。`baidu-tui --authorize` 可配置授权；到期前1小时按需续期，失败或结果不确定时不反复刷新。

SSH 必须先建立可信连接，保留主机密钥验证。密码在自己的终端输入，禁止在命令中明文填写。所有示例地址是占位符，使用前替换。

## 验证范围

- 百度 → 服务器：本次实际部署版本已完成目录递归下载，并逐项核对文件数量和大小。这里的可配置版本需要按下载文档先完成离线检查和小样本验证；不声称其已完成新的真实网盘传输。
- 服务器 → 百度：1.3.0 提供隔离回归测试与 MCP 握手验证，覆盖默认模型过滤、大小写去重、未知结果隔离、同会话续传、连接复用及安全重试。指定服务器已有真实模型传输与分片续传证据，整体同步尚未完成；不把测试通过等同于全部模型已上传。新服务器和账号仍需先核验授权和分片能力。
- 服务器 → 服务器：提供 rsync 标准操作流程；本次发布没有执行新的服务器互传。

数量和大小相符不等于内容散列一致。只有另行完成哈希校验，才能声称内容一致。

## 来源

原始上传包的依赖来源及许可证见 [vendor/UPSTREAM.json](baidu-mcp-transfer/vendor/UPSTREAM.json) 和 [vendor/LICENSE](baidu-mcp-transfer/vendor/LICENSE)。

社区下载依赖：[capwitf/baidu-netdisk-knowledge-mcp](https://github.com/capwitf/baidu-netdisk-knowledge-mcp)，固定提交 `ed7359624f82b77bb80e28839eb11e807518f23c`。保留其上游身份，不将本仓库描述为百度官方发行包。


