# dual_v3 缓存 OCR 结果

本目录是场景 09 优化 1 的离线回放输入，不会在网页访问时重新调用 OCR 或视觉模型。50 份 `structured/` 是双路 OCR 及规则解析结果；40 份 `structured_vision/` 是历史视觉兜底候选；`final_candidates/` 提供 50 份统一读取入口。`review/`、`review_vision/` 是待人工复核清单，`vision_tasks/` 是结构异常任务。详细修复与限制见 [`RECOVERY_REPORT.md`](RECOVERY_REPORT.md)。

所有视觉候选仍为 `needs_review`，不能当作已验收的事实。网页中的复核决定由 `label-check/src/review/apply.py` 应用到新文档；通过字段与整表门禁后，才映射到 `label-check-v2` 的数值规则。原图与逐张来源、许可在 [`demo/data/`](../demo/data/) 中。
