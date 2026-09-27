# LabelCheckSkill｜场景⑨包装标签初检

## 项目介绍

本项目在已有的 `SKILL.md` 基础上，尝试解决包装标签初检中的三类问题：**图片里的文字和数字读错、营养数据算错、包装不同位置的信息对不上**。目标流程是：包装图片或人工录入 → 统一结构化数据 → 确定性计算 → 跨位置核对 → 带证据位置的初检报告。

**当前完成第一段：OCR 识别、结构化和人工复核。** `SKILL.md` 已加入该流程入口；计算、矛盾核对和报告模块尚未实现。现阶段的 JSON 是“从包装提取的事实”，不能当作合规审核结论。

## 当前代码结构

```text
label-check/
├── SKILL.md                  Skill 入口，含 OCR 人工复核流程
├── src/
│   ├── main.py               当前的命令行入口；未来可串联整个流程
│   ├── ocr/
│   │   ├── baidu.py           调用百度高精度含位置版 OCR
│   │   ├── images.py          读取 PNG/JPEG 原图尺寸
│   │   └── parser.py          解析文字、营养表行列、单位及宣称
│   ├── review/
│   │   ├── page.py            生成原图标注与人工输入的离线 HTML
│   │   ├── template.html      复核页面模板
│   │   └── apply.py           校验人工决定并生成复核后 JSON
│   └── shared/
│       ├── models.py          组内共用的数据类
│       ├── serde.py           数据类与 JSON 相互转换
│       ├── confidence.py      统一的 OCR 人工复核阈值
│       ├── review.py          待复核清单与下游检查门禁
│       ├── validation.py      检查字段、来源引用和坐标
│       └── CONTRACT.md        完整的数据格式定义
└── tests/
    ├── test_ocr_structure.py  结构化解析测试
    ├── test_review_page.py    复核页面生成测试
    ├── test_review_apply.py   人工决定应用测试
    └── fixtures/             OCR 行级数据及结构化结果样例
```

旧的项目上级目录中的 `test_ocr.py` 是早期单图试验脚本；组内集成以 `src/` 代码及 `shared` 契约为准。

## OCR 模块目前做了什么

1. 读取整张包装图片，调用百度 OCR 获取每行文字、原图坐标及服务返回的行级置信度。也可以读取保存的 OCR 响应离线解析，便于其他组员不消耗接口额度就能联调。
2. 将**全部**识别行保存为 `evidence`；定位营养成分表后，用“项目／每份／NRV%”列标题限定表格范围，再按行列关系配对营养素、含量、单位和印刷的 NRV%。同时提取产品名、净含量、配料段、过敏原段、正面标题、条码及可识别的宣传语。
3. 输出 `LabelDocument` JSON。每个字段保留原文、解析值、状态及 `sourceRefs`；通过引用可找到 OCR 行和原图像素坐标。暂时无法归类的行进入 `unassignedEvidenceIds`，不会丢失。

字段状态为 `ready`、`needs_review`、`missing`。数值在 JSON 中保存为十进制字符串（如 `"2.6"`），后续计算模块应转为 `Decimal`。目前所有 OCR 来源的结构化字段统一检查行级平均置信度：只要任一来源行**低于 0.96**，就标 `OCR_LOW_CONFIDENCE`；接口未返回分数则标 `OCR_CONFIDENCE_MISSING`。缺单位、同一行多个候选值、未知营养素或明显的配料文本异常也会独立触发复核。人工输入来源不套用 OCR 分数阈值。**0.96 是待更多样本验证的人工复核触发值，不是“正确率 96%”；高分也不能消除其他解析问题。**

`collect_review_items(document)` 从 `needs_review` 字段生成清单，包括字段路径、原因、当前结构化值、证据 ID、图片 ID、原图坐标和 OCR 分数。命令行可用 `--review-output review.json` 保存该清单，并在标准错误输出显示待复核字段数。`src/review/` 提供离线复核页和人工决定应用器；软件只提供定位、记录与校验，**不会替人判断原图究竟写了什么**。

正面标题保存在 `claims[]` 的 `front_title` 项中；未指定 `--product-id` 且标题为 `ready` 时，也会成为临时 `productId`，并标记 `productIdSource: ocr_front_title`。人工复核后可标为 `human_reviewed_title`。跨图片核对前仍需人工确认产品身份；显式传入的 `--product-id` 优先。条码数字单独保存在 `facts.barcodes[]`，保留每段来源和校验位结果。

用现有龙井厚乳包装图实测：返回 57 条 OCR 证据，解析出 1 张营养成分表及 8 行营养数据。配料文字中的括号异常被标为待确认。另一张“原味咸甜 72g”图片属于不同产品，不能和龙井厚乳 43g 的数据合并核对。这只是样图联调结果，尚不能证明作业要求的总体准确率。

## 如何运行

在 `label-check/` 目录打开 PowerShell。直接识别图片需要在项目上级目录的本地 `.env` 中配置 `BAIDU_API_KEY`、`BAIDU_SECRET_KEY`，或设置同名环境变量。`.env` 已被 Git 忽略。

```powershell
python -m src.main --image '..\M2-包装设计稿正面.png' --product-id longjing-43g --output structured.json --review-output review.json
```

使用仓库内的 OCR 行级数据样例离线复现，无需再请求百度：

```powershell
python -m src.main --image '..\M2-包装设计稿正面.png' --ocr-json 'tests\fixtures\front_accurate.json' --product-id longjing-43g --output structured.json --review-output review.json
```

不写 `--output` 时，JSON 只打印到终端；指定后才写入该文件。运行测试：

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

保留 `structured.json` 和决定 JSON 作为审计材料。已确认字段保留原 OCR 引用及坐标，并追加 `manual` 证据；未处理项继续 `needs_review`。具体操作见 [OCR 人工复核说明](references/ocr-review.md)。

OCR 验收指标可运行 `python -m tests.acceptance_eval --output tests/acceptance_report.json`。当前样本、人工标注范围和未满足的 50 图要求见 [验收测试说明](tests/ACCEPTANCE.md)；不能用单张样图的正确率宣称项目已通过验收。

可直接查看 [结构化样例](tests/fixtures/front_structured.json)；字段含义、`boxPx` 坐标规则和状态定义见 [数据契约](src/shared/CONTRACT.md)。

## 与后续模块如何承接

`src/main.py` 目前只负责“获取 OCR → 解析 → 校验 → 输出 JSON”；它放在 `src/`，是为以后串联完整流程预留的总入口。OCR 模块**只提供事实和来源**，不自行计算 NRV%、判断宣称是否合规，也不把计算值写回成“原图识别值”。

| 后续模块 | 从 `LabelDocument` 读取什么 | 应返回什么 |
|---|---|---|
| `calculator/`（未实现） | 营养表的数值、单位、标示基准及证据引用 | 独立的计算值和 `CheckResult`；先核实适用标准，再做能量、NRV% 等校验 |
| `checker/`（未实现） | 同一产品的表格、宣传语、脚注及其位置 | 跨位置一致性 `CheckResult`；基准不明确时标待确认 |
| `report/`（未实现） | 原始证据、结构化字段、各模块的检查结果 | 汇总状态、依据及所有相关原图位置 |

`CheckResult` 的约定状态为 `pass`、`fail`、`needs_review`、`insufficient_data`、`not_applicable`。后续模块在计算或判定前应调用 `gate_check(check_id, {字段路径: 字段对象})`：全部 `ready` 时返回 `None`，有待复核输入时返回 `needs_review`，有必需字段缺失时返回 `insufficient_data`。OCR 阶段自身**不产生合规审核结论**。后续开发优先复用 `src/shared/models.py` 和 [数据契约](src/shared/CONTRACT.md)，需要增加字段时同步修改契约、校验及测试样例。旧 `SKILL.md` 中“每份必须先折算每100g再算 NRV%”的表述不能直接作为计算规则。

按现有草拟分工，A 交付并维护 `LabelDocument`，B、C 可先基于 `tests/fixtures/front_structured.json` 并行开发；联调时再将样例输入替换为实际 OCR 输出。

## 当前边界

表格文字识别 V2 的自动补识别、独立人工录入入口、计算、交叉核对及报告都待后续实现。人工复核页只列结构化字段的待复核项；`unassignedEvidenceIds` 中的潜在漏项仍需另行检查。尚无 50 张人工标注标签的完整验收数据；项目提供的课程图片仅用于联调，不能据此宣称达到题目指标。
