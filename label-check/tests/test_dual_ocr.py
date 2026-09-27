"""Offline replay of two real Table V2 responses and the vision-to-human handoff."""

import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from src.ocr.dual import _amount_exceeds_per_100g, _box, parse_dual
from src.ocr.crop import prepare_table_image
from src.ocr.table_v2 import recognize_table_v2
from src.ocr.vision import apply_vision_candidates
from src.review.apply import apply_decisions
from src.shared import collect_review_items, gate_nutrition_table, validate_document
from src.shared.models import Basis, Evidence, FieldValue
from src.ocr.parser import _nutrient_code, _quantity


BASE = Path(__file__).parent / "results" / "market_labels_50"
FIXTURES = Path(__file__).parent / "fixtures"


def load(code):
    image = BASE / "images" / f"{code}.jpg"
    accurate = json.loads((BASE / "ocr_raw" / f"{code}.json").read_text(encoding="utf-8"))
    table = json.loads((FIXTURES / f"table_v2_{code}.json").read_text(encoding="utf-8"))
    return image, accurate, table


class DualOcrTests(unittest.TestCase):
    def test_equivalent_units_are_not_plain_mass(self):
        for raw in ("55µgRE", "450微克视黄醇当量", "11.25mg α-TE"):
            field = _quantity(Evidence("e", "front", "baidu_accurate", raw, None, 0.999))
            self.assertIsNone(field.value)
            self.assertIn("UNSUPPORTED_EQUIVALENT_UNIT", field.reviewReasons)

    def test_label_outside_v2_polygon_still_triggers_completeness_review(self):
        code = "6902083922658"
        image = BASE / "images" / f"{code}.jpg"
        accurate = json.loads((BASE / "ocr_raw" / f"{code}.json").read_text(encoding="utf-8"))
        cached = json.loads((FIXTURES / f"table_v2_cached_{code}.json").read_text(encoding="utf-8"))
        doc = parse_dual(image, accurate, cached["response"], code,
                         table_origin=tuple(cached["transform"]["origin"]),
                         table_scale=tuple(cached["transform"]["scale"]))
        self.assertTrue(any(item.status == "needs_vision" for item in doc.tableDiagnostics))
        validate_document(doc)

    def test_bilingual_aliases_do_not_swallow_multiple_rows(self):
        self.assertEqual(_nutrient_code("蛋白质/\nprotein"), "protein")
        self.assertEqual(_nutrient_code("Salt/Sel"), "salt")
        self.assertIsNone(_nutrient_code("能量\n0千焦(kJ)"))
        self.assertIsNone(_nutrient_code("脂肪\n饱和脂肪"))
        self.assertIsNone(_nutrient_code("钠钙"))

    def test_foreign_numbers_preserve_comparators_and_multiple_units(self):
        field = _quantity(Evidence("e", "front", "baidu_accurate", "6,9 g", None, 0.99))
        self.assertEqual((field.value, field.unit), ("6.9", "g"))
        for text, reason in (("<0,5g", "UNSUPPORTED_COMPARATOR"),
                             ("720kj/172kcal", "MULTIPLE_QUANTITIES")):
            field = _quantity(Evidence("e", "front", "baidu_accurate", text, None, 0.99))
            self.assertIsNone(field.value)
            self.assertIn(reason, field.reviewReasons)

    def test_crop_preserves_both_sides_of_offset_or_two_panel_tables(self):
        for code in ("6920907808612", "6922848641310", "6954767440774"):
            image = BASE / "images" / f"{code}.jpg"
            accurate = json.loads((BASE / "ocr_raw" / f"{code}.json").read_text(encoding="utf-8"))
            prepared = prepare_table_image(image, accurate)
            self.assertEqual(prepared.cropBox[0], 0)
            for item in accurate["words_result"]:
                if _nutrient_code(item["words"]):
                    location = item["location"]
                    self.assertLessEqual(location["left"] + location["width"], prepared.cropBox[2])
                    self.assertLessEqual(location["top"] + location["height"], prepared.cropBox[3])
    def test_v2_zero_recovers_review_only_rows_from_full_text(self):
        for code, minimum_rows in (("6922848641310", 5), ("6911988014924", 1),
                                   ("6954767440774", 4)):
            with self.subTest(code=code):
                image = BASE / "images" / f"{code}.jpg"
                accurate = json.loads((BASE / "ocr_raw" / f"{code}.json").read_text(encoding="utf-8"))
                doc = parse_dual(image, accurate, {"table_num": 0, "tables_result": []}, code)
                validate_document(doc)
                rows = [row for table in doc.facts.nutritionTables for row in table.rows]
                self.assertGreaterEqual(len(rows), minimum_rows)
                self.assertTrue(all(row.nutrient.status == "needs_review" for row in rows))
                self.assertEqual(doc.tableDiagnostics[0].status, "needs_vision")

    def test_accurate_fallback_aligns_nrv_to_amount_on_skewed_photo(self):
        code = "6920907808612"
        image = BASE / "images" / f"{code}.jpg"
        accurate = json.loads((BASE / "ocr_raw" / f"{code}.json").read_text(encoding="utf-8"))
        doc = parse_dual(image, accurate, {"table_num": 0, "tables_result": []}, code)
        rows = doc.facts.nutritionTables[0].rows
        energy = next(row for row in rows if row.nutrient.value == "energy")
        protein = next(row for row in rows if row.nutrient.value == "protein")
        self.assertEqual((energy.nrvPercent.value, protein.nrvPercent.value), ("19", "5"))

    def parse_cached_v2(self, code):
        image = BASE / "images" / f"{code}.jpg"
        accurate = json.loads((BASE / "ocr_raw" / f"{code}.json").read_text(encoding="utf-8"))
        saved = json.loads((FIXTURES / f"table_v2_{code}.json").read_text(encoding="utf-8"))
        transform = saved["transform"]
        doc = parse_dual(image, accurate, saved["response"], code,
                         table_origin=tuple(transform["origin"]), table_scale=tuple(transform["scale"]))
        validate_document(doc)
        return doc

    def test_merged_nutrient_amount_cell_restores_carbohydrate_row(self):
        doc = self.parse_cached_v2("6909995103670")
        rows = doc.facts.nutritionTables[0].rows
        self.assertEqual(len(rows), 5)
        carbohydrate = next(row for row in rows if row.nutrient.value == "carbohydrate")
        self.assertEqual((carbohydrate.amount.value, carbohydrate.amount.unit,
                          carbohydrate.nrvPercent.value), ("59.0", "g", "20"))
        self.assertIn("MERGED_NUTRIENT_AMOUNT_CELL", doc.tableDiagnostics[0].message)

    def test_chinese_nrv_header_restores_percentage_column(self):
        for code, expected in (("6938866513607", ["3", "5", "6", "2", "3", "13"]),
                               ("6954767430461", ["0", "0", "0", "0", None, "6", "1"])):
            with self.subTest(code=code):
                doc = self.parse_cached_v2(code)
                rows = doc.facts.nutritionTables[0].rows
                self.assertEqual([row.nrvPercent.value if row.nrvPercent else None for row in rows], expected)
                self.assertTrue(all(row.nrvPercent.status == "needs_review" for row in rows if row.nrvPercent))
                self.assertIn("NRV_RECOVERED_FROM_ACCURATE", doc.tableDiagnostics[0].message)

    def test_crop_includes_chinese_nrv_percentage_column(self):
        for code, rightmost in (("6938866513607", 2174), ("6954767430461", 2640)):
            with self.subTest(code=code):
                image = BASE / "images" / f"{code}.jpg"
                accurate = json.loads((BASE / "ocr_raw" / f"{code}.json").read_text(encoding="utf-8"))
                self.assertGreaterEqual(prepare_table_image(image, accurate).cropBox[2], rightmost)

    def test_merged_basis_nrv_header_uses_percentage_data_column(self):
        code = "6954767430461"
        image = BASE / "images" / f"{code}.jpg"
        accurate = json.loads((BASE / "ocr_raw" / f"{code}.json").read_text(encoding="utf-8"))
        saved = json.loads((FIXTURES / f"table_v2_{code}_wide.json").read_text(encoding="utf-8"))
        transform = saved["transform"]
        doc = parse_dual(image, accurate, saved["response"], code,
                         table_origin=tuple(transform["origin"]), table_scale=tuple(transform["scale"]))
        validate_document(doc)
        rows = doc.facts.nutritionTables[0].rows
        self.assertEqual([row.nrvPercent.value if row.nrvPercent else None for row in rows],
                         ["0", "0", "0", "0", None, "6", "1"])
        self.assertIn("NRV_COLUMN_INFERRED_FROM_DATA", doc.tableDiagnostics[0].message)

    def test_per_100g_amount_above_100g_requires_review(self):
        basis = Basis(kind="per_100g", raw="每100克", sourceRefs=[], status="ready")
        amount = FieldValue(raw="180克", value="180", unit="g", sourceRefs=[], status="ready")
        self.assertTrue(_amount_exceeds_per_100g(basis, amount))
        amount.value = "18.0"
        self.assertFalse(_amount_exceeds_per_100g(basis, amount))

    def test_v2_zero_table_response_may_omit_tables_result(self):
        with patch("src.ocr.table_v2._access_token", return_value="test"), patch(
            "src.ocr.table_v2._post", return_value={"table_num": 0, "log_id": 1}
        ):
            self.assertEqual(recognize_table_v2(b"image", Path("."))["tables_result"], [])

    def parse(self, code, accurate=None, table=None):
        image, saved_accurate, saved_table = load(code)
        doc = parse_dual(image, accurate or saved_accurate, table or saved_table, code)
        validate_document(doc)
        return doc

    def test_simple_table_has_six_aligned_rows_and_independent_sources(self):
        doc = self.parse("6955150411463")
        self.assertEqual([(x.code, x.status) for x in doc.tableDiagnostics], [("TABLE_PARSED", "ok")])
        table = doc.facts.nutritionTables[0]
        self.assertEqual((table.basis.kind, table.basis.status), ("per_100ml", "ready"))
        rows = {row.nutrient.value: row for row in table.rows}
        self.assertEqual(len(rows), 6)
        self.assertEqual((rows["energy"].amount.value, rows["energy"].amount.unit,
                          rows["energy"].nrvPercent.value), ("285", "kJ", "3"))
        self.assertEqual((rows["calcium"].amount.value, rows["calcium"].nrvPercent.value), ("115", "14"))
        refs = {entry.id: entry for entry in doc.evidence}
        self.assertEqual({refs[ref].sourceType for ref in rows["energy"].amount.sourceRefs},
                         {"baidu_accurate", "baidu_table_v2"})
        self.assertTrue(all(e.ocrConfidence is None for e in doc.evidence if e.sourceType == "baidu_table_v2"))
        self.assertIsNone(gate_nutrition_table(doc, table.id, "nutrition.calculate"))

    def test_shifted_table_is_blocked_for_vision_instead_of_ready(self):
        doc = self.parse("6946036500036")
        diagnostic = doc.tableDiagnostics[0]
        self.assertEqual(diagnostic.status, "needs_vision")
        self.assertIn("NUTRIENT_UNIT_MISMATCH", diagnostic.message)
        self.assertNotEqual(doc.facts.nutritionTables[0].rows[0].amount.unit, "g")
        self.assertTrue(all(row.amount.status != "ready" for row in doc.facts.nutritionTables[0].rows))
        self.assertEqual(gate_nutrition_table(doc, diagnostic.tableId, "nutrition.calculate").status, "needs_review")

    def test_v2_missing_with_nutrition_clues_creates_structural_task(self):
        image, accurate, _ = load("6955150411463")
        doc = parse_dual(image, accurate, {"table_num": 0, "tables_result": []}, "missing-table")
        validate_document(doc)
        self.assertIn(doc.tableDiagnostics[0].code, {"TABLE_V2_NOT_FOUND", "TABLE_LAYOUT_RECOVERED"})
        self.assertEqual(doc.tableDiagnostics[0].status, "needs_vision")
        self.assertTrue(all(row.amount.status == "needs_review" for row in doc.facts.nutritionTables[0].rows))

    def test_table_route_can_propose_rows_when_full_text_route_is_empty(self):
        image, _, table = load("6955150411463")
        doc = parse_dual(image, {"words_result": []}, table, "v2-only")
        validate_document(doc)
        self.assertEqual(len(doc.facts.nutritionTables[0].rows), 6)
        self.assertEqual(doc.facts.nutritionTables[0].rows[0].amount.status, "needs_review")
        self.assertIn("TABLE_V2_UNCORROBORATED", doc.facts.nutritionTables[0].rows[0].amount.reviewReasons)
        empty = parse_dual(image, {"words_result": []}, {"tables_result": []}, "both-empty")
        validate_document(empty)
        self.assertEqual(empty.tableDiagnostics[0].code, "TABLE_NOT_DETECTED")
        self.assertEqual(empty.tableDiagnostics[0].status, "needs_vision")
        self.assertEqual(gate_nutrition_table(empty, "table:1", "nutrition.calculate").status,
                         "needs_review")
        expected = parse_dual(image, {"words_result": [{"words": "其他文字"}]},
                              {"tables_result": []}, "expected-table", expect_nutrition_table=True)
        validate_document(expected)
        self.assertEqual(expected.tableDiagnostics[0].code, "TABLE_NOT_DETECTED")

    def test_low_confidence_and_engine_conflict_remain_reviewable(self):
        code = "6955150411463"
        image, accurate, table = load(code)
        low = copy.deepcopy(accurate)
        next(x for x in low["words_result"] if x["words"] == "285kJ")["probability"]["average"] = 0.9599
        doc = self.parse(code, accurate=low)
        self.assertIn("OCR_LOW_CONFIDENCE", doc.facts.nutritionTables[0].rows[0].amount.reviewReasons)
        conflict = copy.deepcopy(table)
        next(x for x in conflict["tables_result"][0]["body"] if x["words"] == "285kJ")["words"] = "286kJ"
        doc = self.parse(code, table=conflict)
        amount = doc.facts.nutritionTables[0].rows[0].amount
        self.assertEqual(amount.status, "needs_review")
        self.assertIn("OCR_ENGINE_DISAGREEMENT", amount.reviewReasons)
        self.assertEqual(gate_nutrition_table(doc, "table:1", "nutrition.calculate").status, "needs_review")

    def test_crop_coordinates_return_to_original_pixels(self):
        box = _box([{"x": 2, "y": 3}, {"x": 8, "y": 9}], 500, 500, (100, 200), (2, 3))
        self.assertEqual((box.left, box.top, box.width, box.height), (104, 209, 12, 18))

    def test_m2_crop_response_keeps_original_coordinates_and_detects_merged_rows(self):
        image = Path(__file__).resolve().parents[2] / "M2-包装设计稿正面.png"
        accurate = json.loads((FIXTURES / "front_accurate.json").read_text(encoding="utf-8"))
        saved = json.loads((FIXTURES / "front_table_v2_crop.json").read_text(encoding="utf-8"))
        doc = parse_dual(image, accurate, saved["response"], "m2-front", "longjing-43g",
                         table_origin=tuple(saved["transform"]["origin"]),
                         table_scale=tuple(saved["transform"]["scale"]))
        validate_document(doc)
        self.assertEqual(doc.tableDiagnostics[0].status, "needs_vision")
        self.assertIn("V2_MISSING_NUTRIENT_ROWS", doc.tableDiagnostics[0].message)
        self.assertTrue(any(entry.text == "糖钠" for entry in doc.evidence))
        self.assertFalse(any(row.nutrient.raw == "糖钠" for row in doc.facts.nutritionTables[0].rows))
        self.assertTrue(all(e.boxPx.left > 1600 for e in doc.evidence
                            if e.sourceType == "baidu_table_v2" and e.boxPx))

    def test_visual_candidate_requires_field_and_whole_table_human_review(self):
        code = "6955150411463"
        image, accurate, table_raw = load(code)
        doc = parse_dual(image, accurate, table_raw, code)
        doc.tableDiagnostics[0].status = "needs_vision"  # Simulate a structural trigger.
        table = doc.facts.nutritionTables[0]
        proposals = {
            "schemaVersion": "1.0", "documentId": code, "imageName": image.name,
            "imageSha256": hashlib.sha256(image.read_bytes()).hexdigest(),
            "tables": [{
                "tableId": table.id,
                "basis": {"kind": table.basis.kind, "raw": table.basis.raw, "value": table.basis.kind},
                "rows": [{
                    "nutrient": {"raw": row.nutrient.raw, "value": row.nutrient.value},
                    "amount": {"raw": row.amount.raw, "value": row.amount.value, "unit": row.amount.unit},
                    "nrvPercent": {"raw": row.nrvPercent.raw, "value": row.nrvPercent.value, "unit": "%"},
                } for row in table.rows],
            }],
        }
        candidate = apply_vision_candidates(doc, image.read_bytes(), proposals)
        replacement = copy.deepcopy(proposals)
        replacement["supersedesTableIds"] = [table.id]
        replacement["replaceAllTables"] = True
        replacement["tables"][0]["tableId"] = "table:vision:replacement"
        replaced = apply_vision_candidates(doc, image.read_bytes(), replacement)
        self.assertEqual([item.id for item in replaced.facts.nutritionTables], ["table:vision:replacement"])
        self.assertTrue(all(item.status == "needs_review" for item in replaced.tableDiagnostics))
        self.assertEqual(candidate.tableDiagnostics[0].status, "needs_review")
        self.assertTrue(all(row.amount.status == "needs_review" for row in candidate.facts.nutritionTables[0].rows))
        self.assertEqual(gate_nutrition_table(candidate, table.id, "nutrition.calculate").status, "needs_review")
        queue = collect_review_items(candidate)
        decisions = {
            "reviewSchemaVersion": "1.0", "documentId": code, "productId": candidate.productId,
            "imageName": image.name, "imageSha256": proposals["imageSha256"],
            "threshold": 0.96, "reviewer": "tester", "decisions": [{
                "path": item["path"], "action": "confirm", "originalRaw": item["raw"],
                "originalValue": item["value"], "originalUnit": item["unit"],
                "manualText": item["raw"], "correctedValue": item["value"],
                "correctedUnit": item["unit"],
                "sourceEvidenceRefs": [entry["id"] for entry in item["evidence"]],
            } for item in queue if item["path"].startswith("facts.nutritionTables[0]")],
            "tableDecisions": [{"tableId": table.id, "originalCode": candidate.tableDiagnostics[0].code,
                                "sourceEvidenceRefs": candidate.tableDiagnostics[0].evidenceRefs,
                                "action": "confirm_complete"}],
        }
        with self.assertRaisesRegex(ValueError, "Confirm all table fields"):
            partial = copy.deepcopy(decisions)
            partial["decisions"] = partial["decisions"][:1]
            apply_decisions(candidate, image.read_bytes(), partial)
        reviewed = apply_decisions(candidate, image.read_bytes(), decisions)
        validate_document(reviewed)
        self.assertEqual(reviewed.tableDiagnostics[0].code, "HUMAN_REVIEWED_TABLE")
        self.assertIsNone(gate_nutrition_table(reviewed, table.id, "nutrition.calculate"))


if __name__ == "__main__":
    unittest.main()
