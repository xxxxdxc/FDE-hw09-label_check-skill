# 结构化输入约定

用 UTF-8 JSON 输入；[per100g_ok.json](../examples/per100g_ok.json) 与 [course_40g.json](../examples/course_40g.json) 是可运行示例。

| 字段 | 含义 |
| --- | --- |
| `product_id`, `revision` | SKU 或产品与包装版次。跨位置比较只在相同产品和版次进行。 |
| `basis` | `per_100g` 或 `per_serving`；后者须给正数 `serving_g`。不从 100mL 猜 100g。 |
| `table_header` | 课程交付应明确为 `每100g`。 |
| `nutrients` | 按营养项名列出 `{value, unit, nrv_percent?}`；能量用 kJ/kcal，钠用 mg/g，其余当前支持项用 g/mg。 |
| `nutrition_complete` | 设 `true` 表示转录了完整营养表；据此检查能量、蛋白质、脂肪、饱和脂肪、碳水化合物、糖、钠是否齐全。部分转录不要设为 `true`。 |
| `claims` | 当前自动检查 `high_protein`、`high_fiber`、`low_sugar`、`sugar_free`；其他声称转人工。 |
| `packaging` | 可选的包装文本数据。`complete: true` 时检查 `fields` 中的食品名称、配料表、净含量、生产者、保质期、贮存条件、生产许可信息是否已提供；`ingredients: [{name, amount_percent?}]` 检查已给占比的顺序，`allergen_declarations` 使用 `milk/peanut/soy/egg/gluten/fish/crustacean/tree_nut` 类别。过敏原词典只给复核提示。 |
| `actual_per_100g` | 可选的检测或可靠计算实际值；没给就不执行实际值允许误差比较。该值需先由业务方确认来源。 |
| `statements` | 可选的跨位置结构化事实。每项至少给 `fact_key, value, unit, basis`，每份口径另给 `serving_g`。可附 `bbox/source_text` 作为定位证据。只比较同一事实键、SKU 和版次。 |
| `channel`, `category`, `sku`, `label_text`, `documents`, `on_date` | L2 渠道规则匹配范围与当前材料；需同时传 `--db`。 |
| `subjective_questions` | 需要 L3 人工判断的问题列表。 |

`nrv_percent` 是百分数数值，如 `12` 或 `12%`。`value` 推荐用字符串保留原始小数位。输出 `normalized_per_100g` 是换算值，不会替代原始标签；`findings` 中保留规则 ID、层级、结论、依据类型及可用定位证据。

示例：

```bash
python3 scripts/label_check.py check --input examples/course_40g.json
python3 scripts/label_check.py cards --db /tmp/scene09-channels.sqlite submit \
  --channel 示例渠道 --requirement 必须写专供 --matcher contains \
  --target 专供 --source-ref 渠道邮件-2026-001
python3 scripts/label_check.py cards --db /tmp/scene09-channels.sqlite review 1 \
  --decision confirmed --reviewer 质量负责人
python3 scripts/label_check.py search '每份转换成每100g'
```

可选语义检索环境：在独立 Python 环境安装 `fastembed==0.7.4`，首次运行 `search ... --method embedding` 时下载 `BAAI/bge-small-zh-v1.5`。规则初检及普通检索不依赖该模型。
