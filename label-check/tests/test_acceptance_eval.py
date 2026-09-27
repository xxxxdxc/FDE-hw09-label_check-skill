import json
import unittest

from src.ocr.parser import parse_accurate
from tests.acceptance_eval import (
    DEFAULT_ANNOTATIONS, _numeric_observed, _resolved_images, _table_matches, evaluate,
)


class AcceptanceEvaluationTests(unittest.TestCase):
    def test_real_annotated_sample_reports_metrics_and_sample_shortfall(self):
        report = evaluate()
        self.assertEqual(report["distinctImages"], 1)
        self.assertEqual(report["numericRecognition"]["total"], 15)
        self.assertEqual(report["numericRecognition"]["status"], "insufficient_samples")
        self.assertEqual(report["nutritionStructure"]["total"], 1)
        self.assertEqual(report["confidenceAudit"]["total"], 9)

    def test_wrong_ocr_digit_is_caught_by_both_numeric_and_structure_metrics(self):
        source = json.loads(DEFAULT_ANNOTATIONS.read_text(encoding="utf-8"))
        case = source["cases"][0]
        image = (DEFAULT_ANNOTATIONS.parent / case["image"]).resolve()
        raw_path = (DEFAULT_ANNOTATIONS.parent / case["ocrResponse"]).resolve()
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        next(item for item in raw["words_result"] if item["words"] == "822千焦(kJ)")["words"] = "823千焦(kJ)"
        document = parse_accurate(image, raw, "changed-sample", case["productId"])
        self.assertEqual(_numeric_observed(document, "energy_amount"), "823")
        self.assertEqual(_numeric_observed(document, "protein_amount"), "2.6")
        self.assertEqual(_table_matches(document, case["nutritionTable"])[0], False)

    def test_repeated_image_cannot_inflate_50_image_coverage(self):
        source = json.loads(DEFAULT_ANNOTATIONS.read_text(encoding="utf-8"))
        source["cases"].append(dict(source["cases"][0]))
        with self.assertRaisesRegex(ValueError, "Repeated copies"):
            _resolved_images(source, DEFAULT_ANNOTATIONS.parent)


if __name__ == "__main__":
    unittest.main()
