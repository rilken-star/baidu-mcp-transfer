# baidu-mcp-transfer 1.3.0

发布日期：2026-11-02

[下载安装包](baidu-mcp-transfer-1.3.0.zip) · [查看当前源码](../../baidu-mcp-transfer/) · [模型上传策略](../../baidu-mcp-transfer/references/model-upload-policy.md)

本次同时更新仓库根目录程序源码、安装器、校验清单和 ZIP，保留旧版本以及其他传输通道文件。

默认上传策略：

- 只上传模型权重（safetensors、ckpt、pt、pth、bin、gguf、sft、onnx），TXT、图片、JSON 等辅助文件不上传。
- 网盘路径忽略大小写，同名同大小跳过，冲突保留，不覆盖；已上传的辅助文件不删除。
- 文件链接须明确允许，目录链接不跟随；源路径、大小、mtime 和分片哈希一致时，复用未完成 uploadid，只发送未确认分片。
- 最终提交未知或缺少可信部分会话时继续隔离，不盲目重传；重复目标锁避免另起重复上传。
- 只读查询及相同分片有限退避；最终提交异常先核验；按工作线程复用连接。
- 自动续期保留，4/8 MiB 分片按账号信息与文件大小选择，2048分片保护保留。

```bash
baidu-tui --sync-models /服务器模型目录 --to /网盘模型目录 --follow-file-links
baidu-tui --resume-models /任务目录
# 确实要上传普通文件时才显式覆盖默认策略
baidu-tui --upload /服务器文件 --to /网盘目录 --all-files
```

完整安装与授权步骤见包内 INSTALL.md。模型任务目前支持命令启动、续传及面板查看，普通上传/下载保留原控制方式。

Linux 全新安装、85项离线回归测试、真实 stdio MCP 握手及模拟分片验证通过。指定服务器的真实模型任务正在继续，未声明全部363个模型已经完成。此包不含凭据、用户文件、实际任务记录或服务器连接信息。

校验：

```bash
shasum -a 256 -c SHA256SUMS
```
