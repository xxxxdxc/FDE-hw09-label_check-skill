# OCR 人工复核操作

在仓库 `label-check/` 目录运行以下命令。`structured.json` 是未改动的 OCR 结构化结果；浏览器中的复核页不会向外部服务器发送图片，也不会改写原文件。

```powershell
python -m src.main --image '..\M2-营养成分表局部.png' --ocr-json 'tests\results\original72_accurate.json' --output 'structured.json' --review-output 'review.json'
python -m src.review.page --document 'structured.json' --image '..\M2-营养成分表局部.png' --output 'review.html'
```

打开 `review.html`。点击右侧一项后，左侧会自动放大并定位到红圈区域，上方另有局部放大图；蓝字显示识别内容。滚轮、缩放滑块和加减按钮可自由缩放，拖动图片可查看周围内容，“适应整图”恢复全图。人工对照原图，选择“确认原值”或“修正识别结果”；修正时分别填写原图文字、结构化值和单位。例如原图“净含量 72g”对应结构化值 `72`、单位 `g`。营养素名称的结构化值沿用已有代码（如 `fat`），不要把中文原文直接放进代码字段。复核人填写姓名或组内编号；可以分批导出，未完成项保持待复核。

浏览器下载的 `review-decisions-<documentId>.json` 是审计记录，包含原值、人工值、证据 ID、复核人、时间、原图 SHA-256。请保留它。应用时指定**新的输出路径**：

```powershell
python -m src.review.apply --document 'structured.json' --image '..\M2-营养成分表局部.png' --decisions 'review-decisions-original72.json' --output 'reviewed.json'
```

应用器会拒绝图片不符、旧版/重复字段、原值或证据引用被改动，以及无效数值或单位。已处理字段在新文件中保留原 OCR 引用和坐标，同时追加 `manual` 证据；未处理字段仍是 `needs_review`。如需再次复核，基于新的 `reviewed.json` 生成新页面。不要手动将引用低分 OCR 行的字段直接改为 `ready`。

阈值在 `src/shared/confidence.py`，当前为 `0.96`，仅用于筛选复核：任一被引用 OCR 行低于该值或缺失分数时，整个结构化字段需复核。高分可能仍有错字或错行；结构化异常也独立触发复核。`unassignedEvidenceIds` 中未归类的证据不会自动出现在字段复核页，需另行检查是否存在关键漏项。当前页面只支持一个 `LabelDocument` 对应一张原图。
