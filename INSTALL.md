# 百度 MCP 服务器直传与 baidu-tui 安装包 1.1.0

此包适用于 Linux 服务器（Python 3.10+，含 venv、ensurepip 和 curses）。它包含原有的 Codex 技能、官方 MCP 上传实现、终端上传/下载界面和脱敏状态读取。安装不会登录百度、启动传输或生成凭据文件。

## 安装

将 ZIP 上传到需要传输文件的服务器，解压后在该目录执行：

```bash
python3 install.py
```

默认安装到 `${CODEX_HOME:-~/.codex}/skills/baidu-mcp-transfer`，并在 `~/.local/bin/baidu-tui` 创建启动器。若 `~/.local/bin` 不在 PATH，可用其完整路径运行。已有同名技能或启动器时安装器会停止，不覆盖原文件。

可自定义技能目录和启动器目录：

```bash
python3 install.py --dest /path/to/skills --bin-dir /path/to/bin
```

只安装技能、不创建启动器：`python3 install.py --no-cli`。

安装器离线校验包内全部技能文件的 SHA-256；上传功能还需要单独安装 Python 依赖。以下命令在安装后的技能目录创建独立虚拟环境（安装依赖需要网络）：

```bash
SKILL_DIR="${CODEX_HOME:-$HOME/.codex}/skills/baidu-mcp-transfer"
python3 -m venv "$SKILL_DIR/.venv"
"$SKILL_DIR/.venv/bin/python" -m pip install -r "$SKILL_DIR/requirements.txt"
```

如果系统提示缺少 venv/ensurepip，请先安装与系统 Python 版本对应的 python3-venv 软件包，或选用已包含 venv 的 Python。

如果使用了 `--dest`，将 `SKILL_DIR` 改为对应的安装目录。安装完成后可先运行 `baidu-tui --status`；交互界面需要 UTF-8 终端，建议至少 82 列 × 24 行。

## 授权和使用

上传或下载前，在服务器的 Bash 终端隐藏输入有相应权限的百度 Access Token，按 Enter 后启动界面：

```bash
read -r -s -p '百度 Access Token: ' BAIDU_NETDISK_ACCESS_TOKEN
printf '\n'
export BAIDU_NETDISK_ACCESS_TOKEN
baidu-tui
unset BAIDU_NETDISK_ACCESS_TOKEN
```

令牌不写入安装包、任务配置、日志或文件；它会存在当前终端及后台传输进程的环境/内存中，后台任务结束前不会因退出界面而消失。不要把令牌粘贴到聊天、命令参数或上传目录。已有受保护的凭据文件也可通过 `BAIDU_TUI_SECRETS_FILE` 显式指定；程序不会创建该文件，且要求目录 700、文件 600、文件不能为软链接。

界面按键：`u` 上传，`d` 下载，`↑↓` 选择任务，`c` 继续下载，`s` 停止任务，`q` 退出界面（后台任务继续）。上传选择服务器文件或目录和网盘目标；下载选择网盘路径和服务器目录。默认不覆盖同名文件，凭据文件不作为上传源。

速率是最近已确认的上传分片或已写入的下载字节的平均值。计算校验、等待接口、尚无两个采样点时显示 `--`，不是网卡瞬时带宽。任务完成按网盘路径、文件类型和大小核验；不等同于对全部文件进行 SHA-256 校验。

## 分片和兼容性

TUI 上传默认 4 MiB 分片、4 个并发，适合先用小文件验证。可在运行前按账号能力设置 `BAIDU_TUI_CHUNK_MIB`（4、8、16、32、64）和 `BAIDU_TUI_WORKERS`（1–8）。更大分片或更高并发不保证所有账号均支持；应先测小文件，失败时降低设置。

本包在 TUI 模式对超过 2048 个分片的文件提前提示，避免传输到后半段才失败。上传断点记录会保存在受保护状态目录，但此前结果不明的文件不会自动重新提交；需先核验网盘结果及源文件。下载支持使用临时文件继续。其他原技能工作流保持原有文件级恢复规则。

TUI 自动发现自身创建的任务，以及默认位于 `~/project/code/baidu*/config*.json`、`~/project/code/baidu*/baidu-mcp-transfer/config*.json` 和 `~/project/codex/baidu*/config*.json` 的兼容任务；可通过 `BAIDU_TUI_DISCOVERY_CODE_ROOT` 和 `BAIDU_TUI_DISCOVERY_STATE_ROOT` 指定这两个搜索根。第三方 MCP 如果不提供兼容的状态/进度，无法推断其传输速度；能识别的进程会显示“未接入进度”。

原 Codex SSH 传输工作流与参数见 `baidu-mcp-transfer/references/operations.md`。在新服务器或账号上应先用小样本验证；安装或离线测试本身不等于真实网盘上传成功。
