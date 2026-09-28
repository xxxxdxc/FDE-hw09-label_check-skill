# FDE09 三层标签审核演示页

在 `code/demo` 目录运行：

```bash
python3 server.py --host 127.0.0.1 --port 8000
```

已有 Cloudflare Tunnel 将 `fde09.20040312.xyz` 指向 `http://127.0.0.1:8000`，无需修改域名或 Tunnel。服务提供网页、五组结构化案例和 `/api/check` 实时校验接口；不接受图片/OCR，也不提供公开的渠道卡写入接口。演示渠道卡保存在本机 `~/.cache/label-check-v2/demo-channel.sqlite`。

这台 Mac 现已配置两个用户级 LaunchAgent：`xyz.20040312.fde09.demo` 运行网页服务，`xyz.20040312.fde09.tunnel` 运行现有 Tunnel。它们在用户登录后启动、进程退出后自动重启；Tunnel 凭据放在仅当前用户可读的 `~/.cloudflared/fde09-token`，没有写入项目目录。若 Mac 关机、未登录或网络断开，公网仍会暂时不可用。

本地检查：

```bash
curl http://127.0.0.1:8000/api/health
curl http://127.0.0.1:8000/api/samples
```

页面的样例数据可编辑，结果中的 `FAIL`、`REVIEW` 和 `NOT_CHECKED` 保留原有三层规则含义。公网服务只用于课程演示，不是正式标签放行系统。
