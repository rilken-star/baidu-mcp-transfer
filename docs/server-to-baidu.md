# 服务器 → 百度网盘

此方向使用 Python MCP 上传程序。源服务器通过百度 API 直接上传，本机只传程序、配置和控制指令。也可在源服务器安装 `baidu-tui`，查看兼容任务的上传和下载进度；安装和分片设置见 [终端界面安装说明](../INSTALL.md)。

## 安装与 SSH

本机 Python 3.9+ 和 OpenSSH；源服务器 Linux、Python 3.12+（含 venv），能访问 PyPI 与百度 API。

在仓库根目录执行：

```bash
python3 install.py
```

如果使用密码登录，在自己的终端建立连接：

```bash
ssh -fMN -p 22 -S /tmp/baidu-transfer-ssh \
  -o ControlPersist=yes -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 user@server.example
```

首次主机指纹由用户确认；不要禁用主机密钥检查。

## 创建与部署任务

```bash
SKILL="${CODEX_HOME:-$HOME/.codex}/skills/baidu-mcp-transfer"
python3 "$SKILL/scripts/transfer.py" init \
  --workspace /path/to/workspace \
  --host server.example --user user --port 22 \
  --source /srv/ComfyUI/models/loras \
  --target /backups/loras \
  --remote-base /home/user/baidu-transfer \
  --control-path /tmp/baidu-transfer-ssh
```

`--source` 是服务器路径，`--target` 是百度网盘路径。网盘父目录 `/backups` 必须存在。把 init 输出的路径设为 `JOB`，不要把示例占位路径照抄为实际任务路径。

```bash
JOB=/path/printed/by/init/config.json
python3 "$SKILL/scripts/transfer.py" deploy --config "$JOB"
python3 "$SKILL/scripts/transfer.py" setup --config "$JOB"
python3 "$SKILL/scripts/transfer.py" plan --config "$JOB"
python3 "$SKILL/scripts/transfer.py" start --config "$JOB"
```

start 时隐藏输入 Access Token。默认不覆盖；必须明确授权覆盖时才添加 init 参数 `--overwrite`。完整复制不要添加最小文件大小限制或排除项。

需要排除时，init 可重复添加 `--exclude-root 目录名` 或 `--exclude-glob '*.png'`。glob 大小写不敏感，作用于源目录的相对路径；精确行为见原始操作文档。

## 状态、停止与恢复

```bash
python3 "$SKILL/scripts/transfer.py" status --config "$JOB"
python3 "$SKILL/scripts/transfer.py" verify --config "$JOB"
python3 "$SKILL/scripts/transfer.py" stop --config "$JOB"
```

start 不自动脱离终端；需要持续运行时由用户在持久终端会话中运行。SSH 断线后先 status，确认旧控制器状态再决定是否重新 start。更换 SSH 地址时保留原远端任务和账本，先核对配置中的 host/port，再检查状态。

恢复复用原配置，不重新 deploy。目标已核验一致的文件会跳过；结果未知、源变化等条目需要人工核对，不自动覆盖或清空状态。本节所述 SSH 工作流按文件核验并恢复；TUI 可记录分片进度，但结果未知的上传不会自动重传。

只有 `phase=complete` 且目标范围正确，才能认定任务完成；核验是目录元数据、文件名和大小核对，未进行内容哈希校验。

详见 [原始完整操作说明](../baidu-mcp-transfer/references/operations.md)。

