# LabelCheckSkill｜场景⑨包装标签初检

## 项目介绍

本项目在已有的 `SKILL.md` 基础上，尝试解决包装标签初检中的三类问题：**图片里的文字和数字读错、营养数据算错、包装不同位置的信息对不上**。目标流程是：包装图片或人工录入 → 统一结构化数据 → 确定性计算 → 跨位置核对 → 带证据位置的初检报告。

**本目录完成第一段：双路 OCR、结构化、表格异常诊断与人工复核。** `SKILL.md` 已加入该流程入口。本目录的 JSON 是“从包装提取的事实”，不能当作合规审核结论。仓库另有 [`label-check-v2`](../label-check-v2/) 确定性规则模块；网页交接入口见 [`demo/README.md`](../demo/README.md)。

## 当前代码结构

```text
label-check/
├── SKILL.md                  Skill 入口，含 OCR 人工复核流程
├── src/
│   ├── main.py               当前的命令行入口；未来可串联整个流程
│   ├── ocr/
│   │   ├── baidu.py           调用百度高精度含位置版 OCR
│   │   ├── table_v2.py        调用百度表格文字识别 V2
│   │   ├── crop.py            大图表格区域裁剪及原图坐标映射
│   │   ├── dual.py            两路单元格/文字交叉核对与结构诊断
│   │   ├── vision.py          导入 Codex 看图给出的待确认候选表
│   │   ├── images.py          读取 PNG/JPEG 原图尺寸
│   │   └── parser.py          解析全文文字、产品信息和非表格字段
│   ├── review/
│   │   ├── page.py            生成原图标注与人工输入的离线 HTML
│   │   ├── template.html      复核页面模板
│   │   └── apply.py           校验人工决定并生成复核后 JSON
│   └── shared/
│       ├── models.py          组内共用的数据类
│       ├── serde.py           数据类与 JSON 相互转换
│       ├── confidence.py      统一的 OCR 人工复核阈值
│       ├── review.py          待复核清单与字段/整表门禁
│       ├── validation.py      检查字段、来源引用和坐标
│       └── CONTRACT.md        完整的数据格式定义
└── tests/
    ├── test_ocr_structure.py  结构化解析测试
    ├── test_review_page.py    复核页面生成测试
    ├── test_review_apply.py   人工决定应用测试
    ├── test_dual_ocr.py       真实 V2 响应回放与兜底闭环测试
    └── fixtures/             OCR 行级数据及结构化结果样例
```

旧的项目上级目录中的 `test_ocr.py` 是早期单图试验脚本；组内集成以 `src/` 代码及 `shared` 契约为准。

## OCR 模块目前做了什么

1. 全文高精度 OCR 取得每行文字、原图坐标与行级分数；图片较大时裁出营养表区域，再调用表格文字识别 V2 获取单元格、行列关系。裁剪图坐标换回原图像素。可分别保存两路原始响应，供离线复现。
2. `evidence` 保留两路原始证据。V2 提供营养表候选行列；同位置、同内容的全文 OCR 行提供置信度佐证。表头混入数据、错单位、少行、零行或两路冲突会被拦下；产品信息、配料、宣称、条码仍由全文 OCR 解析。
3. 输出 `LabelDocument` JSON。字段包含原文、值、状态与来源；`tableDiagnostics` 记录整表结构状态。待分类行进入 `unassignedEvidenceIds`。低分字段送人工；`needs_vision` 表格送当前 Codex Skill 看原图并提出候选，之后仍由人工确认每个字段和整表完整性。

字段状态为 `ready`、`needs_review`、`missing`；数值用十进制字符串。全文 OCR 行低于 **0.96** 或缺分数会触发人工复核。V2 不返回同类分数，不能把它的 `null` 当作置信度；V2 单元格只有得到高置信度全文行佐证，才可自动 `ready`。即使两路分数都高，也要过结构质量门槛。**0.96 是待标注样本验证的分流阈值，不是“正确率 96%”。**

`collect_review_items(document)` 生成字段复核清单；`--vision-task-output` 保存结构异常表格的原图区域和证据 ID。复核页可逐项修改字段，视觉候选还需勾选“已检查全部行列”。程序不会替人确认包装究竟写了什么。

正面标题保存在 `claims[]` 的 `front_title` 项中；未指定 `--product-id` 且标题为 `ready` 时，也会成为临时 `productId`，并标记 `productIdSource: ocr_front_title`。人工复核后可标为 `human_reviewed_title`。跨图片核对前仍需人工确认产品身份；显式传入的 `--product-id` 优先。条码数字单独保存在 `facts.barcodes[]`，保留每段来源和校验位结果。

真实 V2 联调：一张规则整齐的营养表成功配出 6 行及各 NRV%；龙井厚乳图的 V2 把“糖、钠”合为“糖钠”，被标为 `needs_vision`，不会把错行数据交给计算模块。另有一张弯曲表格因表头数据混入、能量单位错位被拦下。这些只是联调样例，尚不能证明作业要求的总体准确率。

## 如何运行

在 `label-check/` 目录打开 PowerShell。大图裁剪需要安装 `requirements.txt` 中的 Pillow；直接识别图片需要在项目上级目录的本地 `.env` 中配置 `BAIDU_API_KEY`、`BAIDU_SECRET_KEY`，或设置同名环境变量。`.env` 已被 Git 忽略。

```powershell
python -m pip install -r requirements.txt
python -m src.main --image '..\M2-包装设计稿正面.png' --product-id longjing-43g --output structured.json --review-output review.json --vision-task-output vision-tasks.json
```

两路已保存响应离线复现，无需再请求百度；V2 包装文件含裁剪坐标映射：

```powershell
python -m src.main --image '..\M2-包装设计稿正面.png' --ocr-json 'tests\fixtures\front_accurate.json' --table-v2-json 'tests\fixtures\front_table_v2_crop.json' --product-id longjing-43g --output structured.json --review-output review.json --vision-task-output vision-tasks.json
```

实时运行时也可用 `--accurate-raw-output`、`--table-v2-raw-output` 保存两路原始返回。只给 `--ocr-json` 会沿用旧单路解析，不会请求 V2。未指定 `--output` 时，结构化 JSON 只打印到终端。运行测试：

```powershell
python -m unittest discover -s tests -v
```

有待复核字段时，生成离线页面并在浏览器打开：

```powershell
python -m src.review.page --document structured.json --image '..\M2-包装设计稿正面.png' --output review.html
```

页面左侧显示原图；点击右侧字段会自动放大并定位到红圈区域，上方另有原图像素绘制的局部放大图。可用滚轮、滑块或加减按钮自由缩放，拖动图片查看周围内容，“适应整图”恢复全图。人工选择确认或修正，填写看到的原文、结构化值、单位与复核人，导出决定 JSON。软件校验原图 SHA-256、文档 ID、原值及证据引用后，生成**新的**结构化文件：

```powershell
python -m src.review.apply --document structured.json --image '..\M2-包装设计稿正面.png' --decisions review-decisions-m2-front.json --output reviewed.json
```

当 `vision-tasks.json` 有任务时，按 [双路 OCR 与视觉候选交接](docs/OCR_DUAL_WORKFLOW.md) 让当前 Codex Skill 查看原图并写候选表，再用 `python -m src.ocr.vision` 导入为**待人工复核**字段。人工复核页需逐字段确认，同时确认整张表的行列完整性。保留原始、候选、决定和复核后 JSON 作为审计材料。具体操作见 [OCR 人工复核说明](references/ocr-review.md)。

OCR 验收指标可运行 `python -m tests.acceptance_eval --output tests/acceptance_report.json`。当前样本、人工标注范围和未满足的 50 图要求见 [验收测试说明](tests/ACCEPTANCE.md)；不能用单张样图的正确率宣称项目已通过验收。

可直接查看 [结构化样例](tests/fixtures/front_structured.json)；字段含义、`boxPx` 坐标规则和状态定义见 [数据契约](src/shared/CONTRACT.md)。

## 与后续模块如何承接

`src/main.py` 目前只负责“获取 OCR → 解析 → 校验 → 输出 JSON”；它放在 `src/`，是为以后串联完整流程预留的总入口。OCR 模块**只提供事实和来源**，不自行计算 NRV%、判断宣称是否合规，也不把计算值写回成“原图识别值”。

| 后续模块 | 从 `LabelDocument` 读取什么 | 应返回什么 |
|---|---|---|
| `calculator/`（未实现） | 营养表的数值、单位、标示基准及证据引用 | 独立的计算值和 `CheckResult`；先核实适用标准，再做能量、NRV% 等校验 |
| `checker/`（未实现） | 同一产品的表格、宣传语、脚注及其位置 | 跨位置一致性 `CheckResult`；基准不明确时标待确认 |
| `report/`（未实现） | 原始证据、结构化字段、各模块的检查结果 | 汇总状态、依据及所有相关原图位置 |

`CheckResult` 的约定状态为 `pass`、`fail`、`needs_review`、`insufficient_data`、`not_applicable`。营养计算和核对须先调用 `gate_nutrition_table(document, table_id, check_id)`，同时检查表格结构与各字段；其他字段仍用 `gate_check`。OCR 阶段自身**不产生合规审核结论**。后续开发优先复用 `src/shared/models.py` 和 [数据契约](src/shared/CONTRACT.md)。旧 `SKILL.md` 中“每份必须先折算每100g再算 NRV%”的表述不能直接作为计算规则。

按现有草拟分工，A 交付并维护 `LabelDocument`，B、C 可先基于 `tests/fixtures/front_structured.json` 并行开发；联调时再将样例输入替换为实际 OCR 输出。

## 当前边界

通用表格解析已有两路识别与结构门槛，但复杂弯曲版面仍需 Skill 看图及人工确认。`unassignedEvidenceIds` 中的潜在漏项需抽查；计算、交叉核对和报告仍待实现。现有 50 图缺完整人工逐字、逐表标注，不能据此宣称达到 98%/95% 等作业验收指标。

## 本次更新

双路 OCR、漏行检测及视觉候选复核的简要说明见 [CHANGELOG.md](CHANGELOG.md)。完整回归测试依赖本地样例数据；测试结果与密钥不随代码提交。
