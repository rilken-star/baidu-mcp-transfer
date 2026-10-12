# 服务器 A ↔ 服务器 B

此通道使用 SSH + rsync，不依赖百度网盘或百度 Token。下面在服务器 A 上执行，数据从 A 直接进入 B，不落地控制电脑。

两端安装 rsync，A 能访问 B 的 SSH 地址。先在 A 的交互终端连接 B 并确认主机指纹。两台服务器分别需要自己的登录凭据，本机到 A 的认证不能自动代替 A 到 B 的认证。

## A 推送到 B

先预览差异：

```bash
rsync -an --itemize-changes --ignore-existing \
  -e 'ssh -p 22 -o StrictHostKeyChecking=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3' \
  /srv/source/ user@server-b.example:/srv/destination/
```

确认路径后上传：

```bash
rsync -a --partial --partial-dir=.rsync-partial --progress --ignore-existing \
  -e 'ssh -p 22 -o StrictHostKeyChecking=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3' \
  /srv/source/ user@server-b.example:/srv/destination/
```

源路径结尾的 `/` 表示复制目录内容。默认示例用 `--ignore-existing` 保留 B 上全部已有文件，但这也会跳过已有的不完整或不同内容文件，所以它不保证目标已与源完全一致。下面的校验会列出差异，需要逐项处理，不能直接宣称完成。

如果明确需要更新同名文件，可去掉 `--ignore-existing`，并添加 `--backup --backup-dir=/srv/transfer-backups/job-YYYYMMDD-HHMMSS`，为此次传输使用唯一备份目录。不要添加 `--delete`，避免删除目标已有文件。

## A 从 B 拉取

仍然在 A 执行，交换源和目标：

```bash
rsync -a --partial --partial-dir=.rsync-partial --progress --ignore-existing \
  -e 'ssh -p 22 -o StrictHostKeyChecking=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3' \
  user@server-b.example:/srv/source/ /srv/destination/
```

若 A 无法访问 B，但 B 能访问 A，可改在 B 上运行对应命令。两端互不可达时，需要单独设计中转通道，不能把本机中转描述为服务器直传。

## 恢复、排除与校验

中断后复用相同 rsync 命令，保留 `.rsync-partial`；不要手动删除其他任务文件。按需添加 `--exclude='目录名/'`、`--exclude='*.[pP][nN][gG]'` 等规则。需要排除多种图片格式时逐一列出。

执行内容校验预览：

```bash
rsync -anc --itemize-changes \
  -e 'ssh -p 22 -o StrictHostKeyChecking=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3' \
  /srv/source/ user@server-b.example:/srv/destination/
```

`-n` 只预览，不改文件；`-c` 读取两端文件内容计算校验和，可能较慢。传输与验证使用同一排除范围。无差异且退出码为 0 才能认定源范围已复制一致；没有 `--delete` 时，目标额外文件会保留。

