# 标签 OCR 结构化数据契约 v1.0

`src.ocr` 输出 `LabelDocument`。当前只实现 OCR 与结构化，不产生合规结论；后续模块可读取相同对象。Python 数据类见 `models.py`，JSON 编解码见 `serde.py`，入口校验见 `validation.py`。

## 顶层结构

| 字段 | 含义 |
|---|---|
| `schemaVersion` | 固定 `"1.0"` |
| `documentId` | 一次标签文档的 ID |
| `productId` | 产品标识；显式传入时采用用户提供值，否则仅在正面标题可用时取其文字作为**临时标识** |
| `productIdSource` | `provided`、`ocr_front_title`、`human_reviewed_title` 或 `unknown`；只有 `provided` 可直接用于可信的跨图片产品归组 |
| `images` | 图片 ID、文件名、原图宽高 |
| `evidence` | 全文 OCR 行、V2 单元格、视觉候选或人工输入；即使未解析也保留 |
| `facts` | 产品信息、营养表、配料、过敏原、宣传语 |
| `unassignedEvidenceIds` | 尚未归类的 OCR 行 ID |
| `tableDiagnostics` | 表格级质量诊断；`needs_vision` 交给 Skill 看原图，`needs_review` 交给人工确认整表，`ok` 才解除结构阻断 |

`Evidence.boxPx` 是 `{left, top, width, height}`：从原图左上角量起的像素矩形，右下角为 `(left+width, top+height)`。例如 `{left:1919,top:1747,width:149,height:39}` 定位原图中的“822千焦(kJ)”。不能把裁剪图中的坐标直接当作原图坐标；无法换算时填 `null`。`ocrConfidence` 是 OCR 服务给出的行级原始分数，不是数字正确的概率。

高精度全文 OCR 请求 `probability=true`，将每行 `probability.average` 原样保存在 `ocrConfidence`。**低于 0.96**（严格小于）或该路未返回分数的行，使引用它的字段变为 `needs_review`。V2 表格接口没有此行级分数，`ocrConfidence` 固定为 `null`；不能虚构分数。V2 单元格须有同位置、同内容且 ≥0.96 的全文 OCR 行佐证，才能单独成为 `ready`；否则待人工确认。任何全文 OCR 行低于 0.96，即便另一识别路一致，仍进入人工复核。阈值是人工分流规则，不是文字正确率；错行、缺单位等也独立触发复核。

营养表的 `basis.sourceRefs` 还引用表标题及可识别的列标题；任一标题分数不足时，`basis.status` 也变为 `needs_review`，以阻止下游直接使用这一张表的数值做确定性判断。

每个 `FieldValue` 包含 `raw`（原文）、`value`（解析值）、`unit`、`sourceRefs`、`status`、`reviewReasons`。数字 `value` 用十进制字符串（如 `"2.6"`），供下游转为 `Decimal`；文本 `value` 是普通字符串。`sourceRefs` 指向 `evidence.id`，因此计算结果将来能追溯到原图。字段状态为 `ready`、`needs_review`、`missing`。`ready` 只说明可供后续模块使用，不代表内容合规。

`facts` 的 `productName`、`netContent`、`ingredientsText`、`allergenText` 都是 `FieldValue`。`nutritionTables[]` 含 `basis` 与 `rows[]`；`basis.kind` 为 `per_serving`、`per_100g`、`per_100ml` 或 `unknown`，每份表头还需 `servingSize`。每行把 `nutrient`、`amount`、`nrvPercent` 分别做成带来源的字段。**原图没有印 NRV% 时填 `null`，不能把以后算出的数回填成识别值。** `claims[]` 保存宣传语原文、可识别的数量以及 `per_piece` 等基准；正面包装标题以 `category: front_title` 单独记录，虽放在同一数组中，但不代表营养或合规宣称。`barcodes[]` 保存从条码下方拼接的 13 位数字及其各段 OCR 证据；校验位不符则标待复核。

自动生成的 `productId` 直接取高置信度正面标题，例如 `原味咸甜玉米片`，`productIdSource` 为 `ocr_front_title`。这只方便同张图的结构化引用；同名产品可能有不同规格，后续跨图片核对必须先确认产品身份，再显式传入 `--product-id`。旧 JSON 未包含新增字段时，读取器会按原有字段恢复。

已有 `CheckResult` 数据类供后续计算与核对模块使用；其状态约定为 `pass`、`fail`、`needs_review`、`insufficient_data`、`not_applicable`。本阶段不生成这些结论。

## 字段路径速查（供计算、核对、报告模块使用）

以下路径均从 `LabelDocument` 根节点起算，`[i]` 表示数组下标。JSON 的 `null` 与 `"missing"` 不同：`null` 表示该可选对象未出现；`missing` 是必须关注但未找到原图内容的 `FieldValue`。数组为空表示当前没有解析出对应对象，**不等于已经证实包装上不存在**。

| 路径 | JSON 类型 / 可空 | 含义及下游用法 |
|---|---|---|
| `images[i].id` / `fileName` | 字符串 | 图片标识 / 原图文件名；`evidence.imageId` 用前者关联图片。文件名不保证是可直接访问的绝对路径。 |
| `images[i].widthPx` / `heightPx` | 正整数 | 原图尺寸，报告叠加标记时与 `boxPx` 配合使用。 |
| `evidence[i].id` / `text` | 字符串 | 原始证据 ID / 百度识别原文；任何结构化字段都通过 `sourceRefs` 追溯到这里。不要用规范化 `value` 覆盖 `text`。 |
| `evidence[i].imageId` / `boxPx` / `ocrConfidence` | 字符串或 `null` / 对象或 `null` / 0–1 数字或 `null` | OCR 行对应图片、原图坐标和百度行级分数。人工证据这三项均为 `null`。缺少坐标时不能在报告中臆造位置。 |
| `evidence[i].sourceType` | 字符串 | `baidu_accurate`：全文行与分数；`baidu_table_v2`：单元格、无分数；`codex_vision`：Skill 看图提出的未验证候选，坐标最多表示表格区域；`manual`：人工复核记录。 |
| `facts.productName` | `FieldValue` | 标签“产品名称”栏的内容，不必与正面标题相同。 |
| `facts.netContent` | 数字 `FieldValue` | 净含量，如 `value:"43", unit:"g"`；不是营养表每份量。 |
| `facts.ingredientsText` / `allergenText` | 文本 `FieldValue` | 配料段 / 致敏原段的完整 OCR 文本；尚未拆成单个配料或过敏原。 |
| `facts.nutritionTables[i].id` / `sourceRefs` | 字符串 / 字符串数组 | 表的本地编号及标题、列标题证据；同一包装可有多张表。 |
| `facts.nutritionTables[i].basis.kind` | 字符串 | `per_serving`、`per_100g`、`per_100ml` 或 `unknown`；计算模块必须先确认基准。 |
| `facts.nutritionTables[i].basis.raw` / `sourceRefs` / `status` | 字符串或 `null` / 字符串数组 / 字段状态 | 表头识别原文、来源和可用状态；列标题低置信度也可能使该状态变为 `needs_review`。 |
| `facts.nutritionTables[i].basis.servingSize` | `FieldValue` 或 `null` | `per_serving` 时必有，例如 `43 g`；其他基准通常为 `null`。与 `netContent` 分开读取。 |
| `facts.nutritionTables[i].rows[j].nutrient` | 文本 `FieldValue` | `raw` 是印刷名称，`value` 是代码：`energy`、`protein`、`fat`、`saturated_fat`、`trans_fat`、`carbohydrate`、`sugar`、`sodium`、`calcium` 或 `dietary_fiber`；未识别则 `value:null` 且需复核。 |
| `facts.nutritionTables[i].rows[j].amount` | 数字 `FieldValue` | 标签印刷含量，例如 `"822"` + `"kJ"`；需与本表 `basis` 一起解释，不能默认按 100 g。 |
| `facts.nutritionTables[i].rows[j].nrvPercent` | 数字 `FieldValue` 或 `null` | 原图印刷的 NRV%，`unit:"%"`；`null` 表示未配到印刷百分比，不得以程序计算值填充。 |
| `facts.claims[i].id` / `category` / `text` | 字符串 / 字符串 / `FieldValue` | 宣称编号、类别及原文。当前类别包括 `front_title`、`energy_per_piece`、`energy_per_bag`、`piece_count`、`trans_fat_zero`、`process`；`front_title` 只是正面标题，不自动构成合规宣称。 |
| `facts.claims[i].quantity` / `basis` | `FieldValue` 或 `null` / 字符串 | 可解析的宣称数字及其基准；当前基准可为 `per_piece`、`per_bag`、`per_serving`、`per_100g`、`unknown` 或 `none`。基准不同的数字不可直接比较。 |
| `facts.barcodes[i]` | `FieldValue` | 13 位条码字符串，可能由多段 OCR 行拼成；校验位失败时会标为待复核。它不是营养数量，不应转成数值计算。 |
| `unassignedEvidenceIds[i]` | 字符串 | 未被上述结构引用的证据 ID。它可能是品牌、厂家、脚注，也可能是漏解析的重要文字；报告或人工抽检应保留检查入口。 |
| `tableDiagnostics[i].code` / `status` / `message` | 字符串 | 例如 `TABLE_PARSED/ok`、`TABLE_STRUCTURE_UNCERTAIN/needs_vision`、`VISION_CANDIDATE_UNVERIFIED/needs_review`、`HUMAN_REVIEWED_TABLE/ok`。这是表格完整性状态，与字段 `status` 分开。 |
| `tableDiagnostics[i].tableId` / `boxPx` / `evidenceRefs` | 字符串或 `null` / 原图坐标或 `null` / ID 数组 | 指向受影响表格、原图区域和相关 OCR/人工证据。`tableId:null` 表示尚未形成可用表。 |

`FieldValue` 的固定键为 `raw: string|null`、`value: string|null`、`unit: string|null`、`sourceRefs: string[]`、`status: ready|needs_review|missing`、`reviewReasons: string[]`。文本字段的 `unit` 为 `null`；数值字段在 JSON 中仍是字符串，下游使用 `Decimal`。`reviewReasons` 还可能是 `TABLE_V2_UNCORROBORATED`、`OCR_ENGINE_DISAGREEMENT`、`TABLE_STRUCTURE_UNCERTAIN`、`VISION_CANDIDATE_UNVERIFIED`。这些是复核原因，不是合规失败结论。

下游处理顺序：读取 JSON → `document_from_dict` → `validate_document` → 营养表先调用 `gate_nutrition_table(document, table_id, check_id)`、其他字段调用 `gate_check` → 仅在表格诊断为 `ok`、必要字段全部 `ready` 且基准可比时计算或核对。输出另建 `DerivedValue` / `CheckResult`，不要改写 `evidence` 或把计算结果写入 `nrvPercent`。例如计算每份能量时，应读取 `facts.nutritionTables[i].basis`、`basis.servingSize` 与能量行的 `amount`；核对“一片约 9 大卡”时，还需要能证明片数与份量关系的证据，不能只看到 `per_piece` 和 `per_serving` 就直接判定矛盾。

`collect_review_items(document)` 列出待复核字段及原图位置；表格级任务另见 `tableDiagnostics` 和 `--vision-task-output`。`gate_check(check_id, {path: field})` 检查普通输入；营养表须调用 `gate_nutrition_table`，它同时检查结构诊断与全部字段。人工决定由 `src.review.page` 导出独立 JSON，再由 `src.review.apply` 校验原图 SHA-256、原值和证据引用，生成新的 `LabelDocument`。候选表的字段全部确认后，人工还须勾选“已检查全部行列”，才能将表格诊断变为 `ok`。没有人工证据的低分 OCR 或视觉候选不能标为 `ready`。

## 使用方式

从项目根目录运行：

```powershell
python -m src.main --image '..\M2-包装设计稿正面.png' --product-id 'longjing-43g' --output 'structured.json' --review-output 'review.json' --vision-task-output 'vision-tasks.json'
```

同时使用两路已保存响应离线复现（不会再次上传图片；V2 裁剪响应自带坐标变换）：

```powershell
python -m src.main --image '..\M2-包装设计稿正面.png' --ocr-json 'tests\fixtures\front_accurate.json' --table-v2-json 'tests\fixtures\front_table_v2_crop.json' --product-id 'longjing-43g' --output 'structured.json' --vision-task-output 'vision-tasks.json'
```

不指定 `--output` 时，JSON 写到标准输出。图片调用需要项目上级目录中的本地 `.env`，或环境变量 `BAIDU_API_KEY` / `BAIDU_SECRET_KEY`；不要将密钥写入仓库。输出文件只有显式指定时才创建。

## 当前解析边界

当前实时流程先运行全文高精度 OCR，再对图片或营养表裁剪图运行表格文字识别 V2。V2 的单元格行列用于营养表候选结构；全文 OCR 提供文字、原图坐标与置信度，两路按位置及内容核对。表头混入数据、单位与营养素不符、V2 少配营养行、零行等问题写入 `tableDiagnostics` 并阻断下游。Skill 可以查看原图并提交候选表，但候选仍需人工逐字段及整表确认。旧 `parse_accurate` 和仅传 `--ocr-json` 的离线入口仍可用于历史样例；它们没有 V2 的独立结构核对。
