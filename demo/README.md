# FDE09 三层标签审核演示页

在 `code/demo` 目录运行：

```bash
python3 server.py --host 127.0.0.1 --port 8000
```

已有 Cloudflare Tunnel 将 `fde09.20040312.xyz` 指向 `http://127.0.0.1:8000`，无需修改域名或 Tunnel。首页提供五组结构化案例和 `/api/check` 规则校验。`/ocr` 把题目的优化 1 与优化 2 串起来：读取仓库中的 `dual_v3/structured`、`structured_vision`、`final_candidates`，展示 50 张原图与候选字段；待复核项进入原图定位的复核页，决定经图片 SHA-256、字段原值和整表完整性校验后，才生成新结构化文档并调用 `label-check-v2` 的确定性规则。复核结果只在本次请求中生成，由浏览器下载审计 JSON；服务端不留存复核数据。

实时 OCR 与实时视觉模型**没有接入**。`POST /api/ocr/ingest` 是待实现入口，明确返回 501；这次回放使用已生成的缓存数据，不会请求百度或模型。视觉兜底的 40 份 `structured_vision` 是历史候选，全部仍需人工确认。每100mL 在没有密度时不能换成每100g，局部营养图片也不提供配料、渠道或真实检测值；这些项目不会被伪造为已检查。演示渠道卡保存在本机 `~/.cache/label-check-v2/demo-channel.sqlite`，但 OCR 局部图片不自动套用渠道卡。

这台 Mac 现已配置两个用户级 LaunchAgent：`xyz.20040312.fde09.demo` 运行网页服务，`xyz.20040312.fde09.tunnel` 运行现有 Tunnel。它们在用户登录后启动、进程退出后自动重启；Tunnel 凭据放在仅当前用户可读的 `~/.cloudflared/fde09-token`，没有写入项目目录。若 Mac 关机、未登录或网络断开，公网仍会暂时不可用。

本地检查：

```bash
curl http://127.0.0.1:8000/api/health
curl http://127.0.0.1:8000/api/samples
```

`dual_v3/`、`demo/data/images/` 和 `demo/data/manifest.json` 随仓库保存，使缓存回放可复现。原图来自 Open Food Facts，逐张来源和许可见 [`data/README.md`](data/README.md)。可用环境变量 `FDE09_DUAL_V3_DIR`、`FDE09_OCR_PACKAGE_DIR` 指向其他本地数据包。运行 `python3 -m unittest -v test_ocr_handoff` 验证 OCR 门禁、人工决定交接、每份换算和图片绑定；测试中的“人工决定”仅是程序模拟，不是实际验收。公网服务只用于课程演示，不是正式标签放行系统。
