---
name: label-check-v2
description: 审核场景09的预包装食品营养标签结构化数据，执行每100g标准化计算、已确认渠道规则匹配，并整理人工复核事项。当前以JSON输入为主；图片识别暂不作为验收范围。
---

# 包装标签三层初检

输入字段见 [结构化输入约定](references/input-schema.md)。先拿到可追溯的标签转录数据、产品版次和适用渠道。不要把检索命中的文章、一次渠道退回或人工意见直接变成自动判错规则。

1. **L1 标准化**：运行 `python3 scripts/label_check.py check --input examples/per100g_ok.json`。脚本以 `Decimal` 统一 g/mg、kcal/kJ 和每份/每100g，检查能量、NRV%、已覆盖的声称、实际含量容差、同一事实的跨位置一致性，以及提供完整包装稿时的基础字段、配料顺序和致敏物质提示。输出必须明确写“每100g”。这一列头要求来自课程教师；不要说成 GB 28050 强制只允许每100g。每份40g蛋白质2.9g须在课程交付中改为7.25g/100g，NRV%约12%。原每份列的5%本身不按算术错误判定。
2. **L2 渠道要求**：通过 `cards submit` 将有来源、适用范围、生效期的要求存成候选卡；质量负责人用 `cards review` 确认后才匹配。证据缺失、卡片互相冲突或规则尚未确认时给 `REVIEW/NOT_CHECKED`，不硬判违规。数据库只保存脱敏要求卡和审查记录。
3. **L3 人工判断**：把品牌表达、未知声称、来源冲突和材料不足放到 `human_queue`。`human` 命令记录具名判断及依据，但不自动把它提升为 L2 规则。

规则版本和出处在 [policy.json](knowledge/policy.json)，检索主题在 [topics.json](knowledge/topics.json)。`search` 用于找依据，计算与判定始终由规则脚本执行；默认用本地 FTS。可选 `fastembed`/`BAAI/bge-small-zh-v1.5` 语义检索只用于候选证据召回。运行 `python3 -m unittest discover -s tests -v` 检查结构化模拟案例，`retrieval-eval` 检查检索候选排序。

已测覆盖、检索数字和当前边界见 [验证记录](references/evaluation.md)。

`FAIL` 表示当前数据触发明确问题，`REVIEW` 表示需要人工核实，`NOT_CHECKED` 表示缺少执行该项所需数据。报告仅是初检，不能充当最终印刷或上市放行。
