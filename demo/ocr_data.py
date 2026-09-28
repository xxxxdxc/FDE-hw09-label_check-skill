"""Read-only adapter for the cached dual_v3 OCR dataset.

The live OCR boundary is deliberately separate: replace load_document() with
an authenticated OCR producer later; this demo never calls an OCR provider.
"""
import json
import os
import re
import sys
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DUAL_V3 = Path(os.environ.get("FDE09_DUAL_V3_DIR", ROOT / "dual_v3"))
PACKAGE = Path(os.environ.get(
    "FDE09_OCR_PACKAGE_DIR", HERE / "data"))
sys.path.insert(0, str(ROOT / "label-check"))
sys.path.insert(0, str(ROOT / "label-check-v2" / "src"))

from src.shared.review import gate_nutrition_table  # noqa: E402
from src.shared.serde import document_from_dict  # noqa: E402
from src.shared.serde import to_dict  # noqa: E402
from src.review.apply import apply_decisions  # noqa: E402
from src.review.page import render_page  # noqa: E402


def _json(path):
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _manifest():
    path = PACKAGE / "manifest.json"
    return {str(row["code"]): row for row in _json(path)["samples"]} if path.exists() else {}


def codes():
    return sorted(path.stem for path in (DUAL_V3 / "final_candidates").glob("*.json"))


def load_document(code, stage="final_candidates"):
    """OCR ingestion boundary: return a cached LabelDocument, never run OCR."""
    if code not in codes() or stage not in {"structured", "structured_vision", "final_candidates"}:
        raise ValueError("未找到这张图片的结构化记录")
    path = DUAL_V3 / stage / (code + ".json")
    if not path.is_file():
        raise ValueError("该图片没有视觉补全记录")
    return _json(path)


def _table_summary(doc):
    return [{
        "id": table["id"], "basis": table["basis"],
        "rows": [{"nutrient": row["nutrient"], "amount": row["amount"],
                  "nrvPercent": row.get("nrvPercent")} for row in table["rows"]],
    } for table in doc["facts"]["nutritionTables"]]


def _gate(doc):
    typed = document_from_dict(doc)
    if not typed.facts.nutritionTables:
        return {"status": "insufficient_data", "message": "没有可校验的营养成分表", "inputPaths": []}
    blocks = []
    for table in typed.facts.nutritionTables:
        result = gate_nutrition_table(typed, table.id, "OCR-DOWNSTREAM-GATE")
        if result:
            blocks.append(asdict(result))
    if blocks:
        return {"status": "needs_review", "message": "结构或字段尚待人工复核", "blocks": blocks}
    return {"status": "ready", "message": "当前结构化字段通过 OCR 门禁；还需检查适用计算口径"}


def _sample(code):
    auto = load_document(code, "structured")
    final = load_document(code)
    vision = (DUAL_V3 / "structured_vision" / (code + ".json")).is_file()
    meta = _manifest().get(code, {})
    review_file = DUAL_V3 / ("review_vision" if vision else "review") / (code + ".json")
    review = _json(review_file) if review_file.exists() else []
    if isinstance(review, dict):
        review = review.get("fieldItems", [])
    basis = final["facts"]["nutritionTables"][0]["basis"]["kind"] if final["facts"]["nutritionTables"] else "none"
    return {
        "code": code, "name": meta.get("productName") or code,
        "productUrl": meta.get("productUrl"), "license": meta.get("license"),
        "imageUrl": "/api/ocr/image/" + code if image_path(code).is_file() else None,
        "visionUsed": vision, "autoRows": sum(len(t["rows"]) for t in auto["facts"]["nutritionTables"]),
        "finalRows": sum(len(t["rows"]) for t in final["facts"]["nutritionTables"]),
        "basis": basis, "reviewCount": len(review), "gate": _gate(final),
        "autoDiagnostics": auto.get("tableDiagnostics", []),
        "finalDiagnostics": final.get("tableDiagnostics", []),
        "autoTables": _table_summary(auto), "finalTables": _table_summary(final),
        "reviewItems": review,
        "humanVerified": False,
    }


@lru_cache(maxsize=1)
def catalog():
    if not DUAL_V3.is_dir():
        raise FileNotFoundError("dual_v3 数据目录不存在")
    items = [_sample(code) for code in codes()]
    return [{key: row[key] for key in (
        "code", "name", "visionUsed", "autoRows", "finalRows", "basis",
        "reviewCount", "gate", "humanVerified") } for row in items]


def sample(code):
    if code not in codes():
        raise ValueError("样本不存在")
    return _sample(code)


def image_path(code):
    if not code.isdigit() or len(code) != 13:
        raise ValueError("样本编号无效")
    return PACKAGE / "images" / (code + ".jpg")


def review_page(code):
    if code not in codes():
        raise ValueError("样本不存在")
    image = image_path(code)
    if not image.is_file():
        raise FileNotFoundError("复核原图不在本机数据包中")
    html, _ = render_page(DUAL_V3 / "final_candidates" / (code + ".json"), image,
                          submit_url="/api/ocr/review/" + code)
    return html


def apply_review(code, decisions):
    if code not in codes():
        raise ValueError("样本不存在")
    image = image_path(code)
    if not image.is_file():
        raise FileNotFoundError("复核原图不在本机数据包中")
    typed = document_from_dict(load_document(code))
    reviewed = apply_decisions(typed, image.read_bytes(), decisions)
    reviewed_dict = to_dict(reviewed)
    payload, gate = checker_input(code, reviewed_dict)
    from label_check_v2.engine import run
    report = run(payload) if payload else None
    return {"reviewedDocument": reviewed_dict, "gate": gate, "report": report,
            "notice": "人工决定仅在本次请求中应用；请下载决定和复核后的 JSON 保存审计记录。"}


def checker_input(code, reviewed_doc=None):
    """Map gated OCR facts to the existing deterministic checker."""
    from label_check_v2.core import EXPECTED_UNITS  # imported after server adds v2/src

    doc = reviewed_doc or load_document(code)
    gate = _gate(doc)
    if gate["status"] != "ready":
        return None, gate
    tables = doc["facts"]["nutritionTables"]
    if len(tables) != 1 or tables[0]["basis"]["kind"] not in {"per_100g", "per_serving"}:
        return None, {"status": "unsupported_basis", "message": "当前 L1 支持每100g和有克数的每份；每100mL不能无密度换算成每100g。"}
    basis = tables[0]["basis"]
    serving = basis.get("servingSize")
    if basis["kind"] == "per_serving" and (not serving or serving.get("status") != "ready" or serving.get("unit") != "g"):
        return None, {"status": "unsupported_basis", "message": "每份数据缺少已复核的克数，不能换算为每100g。"}
    nutrients = {}
    skipped = []
    evidence = {entry["id"]: entry for entry in doc["evidence"]}
    for row in tables[0]["rows"]:
        key = row["nutrient"]["value"]
        key = "fiber" if key == "dietary_fiber" else key
        amount = row["amount"]
        if key not in EXPECTED_UNITS or amount["value"] is None:
            skipped.append(row["nutrient"].get("raw") or key or "未知营养素")
            continue
        refs = list(dict.fromkeys(row["nutrient"].get("sourceRefs", []) + amount.get("sourceRefs", [])))
        sources = [evidence[ref] for ref in refs if ref in evidence]
        manual = any(source["sourceType"] == "manual" for source in sources)
        scored = [source["ocrConfidence"] for source in sources if source.get("ocrConfidence") is not None]
        located = next((source for source in sources if source.get("boxPx")), None)
        item = {"value": amount["value"], "unit": amount["unit"],
                "source_text": amount.get("raw"), "source_file": code + ".jpg",
                "source_refs": refs, "review_status": "human_confirmed" if manual else "ocr_ready",
                "confidence": None if manual or not scored else min(scored)}
        if located:
            item["bbox"] = located["boxPx"]
        nrv = row.get("nrvPercent")
        if nrv and nrv.get("value") is not None:
            item["nrv_percent"] = nrv["value"]
        nutrients[key] = item
    if not nutrients:
        return None, {"status": "insufficient_data", "message": "没有当前规则引擎支持的营养项"}
    payload = {
        "product_id": code, "revision": "dual_v3-cache", "basis": basis["kind"],
        "table_header": ("每100g" if basis["kind"] == "per_100g" and
                         re.fullmatch(r"每\s*100\s*(?:克|[gG])", basis.get("raw") or "")
                         else basis.get("raw")),
        "table_header_raw": basis.get("raw"), "nutrition_complete": False,
        "nutrients": nutrients,
    }
    if serving:
        payload["serving_g"] = serving["value"]
    return payload, {"status": "ready", "message": "OCR 字段已交接到确定性计算模块", "skippedNutrients": skipped}
