import copy
import json
import os
from pathlib import Path
import unittest

from src.ocr.parser import parse_accurate
from src.shared import collect_review_items, document_from_dict, gate_check, to_dict, validate_document


ROOT = Path(__file__).resolve().parents[2]
IMAGE = Path(os.environ.get("LABEL_CHECK_SAMPLE_IMAGE", ROOT / "M2-包装设计稿正面.png"))
FIXTURE = Path(__file__).parent / "fixtures" / "front_accurate.json"
SECOND_IMAGE = ROOT / "M2-营养成分表局部.png"
SECOND_FIXTURE = Path(__file__).parent / "results" / "original72_accurate.json"


class OcrStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = json.loads(FIXTURE.read_text(encoding="utf-8-sig"))

    def parse(self, raw=None):
        document = parse_accurate(IMAGE, raw or self.raw, "m2-front", "longjing-43g")
        validate_document(document)
        return document

    def test_real_ocr_fixture_keeps_text_coordinates_and_table_mapping(self):
        document = self.parse()
        self.assertEqual(len(document.evidence), 57)
        self.assertEqual(document.images[0].widthPx, 3000)
        self.assertEqual(document.facts.productName.value, "龙井厚乳玉米片")
        self.assertEqual(document.facts.netContent.value, "43")
        self.assertEqual(len(document.facts.nutritionTables), 1)
        table = document.facts.nutritionTables[0]
        self.assertEqual((table.basis.kind, table.basis.servingSize.value), ("per_serving", "43"))
        rows = {row.nutrient.value: row for row in table.rows}
        self.assertEqual(len(rows), 8)
        self.assertEqual((rows["energy"].amount.value, rows["energy"].nrvPercent.value), ("822", "10"))
        self.assertEqual((rows["protein"].amount.value, rows["protein"].nrvPercent.value), ("2.6", "4"))
        self.assertEqual((rows["saturated_fat"].amount.value, rows["saturated_fat"].nrvPercent.value), ("4.9", "25"))
        self.assertIsNone(rows["sugar"].nrvPercent)
        self.assertEqual(rows["sugar"].nutrient.status, "ready")
        energy_evidence = next(item for item in document.evidence if item.id == rows["energy"].amount.sourceRefs[0])
        self.assertEqual((energy_evidence.boxPx.left, energy_evidence.boxPx.top,
                          energy_evidence.boxPx.width, energy_evidence.boxPx.height), (1919, 1747, 149, 39))
        self.assertNotIn("NRV%", document.facts.ingredientsText.value)
        self.assertIn("玉米糁", document.facts.ingredientsText.value)
        self.assertEqual(document.facts.ingredientsText.status, "needs_review")
        self.assertIn("OCR_TEXT_ANOMALY", document.facts.ingredientsText.reviewReasons)
        self.assertIn("乳及乳制品", document.facts.allergenText.value)
        self.assertTrue(any(c.category == "energy_per_bag" and c.quantity.value == "196" for c in document.facts.claims))
        self.assertTrue(any(c.category == "energy_per_piece" and c.quantity.value == "9" for c in document.facts.claims))
        piece = next(c for c in document.facts.claims if c.category == "energy_per_piece")
        self.assertEqual(piece.text.status, "needs_review")
        self.assertEqual(piece.quantity.status, "needs_review")
        self.assertTrue(document.unassignedEvidenceIds)

    def test_missing_unit_is_not_ready(self):
        raw = copy.deepcopy(self.raw)
        for item in raw["words_result"]:
            if item["words"] == "5.8克(g)":
                item["words"] = "5.8"
        row = next(row for row in self.parse(raw).facts.nutritionTables[0].rows if row.nutrient.value == "fat")
        self.assertEqual(row.amount.value, "5.8")
        self.assertEqual(row.amount.status, "needs_review")
        self.assertIn("UNIT_MISSING", row.amount.reviewReasons)

    def test_high_confidence_unknown_nutrient_needs_review(self):
        raw = copy.deepcopy(self.raw)
        for item in raw["words_result"]:
            if item["words"] == "蛋白质":
                item["words"] = "蛋自质"
        rows = self.parse(raw).facts.nutritionTables[0].rows
        suspicious = next(row for row in rows if row.nutrient.raw == "蛋自质")
        self.assertEqual(suspicious.nutrient.status, "needs_review")
        self.assertIn("UNKNOWN_NUTRIENT", suspicious.nutrient.reviewReasons)
        self.assertEqual(suspicious.amount.value, "2.6")

    def test_ambiguous_numeric_row_needs_review(self):
        raw = copy.deepcopy(self.raw)
        amount = next(item for item in raw["words_result"] if item["words"] == "5.8克(g)")
        extra = copy.deepcopy(amount)
        extra["words"] = "8.5克(g)"
        raw["words_result"].append(extra)
        row = next(row for row in self.parse(raw).facts.nutritionTables[0].rows if row.nutrient.value == "fat")
        self.assertEqual(row.amount.status, "needs_review")
        self.assertIn("AMBIGUOUS_ROW", row.amount.reviewReasons)

    def test_out_of_bounds_location_is_not_invented(self):
        raw = copy.deepcopy(self.raw)
        for item in raw["words_result"]:
            if item["words"] == "822千焦(kJ)":
                item["location"]["left"] = 5000
        document = self.parse(raw)
        item = next(e for e in document.evidence if e.text == "822千焦(kJ)")
        self.assertIsNone(item.boxPx)
        energy = next(row for row in document.facts.nutritionTables[0].rows if row.nutrient.value == "energy")
        self.assertEqual(energy.amount.status, "missing")

    def test_json_round_trip_and_manual_source_validation(self):
        document = self.parse()
        restored = document_from_dict(json.loads(json.dumps(to_dict(document), ensure_ascii=False)))
        validate_document(restored)
        self.assertEqual(restored.facts.nutritionTables[0].rows[0].amount.value, "822")
        data = to_dict(restored)
        data["evidence"].append({
            "id": "manual:1", "imageId": None, "sourceType": "manual",
            "text": "人工录入", "boxPx": None, "ocrConfidence": None,
        })
        validate_document(document_from_dict(data))
        data["evidence"][-1]["boxPx"] = {"left": 0, "top": 0, "width": 1, "height": 1}
        with self.assertRaises(ValueError):
            validate_document(document_from_dict(data))

    def test_low_confidence_queue_has_location_and_blocks_downstream_check(self):
        document = self.parse()
        claim = next(claim for claim in document.facts.claims if claim.category == "energy_per_piece")
        path = next(f"facts.claims[{i}].text" for i, item in enumerate(document.facts.claims)
                    if item is claim)
        item = next(item for item in collect_review_items(document) if item["path"] == path)
        self.assertEqual(item["reviewReasons"], ["OCR_LOW_CONFIDENCE"])
        self.assertEqual(item["evidence"][0]["imageId"], "front")
        self.assertIsNotNone(item["evidence"][0]["boxPx"])
        gate = gate_check("claim.energy_per_piece", {path: claim.text, "quantity": claim.quantity})
        self.assertEqual(gate.status, "needs_review")
        self.assertIn(claim.text.sourceRefs[0], gate.evidenceRefs)
        self.assertIsNone(gate_check("nutrition.energy", {"amount": document.facts.nutritionTables[0].rows[0].amount}))

    def test_missing_and_boundary_confidence(self):
        raw = copy.deepcopy(self.raw)
        name = next(item for item in raw["words_result"] if "产品名称" in item["words"])
        name["probability"]["average"] = 0.96
        self.assertEqual(self.parse(raw).facts.productName.status, "ready")
        name["probability"]["average"] = 0.9599
        self.assertIn("OCR_LOW_CONFIDENCE", self.parse(raw).facts.productName.reviewReasons)
        name.pop("probability")
        self.assertIn("OCR_CONFIDENCE_MISSING", self.parse(raw).facts.productName.reviewReasons)

    def test_low_confidence_on_one_paragraph_line_marks_whole_field(self):
        raw = copy.deepcopy(self.raw)
        item = next(item for item in raw["words_result"] if "乳及乳制品" in item["words"])
        item["probability"]["average"] = 0.90
        self.assertIn("OCR_LOW_CONFIDENCE", self.parse(raw).facts.allergenText.reviewReasons)

    def test_low_confidence_basis_is_not_ready(self):
        raw = copy.deepcopy(self.raw)
        header = next(item for item in raw["words_result"] if "每份43" in item["words"])
        header["probability"]["average"] = 0.90
        basis = self.parse(raw).facts.nutritionTables[0].basis
        self.assertEqual(basis.status, "needs_review")
        self.assertIn("OCR_LOW_CONFIDENCE", basis.servingSize.reviewReasons)
        result = gate_check("nutrition.energy", {"facts.nutritionTables[0].basis": basis})
        self.assertEqual(result.status, "needs_review")

    def test_low_confidence_table_heading_blocks_basis(self):
        raw = copy.deepcopy(self.raw)
        title = next(item for item in raw["words_result"] if "营养成分表" in item["words"])
        title["probability"]["average"] = 0.80
        document = self.parse(raw)
        basis = document.facts.nutritionTables[0].basis
        self.assertEqual(basis.status, "needs_review")
        item = next(item for item in collect_review_items(document) if item["path"] == "facts.nutritionTables[0].basis")
        self.assertIn("OCR_TABLE_HEADER_UNCERTAIN", item["reviewReasons"])

    def test_ready_field_cannot_point_to_low_confidence_ocr(self):
        data = to_dict(self.parse())
        claim = next(item for item in data["facts"]["claims"] if item["category"] == "energy_per_piece")
        claim["text"]["status"] = "ready"
        claim["text"]["reviewReasons"] = []
        with self.assertRaisesRegex(ValueError, "uncertain OCR"):
            validate_document(document_from_dict(data))

    def test_missing_input_blocks_as_insufficient_data(self):
        doc = self.parse()
        result = gate_check("net", {"facts.netContent": doc.facts.netContent, "missing": doc.facts.productName.missing()})
        self.assertEqual(result.status, "insufficient_data")

    def test_front_title_and_barcode_are_structured(self):
        document = parse_accurate(IMAGE, self.raw, "m2-front")
        validate_document(document)
        self.assertEqual(document.productId, "龙井厚乳玉米片")
        self.assertEqual(document.productIdSource, "ocr_front_title")
        title = next(claim for claim in document.facts.claims if claim.category == "front_title")
        self.assertEqual(title.text.sourceRefs, ["front:1", "front:3"])
        self.assertEqual(document.facts.barcodes[0].value, "6973029307235")
        self.assertNotIn("front:55", document.unassignedEvidenceIds)

    def test_original_72g_table_excludes_other_panels_and_keeps_all_rows(self):
        raw = json.loads(SECOND_FIXTURE.read_text(encoding="utf-8"))
        document = parse_accurate(SECOND_IMAGE, raw, "original72")
        validate_document(document)
        self.assertEqual(document.productId, "原味咸甜玉米片")
        self.assertEqual(document.productIdSource, "ocr_front_title")
        self.assertEqual(document.facts.barcodes[0].value, "6973029307563")
        rows = {row.nutrient.value: row for row in document.facts.nutritionTables[0].rows}
        self.assertEqual(len(rows), 9)
        self.assertEqual((rows["energy"].amount.value, rows["energy"].nrvPercent.value), ("1420", "17"))
        self.assertEqual((rows["protein"].amount.value, rows["protein"].nrvPercent.value), ("4.1", "7"))
        self.assertEqual((rows["fat"].amount.value, rows["fat"].nrvPercent.value), ("13.5", "22"))
        self.assertNotIn("front:60", document.unassignedEvidenceIds)
        self.assertNotIn("front:65", document.unassignedEvidenceIds)
        self.assertNotIn("front:84", document.unassignedEvidenceIds)
        self.assertIn("front:68", document.unassignedEvidenceIds)  # Green CLEAN LABEL badge, not a nutrient.
        self.assertIn("front:75", document.unassignedEvidenceIds)  # Net content on another panel.
        self.assertTrue(any("front:80" in claim.text.sourceRefs for claim in document.facts.claims))
        # The artwork note may be used for the separate 非油炸 claim, but never as a nutrient.

    def test_explicit_product_id_wins_over_ocr_title(self):
        document = self.parse()
        self.assertEqual(document.productId, "longjing-43g")
        self.assertEqual(document.productIdSource, "provided")

    def test_low_confidence_title_does_not_become_product_id(self):
        raw = json.loads(SECOND_FIXTURE.read_text(encoding="utf-8"))
        title = next(item for item in raw["words_result"] if item["words"] == "原味咸甜玉米片")
        title["probability"]["average"] = 0.90
        document = parse_accurate(SECOND_IMAGE, raw, "original72")
        validate_document(document)
        self.assertIsNone(document.productId)
        self.assertEqual(document.productIdSource, "unknown")
        self.assertEqual(next(c for c in document.facts.claims if c.category == "front_title").text.status,
                         "needs_review")

    def test_bad_barcode_check_digit_needs_review(self):
        raw = json.loads(SECOND_FIXTURE.read_text(encoding="utf-8"))
        barcode = next(item for item in raw["words_result"] if item["words"] == "973029307563")
        barcode["words"] = "973029307564"
        document = parse_accurate(SECOND_IMAGE, raw, "original72")
        validate_document(document)
        self.assertEqual(document.facts.barcodes[0].status, "needs_review")
        self.assertIn("BARCODE_CHECKSUM_INVALID", document.facts.barcodes[0].reviewReasons)


if __name__ == "__main__":
    unittest.main()
