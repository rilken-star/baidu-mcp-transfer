# 安装后使用

本机控制程序需要 Python 3.9+、OpenSSH；源服务器需要 Linux、Python 3.10+（含 venv）、可访问 PyPI 和百度 API。macOS 使用系统隐藏框输入百度令牌；Linux/Windows WSL 使用交互终端隐藏输入。本包不依赖百度桌面客户端，也不修改 Codex 的全局 MCP 配置。

官方实现固定为 `baidu-netdisk/mcp` 提交 `b3983d330fea79c7b72e6b7014803e1830148d2c`，单文件大于 4 MiB 时分片。它不是百度平台新发行的官方安装包；本包是围绕该官方实现提供的可迁移 Codex 技能及控制脚本。

## 1. SSH 连接

已配置密钥且已信任主机时，无需另建连接。密码登录可由用户在终端建立复用连接（输入 SSH 密码时不回显）：

```bash
ssh -M -N -f -p 22 -o ControlPath=/tmp/baidu-transfer-ssh -o ControlPersist=8h user@server.example
```

第一次连接需用户核实主机指纹；不要跳过验证。也可复用目标机器上已有的隐藏 SSH 密码输入技能，但本包不依赖它。下面配置中的 `--control-path` 只在使用该复用连接时添加。工具不会读取 SSH 密码、百度客户端配置或浏览器凭据。

## 2. 建立独立任务并部署

以下例子需按实际服务器、目录和授权修改。`$SKILL` 指安装后的技能目录，`$JOB` 指 init 输出的 config.json 路径：

```bash
SKILL="${CODEX_HOME:-$HOME/.codex}/skills/baidu-mcp-transfer"
python3 "$SKILL/scripts/transfer.py" init \
  --workspace /path/to/workspace \
  --host server.example --user user --port 22 \
  --source /srv/ComfyUI --target /backups/ComfyUI \
  --remote-base /home/user/project \
  --control-path /tmp/baidu-transfer-ssh \
  --exclude-root input --exclude-root output --exclude-root user \
  --min-bytes 1048576
```

上例跳过 input/output/user 和小于 1 MiB 的文件，仅是示例。若用户只想传模型，可把源目录设为模型目录。可重复 `--exclude-glob 'models/*wan2.2*'`（大小写不敏感，对整个相对路径做 fnmatch）；另两种命名需分别配置模式。`--min-bytes` 单位是字节，不默认替用户定义“小文件”。要覆盖目标同名文件必须取得授权后添加 `--overwrite`。有效文件软链接需显式添加 `--follow-file-links`；目录软链接不会跟随。

网盘 `/backups` 须已存在；运行只会创建授权目标 `/backups/ComfyUI` 及其子目录。

```bash
JOB=/path/printed/by/init/config.json
python3 "$SKILL/scripts/transfer.py" deploy --config "$JOB"
python3 "$SKILL/scripts/transfer.py" setup --config "$JOB"
python3 "$SKILL/scripts/transfer.py" plan --config "$JOB"
```

部署只传包内程序和无凭据配置。源文件实体不经 SSH、本机或本地网络。远端程序位于 `remote-base/code/<job>`，清单、进度及证明位于 `remote-base/codex/<job>`。部署路径必须全新，防止覆盖其他任务；断点恢复时直接复用已有部署，不要重新 deploy。

## 3. 启动、核验和停止

```bash
python3 "$SKILL/scripts/transfer.py" start --config "$JOB"
```

只有 start/verify 显式请求一次隐藏令牌输入。不要把 AccessToken 发到聊天或写在终端参数里。令牌必须有相应网盘权限；本包不代建百度应用或反复触发 OAuth 页面。认证失效或失败时停止，用户处理后才重新运行。此手动输入流程的令牌仅在进程中使用，不额外写磁盘；控制器与 SDK 子进程退出后不再保留活动授权进程。百度服务端的令牌撤销与本机忘记令牌不同，如需服务端撤销应由用户在授权管理中操作。

另一终端或 Codex 可随时执行（均不弹框）：

```bash
python3 "$SKILL/scripts/transfer.py" status --config "$JOB"
python3 "$SKILL/scripts/transfer.py" stop --config "$JOB"
```

start 不自动后台化；Codex 可使用长运行终端保持它运行。Ctrl+C 会提交独立 stop 控制请求。若 SSH 已断，停止命令可能无法到达服务器：必须明确报告未确认停止，重连后先检查状态并停止，禁止另起控制器。源进程收到 SIGHUP 会停止；某些 SSH 服务断线行为不同，不据此承诺已经停止。

`status.running` 判断 Linux 进程启动标识，避免 PID 复用。`progress.verified_files/bytes` 是当前有效范围的已核验量，`effective_files/bytes` 是当前分母；`pending` 是未完成或未知项。`parts.json` 仅反映最新文件分片阶段，不能当成已完成容量或单独当作网络速率。小文件处理包含 API 往返与元数据核验，按核验量计算的完成速度不等于链路带宽。

## 4. 恢复与结果未知

重新运行同一 config 的 start 会扫描源元数据，先检查每个目标，严格相符的文件不再传。之前已写 attempting/unknown 的文件，目标不符则继续隔离，**不会重传**。代码不提供一键清空未知记录；若用户授权重传某条目，代理先归档完整原记录，核实原源签名和远端任务状态，再针对该项进行审阅后的状态更改，不能把其他 unknown 一并改成待传。

```bash
python3 "$SKILL/scripts/transfer.py" verify --config "$JOB"
```

verify 只用源元数据和目标 OpenAPI 列表，不上传文件或创建目录。目标不存在的父目录可能返回 metadata error，此时报告未核验，不视为完成。完整目录分页、路径、文件名、整数 isdir=0 和整数大小全部相符才能复用证明。没有校验内容散列，不能声称做到逐字节内容一致。

源大小/mtime 相对已尝试记录发生变化时标为 source_changed，需先确认应备份的版本，保留旧证明。不要静默复用旧大小、更改用户源文件或重交未知任务。任务结束 phase=needs_attention 表示仍有 pending 或坏链接等问题；只有 phase=complete 且分母范围符合用户要求才称该范围全部完成。

更换 Codex 电脑：技能重新安装；安全复制无凭据 config.json 并建立新 SSH 连接。继续指向原 remote_code 和 remote_state，先 status，活动控制器无需再次 start。实际证明和文件级接续状态保存在源服务器，不需要把文件实体带到新电脑。不要导入另一备份任务的账本或特殊重传许可。

## 离线测试与来源

安装后可执行 `python3 scripts/test_transfer.py`，只使用临时文件与伪造 API，测试路径边界、分页、未知结果隔离、源变化、严格核验和停止行为。真实 stdio MCP 握手测试需安装 requirements，但不连接百度；通过 `--mcp-python /path/to/python3.10` 运行。包内官方 vendor 保留 MIT LICENSE 和原文件 SHA-256；控制脚本与独立安装器的完整性由包外 MANIFEST.json 校验。


## 服务器文件授权与自动续期

1.2.0 支持使用受保护的服务器凭据文件进行按需续期；参阅 [自动续期](auto-refresh.md)。这与上述手动输入、只在进程中使用令牌的模式不同。不要把凭据放入任务 config.json 或通过 GitHub 发布。SSH 部署会携带续期模块；凭据仍留在目标服务器，不从另一台机器复制或推定可用。
