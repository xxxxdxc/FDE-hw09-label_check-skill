# 场景 09：包装标签初检

仓库包含两段互补的工作：

| 目录 | 作用 | 当前边界 |
| --- | --- | --- |
| [`label-check/`](label-check/) | 双路 OCR、结构化和人工复核；保留原图证据与字段状态 | 输出包装事实，不直接给出合规结论；部分旧回归依赖未随仓库提交的历史缓存样例 |
| [`label-check-v2/`](label-check-v2/) | 对**人工录入或已核实的结构化数据**执行 L1 标准计算、L2 渠道要求、L3 人工判断记录 | 不把未经复核的 OCR 值直接送入自动判错 |
| [`dual_v3/`](dual_v3/) | 50 张标签的缓存 OCR 结构化结果，其中 40 张有 `structured_vision` 候选 | 视觉候选不是人工验收结果 |
| [`demo/`](demo/) | 网页串联优化 1 的缓存 OCR、人工复核与优化 2 的规则校验 | 不实时调用 OCR 或视觉模型；原图和来源见 `demo/data/` |

课程交付要求营养表明确写出“每 100g”，规则引擎据此统一换算。它不把原标签“每份 40g、蛋白质 2.9g、NRV% 5%”直接判为算错；改为每 100g 表示时，蛋白质为 7.25g、对应 NRV% 约 12%。详见 [`label-check-v2/SKILL.md`](label-check-v2/SKILL.md)。

快速验证：

```bash
cd label-check-v2
python3 -m unittest discover -s tests
python3 scripts/label_check.py check --input examples/per100g_ok.json

cd ../demo
python3 server.py --host 127.0.0.1 --port 8000
```

网页首页在 `http://127.0.0.1:8000/`，完整交接演示在 `http://127.0.0.1:8000/ocr`。OCR 产生的 [`LabelDocument`](label-check/src/shared/CONTRACT.md) 先经过字段/整表复核门禁，再由 [`demo/ocr_data.py`](demo/ocr_data.py) 映射到规则引擎的[输入格式](label-check-v2/references/input-schema.md)。实时 OCR 接口已留空，图片上传和在线视觉模型不在本次缓存演示中。
