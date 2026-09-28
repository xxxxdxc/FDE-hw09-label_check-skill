# 当前验证结果（结构化输入阶段）

运行方式：

```bash
python3 -m unittest discover -s tests -v
python3 scripts/label_check.py retrieval-eval --method lexical
python3 scripts/label_check.py retrieval-eval --method embedding
```

当前共有 49 项自动测试，覆盖课程每100g口径、核心营养项完整性、NRV修约、能量换算、单位、含量声称上下界、实际值容差及零值例外、配料排序与致敏物质提示、同一事实跨位置比较、渠道卡候选/确认/驳回、SKU和有效期、规则冲突、人工决策隔离。模拟数据在 `examples/` 和 `tests/`。测试通过仅证明这些已编码情形的行为，不代表完整法规审核能力。

知识检索使用 24 条自行编写的改写问句，对 8 个小型主题卡做 top-k 排序：本地 FTS5 为 top-1 22/24、top-3 24/24；`BAAI/bge-small-zh-v1.5` 为 top-1 22/24、top-3 24/24。因此当前默认 FTS5；embedding 为可选召回方式。问句和知识卡都来自同一设计者，这个数字不能作为上线准确率，更不能替代标签问题的误检/漏检测评。

尚未验收：图片/OCR、真实历史标签集、模糊且需专业解释的全部法规条款、渠道文件批量导入、最终印刷放行。尤其是缺少检测实际值时输出 `NOT_CHECKED`；未覆盖的营养项和声称转人工，避免硬判。
