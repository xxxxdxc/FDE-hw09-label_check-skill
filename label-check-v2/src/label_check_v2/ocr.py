"""Local OCR adapter and cautious nutrition-table parser.

This is an implementation of the image path, not a claim that the OCR meets
the course's 98% target. Fields with poor confidence remain REVIEW in L1.
"""
import json
import re
import subprocess
import sys
from pathlib import Path


SWIFT_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "vision_ocr.swift"
LABELS = {
    "energy": ("能量",),
    "protein": ("蛋白质",),
    "fat": ("脂肪",),
    "saturated_fat": ("饱和脂肪", "饱和詣肪", "饱和脂肋"),
    "carbohydrate": ("碳水化合物",),
    "sugar": ("-糖", "—糖", "糖"),
    "fiber": ("膳食纤维",),
    "sodium": ("钠",),
}
AMOUNT = re.compile(r"(\d+(?:\.\d+)?)\s*(千焦|kJ|千卡|kcal|毫克|mg|克|g)", re.I)
PERCENT = re.compile(r"^(\d+(?:\.\d+)?)%$")
SERVING = re.compile(r"每份\s*(\d+(?:\.\d+)?)\s*(?:克|g)", re.I)


def run_vision(image):
    if sys.platform != "darwin":
        raise RuntimeError("本机OCR适配器需要macOS；可改用直接数据输入")
    path = Path(image)
    if not path.is_file():
        raise ValueError("图片不存在：" + str(path))
    completed = subprocess.run(["swift", str(SWIFT_SCRIPT), str(path)], text=True,
                               capture_output=True, timeout=90, check=False)
    if completed.returncode:
        raise RuntimeError("OCR失败：" + completed.stderr[-500:])
    lines = json.loads(completed.stdout)
    if not isinstance(lines, list):
        raise RuntimeError("OCR输出不是文本行列表")
    return lines


def _center(line):
    box = line["bbox"]
    return box[0] + box[2] / 2, box[1] + box[3] / 2


def _label(text):
    stripped = text.strip().replace(" ", "")
    if "反式" in stripped:
        return None  # it is not total fat
    if "饱和" in stripped:
        return "saturated_fat"
    for metric, variants in LABELS.items():
        if metric in ("fat", "saturated_fat") and ("饱和" in stripped or "反式" in stripped):
            continue
        if any(stripped == variant or stripped.startswith(variant + "（") for variant in variants):
            return metric
    return None


def parse_table(lines, image_path=None):
    header = next((line for line in lines if "营养成分表" in line.get("text", "")), None)
    if header is None:
        return {"error": "未定位到营养成分表", "ocr_lines": len(lines)}
    hx, hy = _center(header)
    # The table's text is below the heading, with labels on its left. Restrict
    # matches so front-of-pack slogans cannot be mistaken for nutrient rows.
    candidates = [line for line in lines if "bbox" in line and
                  hy - .25 < _center(line)[1] < hy + .005 and
                  hx - .12 < _center(line)[0] < hx + .38]
    basis_line = next((line for line in candidates if "每份" in line["text"] or "每100" in line["text"]), None)
    if basis_line is None:
        return {"error": "未读到表头口径", "ocr_lines": len(lines)}
    m = SERVING.search(basis_line["text"])
    if m:
        basis, serving_g = "per_serving", m.group(1)
    elif "每100" in basis_line["text"]:
        basis, serving_g = "per_100g", None
    else:
        return {"error": "表头份量不清", "ocr_lines": len(lines)}
    nrv_header = next((line for line in candidates if "NRV" in line["text"].upper()), None)
    nrv_x = _center(nrv_header)[0] if nrv_header else hx + .15
    labels = []
    for line in candidates:
        metric = _label(line["text"])
        if metric:
            labels.append((metric, line))
    nutrients = {}
    for metric, label_line in labels:
        lx, ly = _center(label_line)
        amount_lines = []
        percentage_lines = []
        for line in candidates:
            x, y = _center(line)
            if abs(y - ly) > .009:
                continue
            if x > lx + .035 and x < nrv_x - .01 and AMOUNT.search(line["text"]):
                amount_lines.append(line)
            if x >= nrv_x - .025 and PERCENT.match(line["text"].strip()):
                percentage_lines.append(line)
        if not amount_lines:
            continue
        amount_line = min(amount_lines, key=lambda line: abs(_center(line)[1] - ly))
        match = AMOUNT.search(amount_line["text"])
        percent_line = min(percentage_lines, key=lambda line: abs(_center(line)[1] - ly)) if percentage_lines else None
        confidence = min(float(label_line.get("confidence", 0)), float(amount_line.get("confidence", 0)))
        item = {
            "value": match.group(1), "unit": match.group(2),
            "source_text": amount_line["text"], "bbox": amount_line["bbox"],
            "source_file": str(image_path) if image_path else "OCR input",
            "confidence": confidence,
        }
        if percent_line:
            item["nrv_percent"] = PERCENT.match(percent_line["text"].strip()).group(1)
            item["confidence"] = min(confidence, float(percent_line.get("confidence", 0)))
        nutrients[metric] = item
    claims = []
    full_text = "\n".join(line.get("text", "") for line in lines)
    if "高膳食纤维" in full_text:
        claims.append("high_fiber")
    if "高蛋白" in full_text:
        claims.append("high_protein")
    return {
        "basis": basis, "serving_g": serving_g,
        "table_header": "每100g" if basis == "per_100g" else basis_line["text"],
        "basis_confidence": basis_line.get("confidence"),
        "nutrients": nutrients, "claims": claims,
        "label_text": full_text, "ocr_lines": len(lines),
        "parser_notes": ["本机OCR为候选读数；低置信度及缺失字段不能自动通过。"],
    }
