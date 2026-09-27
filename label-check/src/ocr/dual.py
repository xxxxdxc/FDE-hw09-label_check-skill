"""Combine line OCR and Table V2 without treating V2 cells as verified facts."""

from __future__ import annotations

import re
from statistics import median
from decimal import Decimal, InvalidOperation
from pathlib import Path

from src.shared.confidence import LOW_OCR_CONFIDENCE
from src.shared.models import Basis, BoxPx, Evidence, Facts, FieldValue, ImageInfo, LabelDocument, NutritionRow, NutritionTable, TableDiagnostic
from .images import image_size
from .parser import NUMBER_UNIT, NUTRIENTS, UNIT_NAMES, _basis, _field, _nutrient_code, _quantity, parse_accurate


def _amount_exceeds_per_100g(basis: Basis, amount: FieldValue) -> bool:
    """Catch OCR-lost decimal points without treating this as a compliance rule."""
    if basis.kind != "per_100g" or amount.unit != "g" or amount.value is None:
        return False
    try:
        return Decimal(amount.value) > Decimal("100")
    except (InvalidOperation, TypeError):
        return False


def _box(points: list[dict], width: int, height: int, origin: tuple[int, int],
         scale: tuple[float, float]) -> BoxPx | None:
    try:
        xs = [round(float(point["x"]) * scale[0] + origin[0]) for point in points]
        ys = [round(float(point["y"]) * scale[1] + origin[1]) for point in points]
        left, top, right, bottom = min(xs), min(ys), max(xs), max(ys)
        if left < 0 or top < 0 or right > width or bottom > height or right <= left or bottom <= top:
            return None
        return BoxPx(left, top, right - left, bottom - top)
    except (KeyError, TypeError, ValueError):
        return None


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold().replace("（", "(").replace("）", ")")


def _is_nrv_header(text: str) -> bool:
    return "NRV" in text.upper() or "营养素参考值" in re.sub(r"\s+", "", text)


def _split_nutrient_amount(text: str) -> tuple[str, str, str, str] | None:
    """Split one OCR cell only when a known nutrient is followed by one whole quantity."""
    clean = re.sub(r"^[\s\-—－·一]+", "", text).strip()
    for name in sorted(NUTRIENTS, key=len, reverse=True):
        if clean.startswith(name):
            remainder = clean[len(name):].strip()
            match = NUMBER_UNIT.fullmatch(remainder)
            if match:
                unit = UNIT_NAMES.get(match.group("unit")) or UNIT_NAMES.get(match.group("unit").lower())
                if unit:
                    return name, NUTRIENTS[name], match.group("number"), unit
    return None


def _accurate_nrv_header(accurate: list[Evidence], headers: dict[int, Evidence], amount_col: int) -> Evidence | None:
    positions = [cell.boxPx for cell in headers.values() if cell.boxPx]
    amount_box = headers.get(amount_col).boxPx if headers.get(amount_col) else None
    if not positions or amount_box is None:
        return None
    header_y = sum(box.top + box.height / 2 for box in positions) / len(positions)
    header_height = max(box.height for box in positions)
    candidates = [entry for entry in accurate if entry.boxPx and _is_nrv_header(entry.text)
                  and entry.boxPx.left > amount_box.left
                  and abs(entry.boxPx.top + entry.boxPx.height / 2 - header_y) <= max(100, header_height)]
    return min(candidates, key=lambda entry: abs(entry.boxPx.top + entry.boxPx.height / 2 - header_y)) if candidates else None


def _accurate_nrv_by_row(accurate: list[Evidence], header: Evidence,
                         cells: dict[tuple[int, int], tuple[Evidence, BoxPx | None]],
                         header_row: int, label_col: int, amount_col: int) -> dict[int, list[Evidence]]:
    """Associate printed percentages with V2 row centers, never by OCR line order."""
    anchors = {}
    for row in sorted({r for r, _ in cells if r > header_row}):
        boxes = [cells[(row, col)][0].boxPx for col in (label_col, amount_col)
                 if (row, col) in cells and cells[(row, col)][0].boxPx]
        if boxes:
            anchors[row] = sum(box.top + box.height / 2 for box in boxes) / len(boxes)
    if not anchors or not header.boxPx:
        return {}
    centers = sorted(anchors.values())
    gaps = [b - a for a, b in zip(centers, centers[1:]) if b > a]
    tolerance = max(100, min(gaps) * 0.7) if gaps else max(100, header.boxPx.height * 1.5)
    assigned: dict[int, list[Evidence]] = {}
    for entry in accurate:
        box = entry.boxPx
        if not box or not re.fullmatch(r"\s*\d+(?:\.\d+)?\s*%\s*", entry.text):
            continue
        if box.left + box.width / 2 < header.boxPx.left or box.top < header.boxPx.top + header.boxPx.height:
            continue
        y = box.top + box.height / 2
        row = min(anchors, key=lambda index: abs(anchors[index] - y))
        if abs(anchors[row] - y) <= tolerance:
            assigned.setdefault(row, []).append(entry)
    return assigned


def _within(inner: BoxPx, outer: BoxPx) -> bool:
    x, y = inner.left + inner.width / 2, inner.top + inner.height / 2
    pad_x, pad_y = max(5, outer.width * 0.06), max(5, outer.height * 0.15)
    return (outer.left - pad_x <= x <= outer.left + outer.width + pad_x and
            outer.top - pad_y <= y <= outer.top + outer.height + pad_y)


def _match_accurate(cell: Evidence, cell_box: BoxPx | None, accurate: list[Evidence]) -> tuple[Evidence | None, bool]:
    if cell_box is None:
        return None, False
    nearby = [entry for entry in accurate if entry.boxPx and _within(entry.boxPx, cell_box)]
    exact = [entry for entry in nearby if _norm(entry.text) == _norm(cell.text)]
    if exact:
        exact.sort(key=lambda entry: abs(entry.boxPx.top - cell.boxPx.top) if cell.boxPx else 0)
        return exact[0], False
    return None, bool(nearby)


def _cell_field(cell: Evidence | None, cell_box: BoxPx | None, accurate: list[Evidence], kind: str) -> FieldValue:
    if cell is None or not cell.text.strip():
        return FieldValue.missing()
    matched, disagreement = _match_accurate(cell, cell_box, accurate)
    refs = [cell, matched] if matched else [cell]
    reasons = [] if matched else ["OCR_ENGINE_DISAGREEMENT" if disagreement else "TABLE_V2_UNCORROBORATED"]
    if kind == "nutrient":
        value = _nutrient_code(cell.text)
        if value is None:
            reasons.append("UNKNOWN_NUTRIENT")
        if "\n" in cell.text.strip():
            reasons.append("MULTIPLE_NUTRIENTS_IN_CELL")
        return _field(cell.text, value, None, refs, extra=reasons)
    if kind == "amount":
        amount = _quantity(cell)
        reasons.extend(amount.reviewReasons)
        if amount.value is None:
            reasons.append("NUMBER_OR_UNIT_UNREADABLE")
        if amount.unit is None:
            reasons.append("UNIT_MISSING")
        return _field(cell.text, amount.value, amount.unit, refs, numeric=True, extra=reasons)
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*%\s*", cell.text)
    if match is None:
        reasons.append("NRV_UNREADABLE")
    return _field(cell.text, match.group(1) if match else None, "%", refs, numeric=True, extra=reasons)


def _mark_uncertain(table: NutritionTable) -> None:
    if table.basis.status == "ready":
        table.basis.status = "needs_review"
    for row in table.rows:
        for field in (row.nutrient, row.amount, row.nrvPercent):
            if field is not None and field.status == "ready":
                field.status = "needs_review"
                field.reviewReasons.append("TABLE_STRUCTURE_UNCERTAIN")


def _table_clues(accurate: list[Evidence]) -> list[Evidence]:
    titles = [entry for entry in accurate if "营养成分表" in entry.text or _is_nrv_header(entry.text)]
    nutrients = [entry for entry in accurate if _nutrient_code(entry.text)]
    if titles or len(nutrients) >= 2:
        return titles or nutrients[:3]
    return []


def _bbox_union(entries: list[Evidence], width: int, height: int) -> BoxPx | None:
    boxes = [e.boxPx for e in entries if e.boxPx]
    if not boxes:
        return None
    left = max(0, min(box.left for box in boxes) - 20)
    top = max(0, min(box.top for box in boxes) - 20)
    right = min(width, max(box.left + box.width for box in boxes) + 20)
    bottom = min(height, max(box.top + box.height for box in boxes) + 20)
    return BoxPx(left, top, right - left, bottom - top)


def _recover_accurate_tables(accurate: list[Evidence], image_id: str,
                             width: int, height: int, prefix: str = "accurate") -> list[NutritionTable]:
    """Recover traceable candidate rows when Table V2 found no table.

    This is deliberately a review-only fallback: line OCR does not establish cell
    boundaries, and a plausible geometric match is not a verified table cell.
    """
    located = [entry for entry in accurate if entry.boxPx]
    headers = [entry for entry in located if "项目" in entry.text or
               re.search(r"每\s*(?:份|100)", entry.text) or _is_nrv_header(entry.text) or
               _basis([entry]).kind != "unknown"]
    titles = [entry for entry in located if "营养成分表" in entry.text or
              re.search(r"nutrition|nutnzion|nahrwerte", entry.text, re.I)]
    if not headers and not titles:
        return []
    start = min((entry.boxPx.top for entry in headers + titles), default=0)
    labels = []
    for entry in located:
        if entry.boxPx.top <= start:
            continue
        merged = _split_nutrient_amount(entry.text)
        if _nutrient_code(entry.text) or merged:
            labels.append((entry, merged))
    if not labels or (len(labels) < 2 and len(headers) < 2):
        return []

    # A package can print two adjacent nutrition panels under one title.
    ordered_x = sorted((entry.boxPx.left for entry, _ in labels))
    splits = [(left + right) / 2 for left, right in zip(ordered_x, ordered_x[1:])
              if right - left > max(300, width * 0.25)]
    boundaries = [0, *splits, width + 1]
    recovered = []
    for panel, (left, right) in enumerate(zip(boundaries, boundaries[1:]), start=1):
        group = [(entry, merged) for entry, merged in labels if left <= entry.boxPx.left < right]
        if not group:
            continue
        group.sort(key=lambda pair: pair[0].boxPx.top)
        centers = [entry.boxPx.top + entry.boxPx.height / 2 for entry, _ in group]
        gaps = [b - a for a, b in zip(centers, centers[1:]) if b > a]
        tolerance = max(30, min(130, median(gaps) * 0.48 if gaps else 50))
        panel_headers = [entry for entry in headers if left <= entry.boxPx.left < right and
                         entry.boxPx.top < group[0][0].boxPx.top]
        if len(group) < 2 and len(panel_headers) < 2:
            continue
        basis = _basis(panel_headers)
        if basis.status == "ready":
            basis.status = "needs_review"
        basis.sourceRefs = list(dict.fromkeys([*basis.sourceRefs, *(entry.id for entry in panel_headers)]))
        rows = []
        for label, merged in group:
            y = label.boxPx.top + label.boxPx.height / 2
            nearby = [entry for entry in located if entry is not label and
                      left <= entry.boxPx.left < right and
                      entry.boxPx.left >= label.boxPx.left + label.boxPx.width * 0.4 and
                      abs(entry.boxPx.top + entry.boxPx.height / 2 - y) <= tolerance]
            amounts = [entry for entry in nearby if NUMBER_UNIT.search(entry.text)]
            amounts.sort(key=lambda entry: entry.boxPx.left)
            name = merged[0] if merged else label.text
            code = merged[1] if merged else _nutrient_code(label.text)
            nutrient = _field(name, code, None, [label], extra=["LAYOUT_FALLBACK_UNVERIFIED"])
            if merged:
                amount = _field(label.text, merged[2], merged[3], [label],
                                numeric=True, extra=["LAYOUT_FALLBACK_UNVERIFIED"])
            elif len(amounts) == 1:
                source = amounts[0]
                quantity = _quantity(source)
                amount = _field(source.text, quantity.value, quantity.unit, [source], numeric=True,
                                extra=["LAYOUT_FALLBACK_UNVERIFIED", *quantity.reviewReasons])
            else:
                amount = FieldValue.missing()
                if amounts:
                    amount.reviewReasons = ["AMBIGUOUS_ROW", "LAYOUT_FALLBACK_UNVERIFIED"]
            nrv = None
            # On skewed photos the three columns can have a systematic vertical
            # offset. Match the NRV against its amount, then require uniqueness.
            if len(amounts) == 1:
                amount_source = amounts[0]
                amount_y = amount_source.boxPx.top + amount_source.boxPx.height / 2
                if re.search(r"\d+(?:\.\d+)?\s*%", amount_source.text):
                    nrvs = [amount_source]
                else:
                    nrvs = [entry for entry in located if entry is not amount_source and
                            left <= entry.boxPx.left < right and
                            entry.boxPx.left > amount_source.boxPx.left and
                            re.search(r"\d+(?:\.\d+)?\s*%", entry.text) and
                            abs(entry.boxPx.top + entry.boxPx.height / 2 - amount_y) <= tolerance]
            else:
                nrvs = []
            if len(nrvs) == 1:
                source = nrvs[0]
                match = re.search(r"(\d+(?:\.\d+)?)\s*%", source.text)
                nrv = _field(source.text, match.group(1), "%", [source], numeric=True,
                             extra=["LAYOUT_FALLBACK_UNVERIFIED"])
            rows.append(NutritionRow(nutrient, amount, nrv))
        refs = list(dict.fromkeys([*(entry.id for entry in titles),
                                   *(entry.id for entry in panel_headers)]))
        for role in ("amount", "nrvPercent"):
            uses = {}
            for row in rows:
                field = getattr(row, role)
                if field:
                    for ref in field.sourceRefs:
                        uses.setdefault(ref, []).append(field)
            for fields in uses.values():
                if len(fields) > 1:
                    for field in fields:
                        field.value = None
                        field.status = "needs_review"
                        field.reviewReasons.append("AMBIGUOUS_ROW")
        recovered.append(NutritionTable(f"table:{prefix}:{panel}", basis, rows, refs))
    return recovered


def _v2_content_evidence(table_raw: dict, image_id: str, width: int, height: int,
                         origin: tuple[int, int], scale: tuple[float, float]) -> list[Evidence]:
    """Keep individual lines inside merged V2 cells, with their actual polygons."""
    entries = []
    for table_index, table in enumerate(table_raw.get("tables_result", []), start=1):
        for cell_index, cell in enumerate(table.get("body", []), start=1):
            for part_index, part in enumerate(cell.get("contents", []), start=1):
                text = str(part.get("word", "")).strip()
                box = _box(part.get("poly_location") or [], width, height, origin, scale)
                if text and box:
                    entries.append(Evidence(f"{image_id}:v2part:{table_index}:{cell_index}:{part_index}",
                                            image_id, "baidu_table_v2", text, box, None))
    return entries


def _layout_score(tables: list[NutritionTable]) -> tuple[int, int]:
    rows = [row for table in tables for row in table.rows if row.nutrient.value]
    return (len({row.nutrient.value for row in rows}),
            sum(row.amount.value is not None and row.amount.unit is not None for row in rows))


def _table_from_v2(index: int, payload: dict, accurate: list[Evidence], image_id: str,
                   width: int, height: int, origin: tuple[int, int],
                   scale: tuple[float, float]) -> tuple[NutritionTable | None, list[Evidence], TableDiagnostic | None]:
    cells: dict[tuple[int, int], tuple[Evidence, BoxPx | None]] = {}
    all_evidence = []
    spanning_rows: set[int] = set()
    has_duplicate_positions = False
    for sequence, item in enumerate(payload.get("body", []), start=1):
        word = str(item.get("words", "")).strip()
        if not word:
            continue
        row, col = item.get("row_start"), item.get("col_start")
        if not isinstance(row, int) or not isinstance(col, int):
            continue
        if item.get("row_end") != row + 1 or item.get("col_end") != col + 1:
            spanning_rows.add(row)
        cell_box = _box(item.get("cell_location") or [], width, height, origin, scale)
        contents = item.get("contents") or []
        text_boxes = [_box(part.get("poly_location") or [], width, height, origin, scale) for part in contents]
        text_boxes = [box for box in text_boxes if box is not None]
        if text_boxes:
            left = min(box.left for box in text_boxes)
            top = min(box.top for box in text_boxes)
            right = max(box.left + box.width for box in text_boxes)
            bottom = max(box.top + box.height for box in text_boxes)
            text_box = BoxPx(left, top, right - left, bottom - top)
        else:
            text_box = cell_box
        evidence = Evidence(f"{image_id}:v2:{index}:{sequence}", image_id, "baidu_table_v2", word, text_box, None)
        if (row, col) in cells:
            has_duplicate_positions = True
        cells[(row, col)] = (evidence, cell_box)
        all_evidence.append(evidence)
    table_box = _box(payload.get("table_location") or [], width, height, origin, scale)
    rows = sorted({row for row, _ in cells})
    header_row = next((row for row in rows if any("项目" in cell.text or _is_nrv_header(cell.text) or
                                                  _basis([cell]).kind != "unknown"
                                                  for (r, _), (cell, _) in cells.items() if r == row)), None)
    nutrient_count = sum(bool(_nutrient_code(cell.text)) for (row, _), (cell, _) in cells.items()
                         if row != header_row)
    if header_row is None and nutrient_count < 2:
        return None, all_evidence, None  # Another kind of table, not a nutrition table.
    issues: list[str] = []
    if has_duplicate_positions:
        issues.append("DUPLICATE_CELL_POSITION")
    if header_row is None:
        issues.append("HEADER_NOT_FOUND")
        header_row = min(rows, default=0) - 1
    headers = {col: cell for (row, col), (cell, _) in cells.items() if row == header_row}
    label_col = next((col for col, cell in headers.items() if "项目" in cell.text), min(headers, default=0))
    basis_col = next((col for col, cell in headers.items() if re.search(r"每\s*(?:份|100)", cell.text) or
                      _basis([cell]).kind != "unknown"), None)
    amount_col = basis_col if basis_col is not None else label_col + 1
    nrv_col = next((col for col, cell in headers.items() if col > amount_col and _is_nrv_header(cell.text)), None)
    foreign_reference = any(re.search(r"\bRI\b|reference intake", cell.text, re.I) for cell in headers.values())
    if foreign_reference:
        issues.append("REFERENCE_BASIS_UNSUPPORTED")
    if nrv_col is None and not foreign_reference:
        percent_cols = {col for (row, col), (cell, _) in cells.items()
                        if row > header_row and col > amount_col
                        and re.fullmatch(r"\s*\d+(?:\.\d+)?\s*%\s*", cell.text)}
        if len(percent_cols) == 1:
            nrv_col = percent_cols.pop()
            issues.append("NRV_COLUMN_INFERRED_FROM_DATA")
    accurate_nrv_header = _accurate_nrv_header(accurate, headers, amount_col)
    accurate_nrv_rows = (_accurate_nrv_by_row(accurate, accurate_nrv_header, cells,
                         header_row, label_col, amount_col) if accurate_nrv_header else {})
    if basis_col is None:
        issues.append("BASIS_UNKNOWN")
    if nrv_col is None and any("%" in cell.text for (row, col), (cell, _) in cells.items()
                               if row > header_row and col > amount_col):
        issues.append("NRV_COLUMN_UNDETECTED")
    if nrv_col is None and accurate_nrv_header:
        issues.append("NRV_COLUMN_MISSING_IN_V2")
    if any(NUMBER_UNIT.search(cell.text) or re.search(r"\d+\s*%", cell.text)
           for col, cell in headers.items() if col != label_col and col != basis_col and col != nrv_col):
        issues.append("HEADER_DATA_MIXED")
    if any(NUMBER_UNIT.search(cell.text) for col, cell in headers.items()
           if col == amount_col and not re.search(r"每\s*(?:份|100)", cell.text)):
        issues.append("HEADER_DATA_MIXED")
    if nrv_col in headers and re.search(r"\d+\s*%", headers[nrv_col].text.replace("NRV%", ""), re.I):
        issues.append("HEADER_DATA_MIXED")
    basis_cell = headers.get(basis_col) if basis_col is not None else None
    basis_match = None
    if basis_cell:
        basis_match, _ = _match_accurate(basis_cell, cells[(header_row, basis_col)][1], accurate)
    basis = _basis([e for e in (basis_cell, basis_match) if e is not None])
    if basis.servingSize is not None:
        if basis_match:
            basis.servingSize.sourceRefs.append(basis_match.id)
            if basis_match.ocrConfidence is None or basis_match.ocrConfidence < LOW_OCR_CONFIDENCE:
                basis.servingSize.status = "needs_review"
                basis.servingSize.reviewReasons.append("OCR_LOW_CONFIDENCE" if basis_match.ocrConfidence is not None
                                                       else "OCR_CONFIDENCE_MISSING")
        else:
            basis.servingSize.status = "needs_review"
            basis.servingSize.reviewReasons.append("TABLE_V2_UNCORROBORATED")
    if basis.kind == "unknown":
        issues.append("BASIS_UNKNOWN")
    if basis_cell and basis_match is None:
        basis.status = "needs_review"
    header_refs = [cell.id for cell in headers.values()]
    if accurate_nrv_header:
        header_refs.append(accurate_nrv_header.id)
    for cell in headers.values():
        match, _ = _match_accurate(cell, cells[(header_row, next(col for col, h in headers.items() if h is cell))][1], accurate)
        if match:
            header_refs.append(match.id)
    basis.sourceRefs = list(dict.fromkeys([*basis.sourceRefs, *header_refs]))
    if any(e.ocrConfidence is None or e.ocrConfidence < LOW_OCR_CONFIDENCE
           for e in accurate if e.id in basis.sourceRefs):
        basis.status = "needs_review"
    if not basis.sourceRefs or not any(e.id in basis.sourceRefs for e in accurate):
        basis.status = "needs_review"
    data_rows: list[NutritionRow] = []
    data_row_indexes: set[int] = set()
    seen_codes = set()
    for row_index in rows:
        if row_index <= header_row:
            continue
        label_pair = cells.get((row_index, label_col))
        amount_pair = cells.get((row_index, amount_col))
        nrv_pair = cells.get((row_index, nrv_col)) if nrv_col is not None else None
        label = label_pair[0] if label_pair else None
        amount = amount_pair[0] if amount_pair else None
        merged = _split_nutrient_amount(label.text) if label and not amount else None
        if not label or (not _nutrient_code(label.text) and not amount and not merged):
            continue
        data_row_indexes.add(row_index)
        if merged:
            name, code, number, unit = merged
            matched, disagreement = _match_accurate(label, label_pair[1], accurate)
            refs = [label, matched] if matched else [label]
            reasons = [] if matched else ["OCR_ENGINE_DISAGREEMENT" if disagreement else "TABLE_V2_UNCORROBORATED"]
            nutrient = _field(name, code, None, refs, extra=reasons)
            quantity = _field(label.text[len(name):].strip(), number, unit, refs, numeric=True, extra=reasons)
            issues.append("MERGED_NUTRIENT_AMOUNT_CELL")
        else:
            nutrient = _cell_field(label, label_pair[1], accurate, "nutrient")
            quantity = _cell_field(amount, amount_pair[1] if amount_pair else None, accurate, "amount")
        nrv = _cell_field(nrv_pair[0], nrv_pair[1], accurate, "nrv") if nrv_pair else None
        if nrv is None and accurate_nrv_rows.get(row_index):
            candidates = accurate_nrv_rows[row_index]
            if len(candidates) == 1:
                entry = candidates[0]
                nrv = _field(entry.text, re.search(r"\d+(?:\.\d+)?", entry.text).group(), "%",
                             [entry], numeric=True, extra=["TABLE_V2_UNCORROBORATED"])
                issues.append("NRV_RECOVERED_FROM_ACCURATE")
            else:
                issues.append("NRV_ROW_AMBIGUOUS")
        if nrv is not None and nrv.value is None:
            issues.append("NRV_UNREADABLE")
        data_rows.append(NutritionRow(nutrient, quantity, nrv))
        if nutrient.value is None or "\n" in label.text:
            issues.append("NUTRIENT_ROW_AMBIGUOUS")
        elif nutrient.value in seen_codes:
            issues.append("DUPLICATE_NUTRIENT")
        else:
            seen_codes.add(nutrient.value)
        if quantity.value is None or quantity.unit is None:
            issues.append("AMOUNT_MISSING_OR_UNREADABLE")
        if ((nutrient.value == "energy" and quantity.unit not in {"kJ", "kcal"}) or
            (nutrient.value in {"protein", "fat", "saturated_fat", "trans_fat", "carbohydrate", "sugar", "dietary_fiber"}
             and quantity.unit != "g") or
            (nutrient.value in {"sodium", "calcium"} and quantity.unit not in {"mg", "g"})):
            issues.append("NUTRIENT_UNIT_MISMATCH")
        if _amount_exceeds_per_100g(basis, quantity):
            issues.append("AMOUNT_EXCEEDS_100G_BASIS")
    if not data_rows:
        issues.append("ZERO_NUTRITION_ROWS")
    if spanning_rows & data_row_indexes:
        issues.append("CELL_SPAN_UNSUPPORTED")
    if table_box:
        visible_codes = {_nutrient_code(entry.text) or
                         ((_split_nutrient_amount(entry.text) or (None, None))[1])
                         for entry in accurate if entry.boxPx and _within(entry.boxPx, table_box)}
        visible_codes.discard(None)
        if visible_codes - seen_codes:
            issues.append("V2_MISSING_NUTRIENT_ROWS")
    table = NutritionTable(f"table:{index}", basis, data_rows, list(dict.fromkeys(header_refs)))
    if issues:
        _mark_uncertain(table)
    diagnostic = TableDiagnostic(
        code="TABLE_STRUCTURE_UNCERTAIN" if issues else "TABLE_PARSED",
        status="needs_vision" if issues else "ok",
        message="; ".join(dict.fromkeys(issues)) if issues else f"Table V2 parsed {len(data_rows)} rows",
        imageId=image_id, boxPx=table_box or _bbox_union(all_evidence, width, height),
        evidenceRefs=[e.id for e in all_evidence], tableId=table.id,
    )
    return table, all_evidence, diagnostic


def parse_dual(image_path: Path, accurate_raw: dict, table_raw: dict, document_id: str,
               product_id: str | None = None, image_id: str = "front",
               table_origin: tuple[int, int] = (0, 0),
               table_scale: tuple[float, float] = (1, 1),
               expect_nutrition_table: bool = False) -> LabelDocument:
    """Use V2 cells for candidate rows, with accurate OCR as a confidence-bearing cross-check."""
    if accurate_raw.get("words_result"):
        document = parse_accurate(image_path, accurate_raw, document_id, product_id, image_id)
    else:
        width, height = image_size(image_path)
        document = LabelDocument(
            "1.0", document_id, product_id, [ImageInfo(image_id, image_path.name, width, height)],
            [], Facts(), [], "provided" if product_id else "unknown",
        )
    width, height = document.images[0].widthPx, document.images[0].heightPx
    accurate = [e for e in document.evidence if e.sourceType == "baidu_accurate"]
    content_evidence = _v2_content_evidence(table_raw, image_id, width, height, table_origin, table_scale)
    document.evidence.extend(content_evidence)
    v2_tables = []
    diagnostics = []
    for index, payload in enumerate(table_raw.get("tables_result", []), start=1):
        table, evidence, diagnostic = _table_from_v2(index, payload, accurate, image_id, width, height,
                                                     table_origin, table_scale)
        document.evidence.extend(evidence)
        if table:
            v2_tables.append(table)
        if diagnostic:
            diagnostics.append(diagnostic)
    if v2_tables and not any(row.nutrient.value is not None for table in v2_tables for row in table.rows):
        for diagnostic in diagnostics:
            diagnostic.tableId = None
        v2_tables = []
    legacy_table_count = len(document.facts.nutritionTables)
    if v2_tables:
        document.facts.nutritionTables = v2_tables
        if legacy_table_count > len(v2_tables):
            diagnostics.append(TableDiagnostic(
                "TABLE_COUNT_MISMATCH", "needs_vision",
                f"Full-text OCR indicated {legacy_table_count} tables but V2 structured {len(v2_tables)}",
                image_id, BoxPx(0, 0, width, height),
                [ref for table in v2_tables for ref in table.sourceRefs],
            ))
    else:
        if not document.facts.nutritionTables:
            document.facts.nutritionTables = _recover_accurate_tables(accurate, image_id, width, height)
    if not v2_tables and (_table_clues(accurate) or document.facts.nutritionTables):
        clues = _table_clues(accurate)
        diagnostics.append(TableDiagnostic(
            "TABLE_V2_NOT_FOUND", "needs_vision",
            f"Table V2 returned no usable table; full-text OCR recovered "
            f"{sum(len(table.rows) for table in document.facts.nutritionTables)} review-only candidate rows",
            image_id, _bbox_union(clues, width, height), [e.id for e in clues],
        ))
        for table in document.facts.nutritionTables:
            _mark_uncertain(table)
    elif not v2_tables and any("功效成分" in entry.text for entry in accurate):
        clues = [entry for entry in accurate if "功效成分" in entry.text]
        diagnostics.append(TableDiagnostic(
            "NON_NUTRITION_TABLE", "needs_review",
            "Image describes functional ingredients, not a nutrition facts table; confirm sample applicability",
            image_id, _bbox_union(clues, width, height), [entry.id for entry in clues],
        ))
    elif not v2_tables and (expect_nutrition_table or not accurate):
        diagnostics.append(TableDiagnostic(
            "TABLE_NOT_DETECTED", "needs_vision",
            "No nutrition table was structured; inspect the original image and its sample applicability",
            image_id, BoxPx(0, 0, width, height), [],
        ))
    if v2_tables and all(not table.rows for table in v2_tables):
        for diagnostic in diagnostics:
            diagnostic.status = "needs_vision"
    # Audit full-image labels as well: the V2 polygon itself may omit a row.
    parsed_codes = {row.nutrient.value for table in document.facts.nutritionTables for row in table.rows}
    omitted = [entry for entry in accurate if _nutrient_code(entry.text)
               and _nutrient_code(entry.text) not in parsed_codes]
    if document.facts.nutritionTables and omitted:
        diagnostics.append(TableDiagnostic(
            "FULLTEXT_NUTRIENT_OMITTED", "needs_vision",
            "Full-image nutrient labels absent from structured rows: " + ", ".join(e.text for e in omitted),
            image_id, BoxPx(0, 0, width, height), [e.id for e in omitted],
        ))
        for table in document.facts.nutritionTables:
            _mark_uncertain(table)
    if any(item.status == "needs_vision" for item in diagnostics):
        candidates = [
            _recover_accurate_tables(accurate, image_id, width, height),
            _recover_accurate_tables(content_evidence, image_id, width, height, "v2_parts"),
        ]
        best = max(candidates, key=_layout_score)
        if _layout_score(best) > _layout_score(document.facts.nutritionTables):
            previous = "; ".join(item.message for item in diagnostics)
            document.facts.nutritionTables = best
            refs = list(dict.fromkeys([
                *(ref for table in best for ref in table.sourceRefs),
                *(ref for table in best for row in table.rows for field in
                  (row.nutrient, row.amount, row.nrvPercent) if field for ref in field.sourceRefs),
            ]))
            evidence = [entry for entry in document.evidence if entry.id in refs]
            diagnostics = [TableDiagnostic(
                "TABLE_LAYOUT_RECOVERED", "needs_vision",
                f"Recovered {sum(len(table.rows) for table in best)} review-only rows from line positions; " + previous,
                image_id, _bbox_union(evidence, width, height), refs,
            )]
    document.tableDiagnostics = diagnostics
    used = set()
    for field in (document.facts.productName, document.facts.netContent,
                  document.facts.ingredientsText, document.facts.allergenText):
        used.update(field.sourceRefs)
    for table in document.facts.nutritionTables:
        used.update(table.sourceRefs)
        used.update(table.basis.sourceRefs)
        if table.basis.servingSize:
            used.update(table.basis.servingSize.sourceRefs)
        for row in table.rows:
            for field in (row.nutrient, row.amount, row.nrvPercent):
                if field:
                    used.update(field.sourceRefs)
    for claim in document.facts.claims:
        used.update(claim.text.sourceRefs)
        if claim.quantity:
            used.update(claim.quantity.sourceRefs)
    for barcode in document.facts.barcodes:
        used.update(barcode.sourceRefs)
    document.unassignedEvidenceIds = [entry.id for entry in document.evidence if entry.id not in used]
    return document
