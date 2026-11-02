# 服务器授权与自动续期

## 配置授权

先在自己的百度网盘应用中取得授权码，权限为 basic,netdisk，redirect_uri 为 oob，然后在传输服务器的交互终端运行：

```bash
baidu-tui --authorize --check-dir /你实际要访问的目录
```

命令隐藏输入 AppKey、SecretKey、授权码，仅向百度官方 HTTPS 接口兑换。目录只读验证通过后，原子保存访问令牌、刷新令牌、应用凭据和到期时间。默认保存至 `~/.config/model-download/secrets.env`，或 `BAIDU_TUI_SECRETS_FILE` 显式指定的文件。新文件目录权限 700、文件 600；已有目录或文件权限不符、存在软链接时拒绝使用。旧配置在同一受保护目录保留备份，其他键值不改动。使用自定义路径也可直接执行 `scripts/authorize.py --secrets-file /受保护路径/secrets.env --check-dir /目录`。

安装本身不会授权、写入凭据或启动传输；只有显式运行授权命令才创建或更新凭据文件。不要把凭据、授权码、令牌放在聊天、命令参数、任务配置、上传目录或版本库中。

## 续期行为

`BAIDU_TOKEN_EXPIRES_AT` 为 Unix 时间戳。剩余有效期不超过一小时时，下一个传输请求触发一次刷新；空闲时不执行定时刷新。自动续期读取同一文件中的 BAIDU_APP_KEY、BAIDU_SECRET_KEY、BAIDU_REFRESH_TOKEN，使用新 access_token 和 refresh_token 一起替换旧值，并更新到期时间。

上传元数据、预创建、分片与完成请求以及下载元数据请求会读取有效令牌；已打开的下载连接不会被主动中断。后台进程无需因令牌文件更新而重新启动。手动输入的令牌若与文件里的令牌不同，程序保留显式输入的授权，不自动换用文件中的其他账号。

使用进程内锁和跨进程 flock 锁避免并发刷新。`.baidu-refresh-state.json` 只记录脱敏状态及凭据文件版本；刷新失败、进程在刷新中断开、或结果未知时禁止对同一凭据版本自动重试。需要通过授权命令取得新凭据后继续。不能保证被用户撤销的授权永久有效。

刷新仅处理授权，不代表任意传输已成功。attempting/unknown 文件仍须核验目标存在性、大小及源签名，不自动重传；旧任务记录保留。

## 离线验证

```bash
cd baidu-mcp-transfer/scripts
python3 -m unittest test_baidu_auth test_authorize
```

测试使用临时文件和模拟 OAuth 响应，覆盖并发仅刷新一次、新刷新令牌保存、保留其他键、失败不重试、保护权限及命令行分派，不读取真实凭据或连接百度。
