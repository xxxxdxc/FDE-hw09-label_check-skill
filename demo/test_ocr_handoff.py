"""Integration checks for cached OCR -> human decision -> deterministic rules."""
import hashlib
import unittest

from ocr_data import apply_review, catalog, checker_input, image_path, load_document
from src.shared.confidence import LOW_OCR_CONFIDENCE
from src.shared.review import collect_review_items
from src.shared.serde import document_from_dict


VISION_SERVING = "6920907808612"


def simulated_decisions(code, all_fields=True):
    """Synthetic confirmations exercise the handoff; they are never saved as review."""
    doc = load_document(code)
    items = collect_review_items(document_from_dict(doc))
    if not all_fields:
        items = items[:1]
    return {
        "reviewSchemaVersion": "1.0", "documentId": doc["documentId"],
        "productId": doc["productId"], "imageName": doc["images"][0]["fileName"],
        "imageSha256": hashlib.sha256(image_path(code).read_bytes()).hexdigest(),
        "threshold": LOW_OCR_CONFIDENCE, "reviewer": "integration-test-only",
        "decisions": [{
            "path": item["path"], "action": "confirm", "originalRaw": item["raw"],
            "originalValue": item["value"], "originalUnit": item["unit"],
            "manualText": item["raw"], "correctedValue": item["value"],
            "correctedUnit": item["unit"],
            "sourceEvidenceRefs": [entry["id"] for entry in item["evidence"]],
        } for item in items],
        "tableDecisions": ([{
            "tableId": item["tableId"], "originalCode": item["code"],
            "sourceEvidenceRefs": item["evidenceRefs"], "action": "confirm_complete",
        } for item in doc["tableDiagnostics"] if item["status"] == "needs_review" and item.get("tableId")]
                           if all_fields else []),
    }


class OcrHandoffTests(unittest.TestCase):
    def test_catalog_and_unreviewed_vision_gate(self):
        items = catalog()
        self.assertEqual(len(items), 50)
        self.assertEqual(sum(item["visionUsed"] for item in items), 40)
        payload, gate = checker_input(VISION_SERVING)
        self.assertIsNone(payload)
        self.assertEqual(gate["status"], "needs_review")

    def test_partial_review_stays_blocked(self):
        result = apply_review(VISION_SERVING, simulated_decisions(VISION_SERVING, all_fields=False))
        self.assertEqual(result["gate"]["status"], "needs_review")
        self.assertIsNone(result["report"])

    def test_complete_review_connects_to_per_100g_calculation(self):
        result = apply_review(VISION_SERVING, simulated_decisions(VISION_SERVING))
        self.assertEqual(result["gate"]["status"], "ready")
        self.assertEqual(result["report"]["normalized_per_100g"]["protein"]["value"], "4.0")
        self.assertTrue(any(item["rule_id"] == "NRV-100G-PROTEIN" for item in result["report"]["findings"]))

    def test_image_binding_rejects_stale_decisions(self):
        decisions = simulated_decisions(VISION_SERVING)
        decisions["imageSha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "original image bytes"):
            apply_review(VISION_SERVING, decisions)

    def test_per_100ml_is_not_guessed_as_per_100g(self):
        payload, gate = checker_input("6955150411463")
        self.assertIsNone(payload)
        self.assertEqual(gate["status"], "unsupported_basis")

    def test_chinese_gram_header_is_canonicalized_without_losing_raw_text(self):
        payload, gate = checker_input("6901668938824")
        self.assertEqual(gate["status"], "ready")
        self.assertEqual(payload["table_header"], "每100g")
        self.assertEqual(payload["table_header_raw"], "每100克")
        from label_check_v2.engine import run
        report = run(payload)
        self.assertFalse(any(item["rule_id"] == "COURSE-100G-001" for item in report["findings"]))


if __name__ == "__main__":
    unittest.main()
