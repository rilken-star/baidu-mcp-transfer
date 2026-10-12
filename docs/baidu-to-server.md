# 百度网盘 → 服务器

这一方向使用社区 Node.js MCP。下载文件从百度直接进入服务器，本机只提供控制和隐藏授权。

## 服务器安装

服务器需要 Node.js 20.11+、npm、Python 3 和 git。每个任务使用独立的 `community-download` 目录；同一目录不要同时运行多个任务。

把本仓库的 `community-download/` 程序目录复制到服务器，例如 `/srv/baidu-download`。仅部署程序和无凭据的 job.json，不复制已有进度或用户文件。

在服务器执行：

```bash
cd /srv/baidu-download
git clone https://github.com/capwitf/baidu-netdisk-knowledge-mcp.git project
git -C project checkout ed7359624f82b77bb80e28839eb11e807518f23c
cd project
npm ci
npm run build
cd ..
cp job.example.json job.json
```

编辑 `job.json`，其中 `source` 是百度路径，`destination` 是服务器路径。示例排除目录按名字匹配，适用于任意层级；扩展名排除大小写不敏感。

```json
{
  "source": "/模型/models/loras",
  "destination": "/root/ComfyUI/models/loras",
  "exclude_directories": ["最终Z-Image", "真人lora"],
  "exclude_extensions": ["png", "webp", "jpeg", "jpg"]
}
```

复制所有目录及文件时使用空排除列表，例如：

```json
{
  "source": "/模型/最终输出lora",
  "destination": "/root/ComfyUI/models/loras/person",
  "exclude_directories": [],
  "exclude_extensions": []
}
```

程序限制列举和下载范围为 source；仅注册列举、下载两个 MCP 工具。默认不覆盖目标，同名文件大小一致时跳过，大小不符时报冲突；软链接目标会拒绝。现有同大小文件没有做内容哈希校验。

## 离线检查和启动

服务器执行以下检查，不连接百度 API：

```bash
cd /srv/baidu-download
node --check controller.mjs
node --check scoped-server.mjs
node controller.mjs --smoke
```

先选自己指定的小目录做实际验证，再扩大范围。首次 SSH 信任和密码输入在自己的终端完成：

```bash
ssh -fMN -p 22 -S /tmp/baidu-download-ssh \
  -o ControlPersist=yes -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 user@server.example
```

在本机仓库根目录启动：

```bash
python3 community-download/start.py \
  --host server.example --user user --port 22 \
  --remote-dir /srv/baidu-download \
  --control-path /tmp/baidu-download-ssh
```

macOS 隐藏对话框或 Linux/WSL 交互终端输入 Access Token。启动器通过 SSH 标准输入发送授权，服务器在后台运行。服务器重启会终止任务，须先检查状态再重启；Access Token 不保存，重启后需要重新输入。

## 进度和空间

```bash
ssh -p 22 -S /tmp/baidu-download-ssh \
  user@server.example 'cat /srv/baidu-download/progress.json'
```

`files/bytes` 是排除后总量，`verified_files/verified_bytes` 是本轮已处理并检查大小的量，含已有文件跳过；`current_bytes` 是正在写入的 `.part` 文件大小。它们不是单纯的新下载量。只有 `phase=complete`、`pending=0` 才是本轮完成；最终会重新逐项检查全部目标文件大小。

程序只计算尚缺文件的空间需求，并预留 1 GiB。空间不足时以 `INSUFFICIENT_SPACE` 退出；扩容后复用任务配置重新启动。此通用版本不自动等待扩容，与此前服务器上特定任务的等待逻辑不同。

## 停止和文件级恢复

停止前从 progress.json 取控制器 PID，并检查 `/proc/PID/cmdline` 确认对应这个任务；使用 `ps -eo pid,ppid,args` 找到它的 MCP 子进程。只终止核对过的控制器和子进程，不使用全局 `pkill node`，不影响其他下载。

若进度停留在 downloading/listing，但对应进程已不存在，先归档原 progress.json，再重新启动；启动器保守拒绝直接覆盖未终结状态。不要把活跃任务的进度文件移走。

已完成的目标文件按大小复用，不重复下载。遗留 `.part` 会报 `EXISTING_PARTIAL_FILE`：保留现场，核对是否还有写入进程；需要重下该文件时先将该部分文件移到任务外的独立归档目录，再重启。没有跨进程分片续传、自动覆盖、自动删除或失败自动重试。文件大小一致不是逐字节哈希证明。

