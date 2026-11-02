# 通用同步任务元数据

本工具自建上传和下载任务通过其注册文件与进度文件自动显示；标准 SSH MCP 工作流沿用现有配置发现机制。其他上传同步控制器可以接入以下文件协议，无需在界面代码中写死任务名、源路径或服务器信息。它不拦截外部网络流量，也不推断第三方工具未提供的速度。

## 目录与发现

默认根目录 `~/.local/state/baidu-sync`，每个任务使用独立子目录；可设置 `BAIDU_TUI_SYNC_ROOT`。根目录和任务目录建议权限 700，JSON 文件权限 600。只在此任务目录写入脱敏元数据，不写令牌、带授权参数的 URL、HTTP 原始错误或用户文件内容。

任务至少提供 `plan.json` 与 `status.json`；`launch.json` 用于核实进程身份；`results.json` 提供逐文件状态。所有文件都应通过临时文件和原子替换更新。

`plan.json`：

```json
{"source":"/srv/models","target":"/models","follow_file_links":false}
```

`status.json`：

```json
{"phase":"running","pid":12345,"started_at":1700000000.0,"updated_at":1700000001.0,"total_files":10,"total_bytes":1000,"counts":{"uploaded":1,"skipped_same_name_size":2},"uploaded_bytes":100,"skipped_bytes":200,"current":{"size":100,"completed_bytes":50,"stage":"accepted_part"}}
```

`phase` 可为 `starting`、`running`、`waiting_for_regular_files`、`complete`、`needs_attention`、`failed` 或 `stopped`。`current.completed_bytes` 仅计当前文件已确认的字节；成功完成后将文件总字节加入 `uploaded_bytes` 并清空 `current`，不要重复计数。已有同名同大小文件只计入 `skipped_bytes` 与 `counts.skipped_same_name_size`，不要计入 `uploaded_bytes`。

`launch.json`：

```json
{"pid":12345,"start_ticks":"123456"}
```

`start_ticks` 为 Linux `/proc/PID/stat` 第22字段，须在启动时记录；PID相同但启动标识不同会显示为已退出。兼容旧生产者未提供启动标识时，面板会比对进程启动时间与 `started_at`，无法核实时不声称进程在线。

`results.json`（可选）：

```json
{"file.bin":{"state":"attempting","size":100}}
```

允许的逐文件状态包括 `uploaded`、`skipped_same_name_size`、`attempting`、`unknown`、`case_conflict`、`different_conflict`、`source_changed` 与 `chunk_limit`。遇到冲突或未知结果，面板只展示状态，不自动覆盖或重传。当前兼容同步任务仅支持查看；原生上传、下载沿用原有停止与下载继续操作。

## 限制

这些数据描述上传同步任务；原生下载任务沿用下载器的 `progress.json` 协议，不应伪装为此上传同步格式。外部 MCP 进程不提供元数据时会显示“在线，未提供进度”，不会显示捏造的任务数、完成率或速度。元信息和大小核验不等于逐字节内容校验。
