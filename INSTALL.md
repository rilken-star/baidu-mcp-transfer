# 百度 MCP 服务器直传 — Codex 可安装包 1.0.0

把 ZIP 复制到另一台装有 Codex 的机器，解压后在该目录执行：

```bash
python3 install.py
```

默认安装到 `${CODEX_HOME:-~/.codex}/skills/baidu-mcp-transfer`。自定义技能父目录：

```bash
python3 install.py --dest /path/to/codex/skills
```

安装器离线校验包内全部技能文件，不覆盖已有同名技能，不修改全局 MCP 设置，不弹授权，不连接服务器，也不启动上传。安装成功后在 Codex 下一轮消息中可使用：

> 使用 $baidu-mcp-transfer 将我的服务器文件直接上传到百度网盘。先核对服务器、源目录、目标目录及排除范围，再部署运行。

也可以把本 ZIP 交给另一 Codex，告诉它“解压并执行 install.py 安装这个技能”。无需百度桌面客户端。Windows 建议使用 WSL 运行控制程序。

详细参数和例子在 `baidu-mcp-transfer/references/operations.md`。

打包内容是通用方法与程序，不含原任务的 IP/SSH 地址、目录清单、上传证明、日志、令牌或用户文件；不会恢复之前已经停止的任务。授权信息须在新机器显式隐藏输入，只留进程内存。

本版本提供文件级接续，不提供跨进程分片续传。打包前做了离线行为测试与本机真实 MCP 协议握手，未在其他机器进行真实百度上传；部署后先用用户指定的小样本核验实际账号和服务器兼容性。
