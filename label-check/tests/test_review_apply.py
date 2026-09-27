import copy
import hashlib
import json
from pathlib import Path
import unittest

from src.ocr.parser import parse_accurate
from src.review.apply import apply_decisions
from src.shared import collect_review_items, validate_document


ROOT = Path(__file__).resolve().parents[2]
IMAGE = ROOT / "M2-营养成分表局部.png"
RAW = Path(__file__).parent / "results" / "original72_accurate.json"


class ReviewApplyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.image_bytes = IMAGE.read_bytes()
        cls.raw = json.loads(RAW.read_text(encoding="utf-8"))

    def document(self, raw=None):
        return parse_accurate(IMAGE, raw or self.raw, "original72")

    def decisions(self, document, item, action="confirm", text=None, value=None, unit=None):
        return {
            "reviewSchemaVersion": "1.0", "documentId": document.documentId,
            "productId": document.productId, "imageName": IMAGE.name,
            "imageSha256": hashlib.sha256(self.image_bytes).hexdigest(),
            "threshold": 0.96, "reviewer": "tester", "reviewedAt": "2026-09-27T00:00:00Z",
            "decisions": [{
                "path": item["path"], "action": action,
                "originalRaw": item["raw"], "originalValue": item["value"],
                "originalUnit": item["unit"],
                "manualText": item["raw"] if text is None else text,
                "correctedValue": item["value"] if value is None else value,
                "correctedUnit": item["unit"] if unit is None else unit,
                "sourceEvidenceRefs": [entry["id"] for entry in item["evidence"]],
                "note": "人工核对",
            }],
        }

    def test_confirm_creates_separate_reviewed_copy_and_keeps_ocr(self):
        original = self.document()
        item = next(x for x in collect_review_items(original) if x["path"] == "facts.productName")
        reviewed = apply_decisions(original, self.image_bytes, self.decisions(original, item))
        validate_document(reviewed)
        self.assertEqual(original.facts.productName.status, "needs_review")
        self.assertEqual(reviewed.facts.productName.status, "ready")
        self.assertEqual(reviewed.facts.productName.value, item["value"])
        self.assertEqual(reviewed.evidence[-1].sourceType, "manual")
        self.assertEqual(reviewed.evidence[-1].text, item["raw"])
        self.assertEqual(reviewed.facts.productName.sourceRefs[:-1], [entry["id"] for entry in item["evidence"]])
        self.assertEqual(reviewed.facts.productName.sourceRefs[-1], reviewed.evidence[-1].id)
        original_ids = {entry.id for entry in original.evidence}
        self.assertTrue(all(entry["id"] in original_ids for entry in item["evidence"]))
        self.assertEqual(len(collect_review_items(reviewed)), len(collect_review_items(original)) - 1)

    def test_correct_low_confidence_title_sets_reviewed_product_id(self):
        raw = copy.deepcopy(self.raw)
        title = next(item for item in raw["words_result"] if item["words"] == "原味咸甜玉米片")
        title["probability"]["average"] = 0.9
        original = self.document(raw)
        title_index = next(i for i, claim in enumerate(original.facts.claims) if claim.category == "front_title")
        item = next(x for x in collect_review_items(original) if x["path"] == f"facts.claims[{title_index}].text")
        reviewed = apply_decisions(original, self.image_bytes,
                                   self.decisions(original, item, "correct", "原味咸甜玉米片", "原味咸甜玉米片"))
        self.assertEqual(reviewed.productId, "原味咸甜玉米片")
        self.assertEqual(reviewed.productIdSource, "human_reviewed_title")

    def test_mismatched_image_and_stale_source_are_rejected(self):
        original = self.document()
        item = next(x for x in collect_review_items(original) if x["path"] == "facts.productName")
        record = self.decisions(original, item)
        with self.assertRaisesRegex(ValueError, "image bytes"):
            apply_decisions(original, b"different image", record)
        record["decisions"][0]["sourceEvidenceRefs"] = []
        with self.assertRaisesRegex(ValueError, "Stale"):
            apply_decisions(original, self.image_bytes, record)


if __name__ == "__main__":
    unittest.main()
