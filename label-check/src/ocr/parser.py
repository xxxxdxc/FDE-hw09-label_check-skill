"""Turn full-image line OCR into traceable, structured packaging facts."""

from __future__ import annotations

import re
from pathlib import Path

from src.shared.models import (
    Basis, BoxPx, Claim, Evidence, Facts, FieldValue, ImageInfo,
    LabelDocument, NutritionRow, NutritionTable,
)
from src.shared.confidence import LOW_OCR_CONFIDENCE
from .images import image_size

NUTRIENTS = {
    "能量": "energy", "蛋白质": "protein", "脂肪": "fat",
    "饱和脂肪": "saturated_fat", "饱和脂肪酸": "saturated_fat",
    "反式脂肪酸": "trans_fat", "碳水化合物": "carbohydrate",
    "糖": "sugar", "钠": "sodium", "膳食纤维": "dietary_fiber",
}
UNIT_NAMES = {
    "千焦": "kJ", "kJ": "kJ", "克": "g", "g": "g",
    "毫克": "mg", "mg": "mg", "微克": "µg", "μg": "µg", "µg": "µg",
    "毫升": "mL", "ml": "mL", "mL": "mL", "大卡": "kcal", "千卡": "kcal", "kcal": "kcal",
}
NUMBER_UNIT = re.compile(r"(?P<number>\d+(?:\.\d+)?)\s*(?P<unit>千焦|毫克|微克|毫升|大卡|千卡|kcal|kJ|mg|mL|ml|µg|μg|克|g)", re.I)
SERVING = re.compile(r"每\s*份\s*(\d+(?:\.\d+)?)\s*(克|g|毫升|mL|ml)", re.I)


def _center_x(item: Evidence) -> float:
    return item.boxPx.left + item.boxPx.width / 2 if item.boxPx else -1


def _center_y(item: Evidence) -> float:
    return item.boxPx.top + item.boxPx.height / 2 if item.boxPx else -1


def _field(raw: str, value: str | None, unit: str | None, entries: list[Evidence], numeric=False, extra=None) -> FieldValue:
    reasons = list(extra or [])
    if any(e.sourceType != "manual" and e.ocrConfidence is None for e in entries):
        reasons.append("OCR_CONFIDENCE_MISSING")
    if any(e.sourceType != "manual" and e.ocrConfidence is not None and e.ocrConfidence < LOW_OCR_CONFIDENCE for e in entries):
        reasons.append("OCR_LOW_CONFIDENCE")
    if any(e.sourceType != "manual" and e.boxPx is None for e in entries):
        reasons.append("LOCATION_UNKNOWN")
    if numeric and unit is None:
        reasons.append("UNIT_MISSING")
    return FieldValue(raw, value, unit, [e.id for e in entries], "needs_review" if reasons else "ready", list(dict.fromkeys(reasons)))


def _quantity(item: Evidence) -> FieldValue:
    match = NUMBER_UNIT.search(item.text)
    if not match:
        number = re.search(r"\d+(?:\.\d+)?", item.text)
        return _field(item.text, number.group(0) if number else None, None, [item], numeric=True,
                      extra=[] if number else ["NUMBER_OR_UNIT_UNREADABLE"])
    unit = UNIT_NAMES.get(match.group("unit"))
    return _field(item.text, match.group("number"), unit, [item], numeric=True)


def _evidence_from_accurate(raw: dict, image_id: str, width: int, height: int) -> list[Evidence]:
    entries = []
    for index, item in enumerate(raw.get("words_result", []), start=1):
        location = item.get("location") or {}
        try:
            box = BoxPx(int(location["left"]), int(location["top"]), int(location["width"]), int(location["height"]))
            if box.left < 0 or box.top < 0 or box.width <= 0 or box.height <= 0 or box.left + box.width > width or box.top + box.height > height:
                box = None
        except (KeyError, TypeError, ValueError):
            box = None
        probability = item.get("probability") or {}
        confidence = probability.get("average")
        entries.append(Evidence(
            id=f"{image_id}:{index}", imageId=image_id, sourceType="baidu_accurate",
            text=str(item.get("words", "")), boxPx=box,
            ocrConfidence=float(confidence) if isinstance(confidence, (int, float)) else None,
        ))
    return entries


def _find_simple_field(entries: list[Evidence], pattern: str, unit: str | None = None, numeric=False) -> FieldValue:
    regex = re.compile(pattern)
    for entry in entries:
        match = regex.search(entry.text)
        if match:
            value = match.group(1).strip()
            return _field(match.group(0), value, unit, [entry], numeric=numeric)
    return FieldValue.missing()


def _paragraph(entries: list[Evidence], start: str, stop: str) -> FieldValue:
    ordered = sorted((e for e in entries if e.boxPx), key=lambda e: (e.boxPx.top, e.boxPx.left))
    collected, pieces, active, start_entry = [], [], False, None
    start_re, stop_re = re.compile(start), re.compile(stop)
    for entry in ordered:
        text = entry.text
        if not active:
            match = start_re.search(text)
            if not match:
                continue
            active = True
            start_entry = entry
            text = text[match.end():]
        elif (entry.boxPx.left > start_entry.boxPx.left + start_entry.boxPx.width + 100 or
              entry.boxPx.left + entry.boxPx.width < start_entry.boxPx.left - 100):
            continue
        end_match = stop_re.search(text)
        piece = text[:end_match.start()] if end_match else text
        piece = piece.strip("○● ：:;；")
        if piece:
            collected.append(entry)
            pieces.append(piece)
        if end_match:
            break
    if not collected:
        return FieldValue.missing()
    joined = "".join(pieces)
    extra = []
    normalized = joined.replace("（", "(").replace("）", ")")
    depth = 0
    for char in normalized:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                break
    if depth != 0 or re.search(r"[≥≤]\s*\(", normalized):
        extra.append("OCR_TEXT_ANOMALY")
    return _field("\n".join(pieces), joined, None, collected, extra=extra)


def _basis(candidates: list[Evidence]) -> Basis:
    for entry in candidates:
        text = entry.text.replace(" ", "")
        match = SERVING.search(text)
        if match:
            unit = UNIT_NAMES[match.group(2)]
            serving = _field(match.group(0), match.group(1), unit, [entry], numeric=True)
            return Basis("per_serving", match.group(0), [entry.id], serving.status, serving)
        if re.search(r"每100\s*(?:克|g)", text, re.I):
            return Basis("per_100g", text, [entry.id], _field(text, text, None, [entry]).status)
        if re.search(r"每100\s*(?:毫升|mL|ml)", text, re.I):
            return Basis("per_100ml", text, [entry.id], _field(text, text, None, [entry]).status)
    return Basis("unknown", None, [], "needs_review")


def _nutrient_code(text: str) -> str | None:
    clean = re.sub(r"^[\s\-—－·]+", "", text).replace(" ", "").strip("：:")
    return NUTRIENTS.get(clean)


def _assign_by_row(items: list[Evidence], labels: list[Evidence]) -> dict[str, list[Evidence]]:
    assigned = {entry.id: [] for entry in labels}
    for item in items:
        distances = sorted(((abs(_center_y(item) - _center_y(label)), label) for label in labels), key=lambda pair: pair[0])
        if distances and distances[0][0] <= 25:
            assigned[distances[0][1].id].append(item)
    return assigned


def _nutrition_tables(entries: list[Evidence]) -> list[NutritionTable]:
    titles = [e for e in entries if "营养成分表" in e.text and e.boxPx]
    tables = []
    for index, title in enumerate(titles, start=1):
        left = max(0, title.boxPx.left - 320)
        right = title.boxPx.left + title.boxPx.width + 370
        nearby = [e for e in entries if e.boxPx and e is not title and
                  left <= _center_x(e) <= right and
                  title.boxPx.top <= _center_y(e) <= title.boxPx.top + 450]
        header_band = [e for e in nearby if _center_y(e) <= title.boxPx.top + 115]
        label_header = next((e for e in header_band if e.text.strip() == "项目"), None)
        nrv_header = next((e for e in header_band if "NRV" in e.text.upper()), None)
        basis_entries = [e for e in header_band if "每" in e.text and ("份" in e.text or "100" in e.text)]
        # Use the actual column headers to bound the table, not the title's broad neighborhood.
        if label_header and nrv_header:
            table_left = max(0, label_header.boxPx.left - max(12, label_header.boxPx.width // 2))
            table_right = nrv_header.boxPx.left + nrv_header.boxPx.width + max(12, nrv_header.boxPx.width // 2)
            nearby = [e for e in nearby if table_left <= _center_x(e) <= table_right]
        row_start = max((e.boxPx.top + e.boxPx.height for e in
                         (label_header, nrv_header, *basis_entries) if e), default=title.boxPx.top + 70)
        basis = _basis(basis_entries)
        structure = [e for e in (title, label_header, nrv_header) if e is not None]
        basis.sourceRefs = list(dict.fromkeys([*basis.sourceRefs, *(e.id for e in structure)]))
        if _field(" | ".join(e.text for e in structure), "table headers", None, structure).status != "ready":
            basis.status = "needs_review"
        label_x = _center_x(label_header) if label_header else left + 100
        nrv_x = _center_x(nrv_header) if nrv_header else right - 100
        amount_x = (_center_x(basis_entries[0]) if basis_entries else (label_x + nrv_x) / 2)
        first_boundary = (label_x + amount_x) / 2
        second_boundary = (amount_x + nrv_x) / 2
        amounts = [e for e in nearby if first_boundary <= _center_x(e) < second_boundary and
                   re.search(r"^\s*\d", e.text) and _center_y(e) > row_start]
        nrvs = [e for e in nearby if _center_x(e) >= second_boundary and
                re.fullmatch(r"\s*\d+(?:\.\d+)?\s*%\s*", e.text) and
                _center_y(e) > row_start]
        labels = [e for e in nearby if _center_x(e) < first_boundary and
                  _center_y(e) > row_start and e.text.strip() != "项目" and
                  (_nutrient_code(e.text) or
                   (len(e.text) <= 16 and any(abs(_center_y(e) - _center_y(a)) <= 25 for a in amounts)))]
        labels.sort(key=_center_y)
        amount_map, nrv_map = _assign_by_row(amounts, labels), _assign_by_row(nrvs, labels)
        rows = []
        for label in labels:
            code = _nutrient_code(label.text)
            nutrient = _field(label.text, code, None, [label],
                              extra=[] if code else ["UNKNOWN_NUTRIENT"])
            amount_options = amount_map[label.id]
            if len(amount_options) == 1:
                amount = _quantity(amount_options[0])
            elif len(amount_options) > 1:
                amount = _field(" | ".join(e.text for e in amount_options), None, None, amount_options, numeric=True, extra=["AMBIGUOUS_ROW"])
            else:
                amount = FieldValue.missing()
            nrv_options = nrv_map[label.id]
            if len(nrv_options) == 1:
                item = nrv_options[0]
                value = item.text.strip().removesuffix("%").strip()
                nrv = _field(item.text, value, "%", [item], numeric=True)
            elif len(nrv_options) > 1:
                nrv = _field(" | ".join(e.text for e in nrv_options), None, "%", nrv_options, numeric=True, extra=["AMBIGUOUS_ROW"])
            else:
                nrv = None  # The printed NRV% cell may intentionally be blank.
            rows.append(NutritionRow(nutrient, amount, nrv))
        if rows:
            table_refs = [title.id]
            table_refs.extend(e.id for e in (label_header, nrv_header) if e)
            tables.append(NutritionTable(f"table:{index}", basis, rows, table_refs))
    return tables


def _front_title(entries: list[Evidence], image_height: int) -> FieldValue:
    candidates = [e for e in entries if e.boxPx and e.boxPx.top < image_height * 0.55 and
                  "玉米片" in e.text and "产品名称" not in e.text and len(e.text) <= 24]
    if not candidates:
        return FieldValue.missing()
    title = max(candidates, key=lambda e: (len(e.text), e.boxPx.height))
    parts = [title]
    if title.text.strip() == "玉米片":
        above = [e for e in entries if e.boxPx and e is not title and e.boxPx.top < title.boxPx.top and
                 title.boxPx.top - (e.boxPx.top + e.boxPx.height) < title.boxPx.height and
                 e.boxPx.height >= title.boxPx.height * 0.45 and
                 min(e.boxPx.left + e.boxPx.width, title.boxPx.left + title.boxPx.width) -
                 max(e.boxPx.left, title.boxPx.left) >= min(e.boxPx.width, title.boxPx.width) * 0.5 and
                 re.fullmatch(r"[\u4e00-\u9fff]{2,12}", e.text)]
        if above:
            parts.insert(0, max(above, key=lambda e: e.boxPx.top))
    value = "".join(e.text.strip() for e in parts)
    return _field(value, value, None, parts)


def _barcode_check_digit(value: str) -> bool:
    total = sum(int(char) * (1 if index % 2 == 0 else 3) for index, char in enumerate(value[:12]))
    return (10 - total % 10) % 10 == int(value[12])


def _barcodes(entries: list[Evidence], image_height: int) -> list[FieldValue]:
    digits = [e for e in entries if e.boxPx and e.boxPx.top > image_height * 0.6 and
              re.fullmatch(r"\d{1,13}", e.text.strip())]
    found = []
    seen = set()
    for entry in digits:
        if len(entry.text.strip()) == 13:
            parts = [entry]
        elif len(entry.text.strip()) == 12:
            leads = [e for e in digits if len(e.text.strip()) == 1 and e.boxPx.left < entry.boxPx.left and
                     0 <= entry.boxPx.left - (e.boxPx.left + e.boxPx.width) <= 80 and
                     abs(_center_y(e) - _center_y(entry)) <= max(e.boxPx.height, entry.boxPx.height)]
            if not leads:
                continue
            parts = [min(leads, key=lambda e: entry.boxPx.left - (e.boxPx.left + e.boxPx.width)), entry]
        else:
            continue
        value = "".join(e.text.strip() for e in parts)
        if value in seen:
            continue
        seen.add(value)
        extra = [] if _barcode_check_digit(value) else ["BARCODE_CHECKSUM_INVALID"]
        found.append(_field(" ".join(e.text for e in parts), value, None, parts, extra=extra))
    return found


def _claims(entries: list[Evidence], tables: list[NutritionTable]) -> list[Claim]:
    table_ids = {ref for table in tables for row in table.rows for f in (row.nutrient, row.amount, row.nrvPercent) if f for ref in f.sourceRefs}
    claims = []
    patterns = [
        ("energy_per_piece", "per_piece", re.compile(r"(?:每片|一片)\s*(?:热量)?\s*约?为?\s*\d+(?:\.\d+)?\s*(?:大卡|千卡|kcal)", re.I)),
        ("energy_per_bag", "per_bag", re.compile(r"每袋\s*\d+(?:\.\d+)?\s*g\s*热量\s*约?为?\s*\d+(?:\.\d+)?\s*kcal", re.I)),
        ("piece_count", "per_bag", re.compile(r"约\s*\d+\s*片/袋")),
        ("trans_fat_zero", "unknown", re.compile(r"0\s*反式脂肪酸")),
        ("process", "none", re.compile(r"非油炸")),
    ]
    for entry in entries:
        if entry.id in table_ids:
            continue
        for category, basis, pattern in patterns:
            for match in pattern.finditer(entry.text):
                phrase = match.group(0)
                text = _field(phrase, phrase, None, [entry])
                quantity = None
                if category == "energy_per_bag":
                    energy = re.search(r"热量\s*约?为?\s*(\d+(?:\.\d+)?)\s*kcal", phrase, re.I)
                    quantity = _field(energy.group(0), energy.group(1), "kcal", [entry], numeric=True) if energy else None
                elif category in {"energy_per_piece", "piece_count", "trans_fat_zero"}:
                    number = re.search(r"(\d+(?:\.\d+)?)", phrase)
                    unit = "kcal" if category == "energy_per_piece" else "piece" if category == "piece_count" else "g"
                    quantity = _field(number.group(0), number.group(1), unit, [entry], numeric=True) if number else None
                claims.append(Claim(f"claim:{len(claims) + 1}", category, text, quantity, basis))
    return claims


def parse_accurate(image_path: Path, raw_response: dict, document_id: str, product_id: str | None = None, image_id: str = "front") -> LabelDocument:
    width, height = image_size(image_path)
    evidence = _evidence_from_accurate(raw_response, image_id, width, height)
    if not evidence:
        raise ValueError("OCR returned no text; cannot structure the label")
    tables = _nutrition_tables(evidence)
    title = _front_title(evidence, height)
    claims = _claims(evidence, tables)
    if title.status != "missing":
        claims.insert(0, Claim("claim:front-title", "front_title", title, None, "none"))
    facts = Facts(
        productName=_find_simple_field(evidence, r"产品名称[：:]\s*([^○●\n]+)"),
        netContent=_find_simple_field(evidence, r"净含量[：:]\s*(\d+(?:\.\d+)?)\s*(?:克|g)", "g", numeric=True),
        ingredientsText=_paragraph(evidence, r"配料表[：:]", r"致敏原信息[：:]|贮藏条件[：:]"),
        allergenText=_paragraph(evidence, r"致敏原信息[：:]", r"贮藏条件[：:]|食用方法[：:]"),
        nutritionTables=tables,
        claims=claims,
        barcodes=_barcodes(evidence, height),
    )
    used = set()
    for field in (facts.productName, facts.netContent, facts.ingredientsText, facts.allergenText):
        used.update(field.sourceRefs)
    for table in tables:
        used.update(table.sourceRefs)
        used.update(table.basis.sourceRefs)
        for row in table.rows:
            for field in (row.nutrient, row.amount, row.nrvPercent):
                if field:
                    used.update(field.sourceRefs)
    for claim in facts.claims:
        used.update(claim.text.sourceRefs)
        if claim.quantity:
            used.update(claim.quantity.sourceRefs)
    for barcode in facts.barcodes:
        used.update(barcode.sourceRefs)
    derived_id = product_id is None and title.status == "ready"
    return LabelDocument(
        schemaVersion="1.0", documentId=document_id,
        productId=title.value if derived_id else product_id,
        images=[ImageInfo(image_id, image_path.name, width, height)],
        evidence=evidence, facts=facts,
        unassignedEvidenceIds=[e.id for e in evidence if e.id not in used],
        productIdSource="ocr_front_title" if derived_id else "provided" if product_id is not None else "unknown",
    )
