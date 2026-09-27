# 营养表漏解析修复与下游承接（2026-09-27）

本次只重放已保存的全文 OCR 和 Table V2 响应，没有调用百度接口。规则解析和视觉候选分开输出，原始 OCR 不变。

## 代码改动

| 文件 | 改动及用途 |
|---|---|
| `src/ocr/parser.py` | 支持中外文营养素别名、双语名称、逗号小数、表头变体；拒绝把多数量、比较符、当量单位当作单一精确数值。盐保持 `salt`，不伪装为钠。 |
| `src/ocr/dual.py` | V2 空表不再阻断全文兜底；利用 V2 `contents` 的逐行文字和位置恢复候选；按列及相对行间距配对，并以含量位置匹配 NRV；有歧义不强配。增加全文营养素遗漏检查，捕获 V2 框外漏行。 |
| `src/ocr/crop.py` | 标题触发裁剪时保留完整横向宽度，避免切掉左右列或第二面板。此轮未调用在线 OCR，故新裁剪的线上效果尚未验证。 |
| `src/ocr/vision.py` | 候选通过原图 SHA256 绑定；支持显式替换错误表而非重复追加；保留原始证据；视觉候选一律 `needs_review`。 |
| `tests/test_dual_ocr.py` | 增加零行恢复、别名防误合并、倾斜 NRV 配对、数字/单位保护、裁剪边界和 V2 框外遗漏等回归。 |
| `tests/apply_zero14_vision.py` | 将本轮看图记录导入为未确认候选。支持多份 `--observations`；不调用模型 API，不属于生产解析规则，不是按条码硬编码答案的解析器。 |
| `tests/audit_recovery_50.py` | 校验 50 份契约、证据及视觉候选状态；对 9 份保留的 OCR 表做模型看图记录对照；生成 `final_candidates` 和 `final_audit.json`。 |

## 通用流程

缓存全文文字与坐标 + 缓存 V2 单元格 → 解析表头与基准 → 营养名称归一化 → 行列匹配 → 全文遗漏/单位/重复配对检查 → 结构不确定时输出看图任务 → Codex 看原图回填候选 → 人工确认后下游计算。

恢复行数不能证明结构正确。`TABLE_PARSED` 也仅是解析器内部诊断，不是人工验收。没有为提高通过率降低 0.96 的复核阈值。

## 新增信息的接口意义

- `front:v2part:...` 是 V2 `contents` 逐行证据 ID。仍通过 `sourceRefs` 引用，不依赖 ID 字符串的内部结构。
- `LAYOUT_FALLBACK_UNVERIFIED`：行列几何恢复候选，必须复核。
- `FULLTEXT_NUTRIENT_OMITTED`：全文发现营养素名称，但结构化表未包含，触发 `needs_vision`。
- `MULTIPLE_QUANTITIES`、`UNSUPPORTED_COMPARATOR`、`UNSUPPORTED_EQUIVALENT_UNIT`：不能安全表示成现有单一数量字段，保留 raw、清空不可靠值并复核。
- `NON_NUTRITION_TABLE`：功效成分等非营养表；不造出常规营养行。
- 视觉导入的 `supersedesTableIds` 明确列出被替换表；`replaceAllTables` 是整图替换模式。`boxPx` 只表示候选区域。本轮采用整图范围，**不能当作词级精确定位**。
- `codex_vision` 没有 OCR 置信度，不允许自行写 0.99。所有候选字段保持 `needs_review`；下游必须检查字段状态及表级诊断，不得只检查 value 非空。
- `µg RE`、`mg α-TE` 当量单位和 `<0.5g` 比较值尚未扩展到契约；当前保留原文并阻止精确计算。冲泡茶的制备基准也不得误写为干茶重量基准。

## 可复现命令（仓库目录中运行）

```powershell
python -m unittest discover -s tests -q
python -m tests.run_dual_50 --offline --dataset tests/results/market_labels_50 --output tests/results/market_labels_50/dual_v3 --project-root ..
python -m tests.apply_zero14_vision --results tests/results/market_labels_50/dual_v3 --images tests/results/market_labels_50/images --observations tests/fixtures/zero14_visual_observations.json tests/fixtures/extra_visual_observations.json
python -m tests.audit_recovery_50 --results tests/results/market_labels_50/dual_v3
```

看图记录属于测试素材，不是人工真值。不能用这些候选与自身一致来计算 ≥98% OCR 准确率或 ≥95% 独立结构化成功率。
