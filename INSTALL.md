# 百度 MCP 服务器直传与 baidu-tui 安装包 1.4.0

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

这种手动环境变量模式不把令牌写入文件、安装包、任务配置或日志；它会存在当前终端及后台传输进程的环境/内存中，后台任务结束前不会因退出界面而消失。不要把令牌粘贴到聊天、命令参数或上传目录。已有受保护的凭据文件可通过 `BAIDU_TUI_SECRETS_FILE` 显式指定；新启动器也会识别 `~/.config/model-download/secrets.env`。凭据目录需为 700、文件为 600，且不能是软链接。手动传入不同令牌时不自动切换到其他账号的文件凭据。

界面按键：`u` 上传，`d` 下载，`↑↓` 选择任务，`c` 继续下载，`s` 停止任务，`q` 退出界面（后台任务继续）。上传默认先传模型权重（从大到小），最后传 TXT、图片等辅助文件。下载选择网盘路径和服务器目录。默认不覆盖同名文件，凭据文件不作为上传源。

速率是最近已确认的上传分片或已写入的下载字节的平均值。计算校验、等待接口、尚无两个采样点时显示 `--`，不是网卡瞬时带宽。任务完成按网盘路径、文件类型和大小核验；不等同于对全部文件进行 SHA-256 校验。

## 分片和兼容性

TUI 上传默认 4 MiB 分片、4 个并发，适合先用小文件验证。可在运行前按账号能力设置 `BAIDU_TUI_CHUNK_MIB`（4、8、16、32、64）和 `BAIDU_TUI_WORKERS`（1–8）。更大分片或更高并发不保证所有账号均支持；应先测小文件，失败时降低设置。

本包在 TUI 模式对超过 2048 个分片的文件提前提示，避免传输到后半段才失败。上传断点记录会保存在受保护状态目录，但此前结果不明的文件不会自动重新提交；需先核验网盘结果及源文件。下载支持使用临时文件继续。其他原技能工作流保持原有文件级恢复规则。

TUI 自动发现自身创建的任务，以及默认位于 `~/project/code/baidu*/config*.json`、`~/project/code/baidu*/baidu-mcp-transfer/config*.json` 和 `~/project/codex/baidu*/config*.json` 的兼容任务；可通过 `BAIDU_TUI_DISCOVERY_CODE_ROOT` 和 `BAIDU_TUI_DISCOVERY_STATE_ROOT` 指定这两个搜索根。第三方 MCP 如果不提供兼容的状态/进度，无法推断其传输速度；能识别的进程会显示“未接入进度”。

原 Codex SSH 传输工作流与参数见 `baidu-mcp-transfer/references/operations.md`。在新服务器或账号上应先用小样本验证；安装或离线测试本身不等于真实网盘上传成功。


## 1.1.1 更新：通用任务面板与终端兼容

- `baidu-tui` 启动时及运行期间自动刷新任务列表，包括通过本工具创建的上传、下载，以及提供兼容状态文件的同步任务。方向键可查看所有任务，`q` 只退出界面。
- 同步任务默认从 `~/.local/state/baidu-sync/*/` 发现，可通过 `BAIDU_TUI_SYNC_ROOT` 修改；这是元数据目录，不扫描模型或媒体内容。
- 已上传、已跳过、正在传输、排队和异常状态分别显示。上传速度按已确认传输字节的时间窗口计算，跳过已有文件不会增加上传速度；这不是网卡瞬时带宽。新开界面需等待至少两个采样点。
- 后台完成的任务仍可显示。进程已退出或 PID 被复用时不会误报正在传输。任务总进度包含同名同大小跳过量，速度只计实际传输量。
- 支持未设置 TERM、TERM=dumb、unknown 或服务器缺少对应 terminfo 的终端；会探测并回退到可用的 xterm-256color / xterm。
- 外部 MCP 若不提供任务进度元数据，只能发现在线进程并显示“未接入进度”；不能据此推断实际上传或下载速度。
- 本包不包含凭据、服务器地址、任务清单、传输记录或用户文件；不会自动启动既有同步任务。原安装器遇到同名目录或启动器仍停止，不覆盖已有安装。

离线回归验证（只使用临时文件和模拟接口）：

```bash
cd baidu-mcp-transfer/tui
python3 -m unittest test_tui test_portable test_discover test_general
```

兼容同步元数据的生产者接入格式见 `baidu-mcp-transfer/references/task-telemetry.md`。


## 1.2.0 更新：授权设置与自动续期

建议用同一应用的新授权码配置访问令牌与刷新凭据：

```bash
baidu-tui --authorize --check-dir /你实际需要访问的网盘目录
```

依次隐藏输入 AppKey、SecretKey 和新取得的 code 授权码。回调地址按 `oob` 兑换，须与获取授权码时一致；授权须包含 `basic,netdisk`。兑换成功并验证网盘目录可访问后，程序将访问令牌、刷新令牌、应用凭据及有效期保存到受保护的凭据文件，并备份原文件。屏幕不会输出这些值。此命令不会启动或恢复传输。

传输请求在令牌到期前一小时按需续期，无需手动刷新；空闲时不后台刷新。上传元数据、上传分片、上传完成请求和下载元数据请求都会重新读取有效令牌。文件下载中的既有连接不会因本地文件更新而被主动重开。多个进程共用刷新锁；新 access_token 与新 refresh_token 一起原子保存，保留其他凭据。刷新失败或结果不确定时停止，不反复尝试可能已经失效的 refresh_token；重新授权后再核验并恢复任务。

自动续期依赖凭据文件中的 `BAIDU_APP_KEY`、`BAIDU_SECRET_KEY`、`BAIDU_REFRESH_TOKEN`、`BAIDU_TOKEN_EXPIRES_AT` 和访问令牌。只有 Access Token 而没有完整刷新凭据时仍可使用，但不能自动续期。Access Token 并非永久有效，授权撤销或刷新凭据失效时仍须重新授权。

更多设置、失败处理及测试见 `baidu-mcp-transfer/references/auto-refresh.md`。安装器仍不覆盖旧安装；新版本应安装到独立目录，核实后再切换入口。


## 1.4.0 更新：模型优先、去重和同会话续传

模型目录同步默认使用以下策略：先上传 `.safetensors`、`.ckpt`、`.pt`、`.pth`、`.bin`、`.gguf`、`.sft`、`.onnx` 权重文件，按大小从大到小；全部模型已上传或已有同名同大小后，再上传 TXT、图片、JSON 等辅助文件。未完成或隔离的模型会阻止辅助阶段；辅助文件自身的隔离记录不自动重传。按扩展名筛选，不猜测模型内容，不另设可能误伤有效权重的最小文件大小；空模型文件标为需处理，不上传。

```bash
# 模型从大到小，辅助文件最后；目标目录须已存在。明确允许模型文件链接，目录链接始终不跟随。
baidu-tui --sync-models /服务器模型目录 --to /网盘模型目录 --follow-file-links

# 默认 --upload 也先传模型再传辅助文件；以下参数显式使用旧普通上传模式。
baidu-tui --upload /服务器文件 --to /网盘目录 --all-files

# 复用同一任务，不新建重复上传。
baidu-tui --resume-models /任务目录
```

界面按 `u` 默认进入模型上传，确认页面明确提示文件链接范围。CLI 不跟随文件链接，除非显式添加 `--follow-file-links`。每项任务保存 `plan.json` 内的 `policy`，目标路径比较忽略大小写；同名同大小跳过，同名大小不同或大小写冲突则保留现有文件并报告。目标锁阻止同一网盘目标同时运行重复模型任务。已上传的辅助文件不清理、不删除。

有未完成分片会话时先核验源路径、大小、mtime、分片大小和分片哈希，复用原 uploadid，只发送未确认的分片；分片确认记录完整但最终提交未知时不能据此重交。没有可验证的部分会话的 unknown 文件仍隔离，不自动重传。文件请求使用有限安全退避，最终提交响应丢失先只读核验，不盲目重复提交；权限、授权及证书错误不循环重试。分片连接按工作线程复用，避免每片都建立新连接。

分片大小按账号信息及文件大小选择，通常 4 MiB；会员身份已知且存在大于 8 GiB 的模型时选择 8 MiB，以保持分片数不超过 2048。服务端仍可能因账号权限或接口限额拒绝请求，失败不会绕过检查或覆盖文件。自动续期沿用1.2.0。

原生下载沿用原有功能。本版模型任务支持命令启动/续传及状态面板；面板的停止功能仍仅适用于原生普通上传、下载任务，模型同步不通过该按钮强制中断。

策略和字段说明见 `baidu-mcp-transfer/references/model-upload-policy.md`。
