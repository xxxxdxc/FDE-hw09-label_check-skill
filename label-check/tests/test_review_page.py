import json
from pathlib import Path
import re
import unittest

from src.review.page import render_page


ROOT = Path(__file__).resolve().parents[2]
RESULTS = Path(__file__).parent / "results"


class ReviewPageTests(unittest.TestCase):
    def test_offline_page_contains_image_boxes_and_review_fields(self):
        html, count = render_page(
            RESULTS / "original72_structured_v3.json",
            ROOT / "M2-营养成分表局部.png",
        )
        self.assertEqual(count, 4)
        self.assertIn('src="data:image/png;base64,', html)
        self.assertIn("复核人", html)
        self.assertIn("人工辨认的原图文字", html)
        self.assertIn("sourceEvidenceRefs", html)
        self.assertNotIn("fetch(", html)
        match = re.search(r'<script id="review-data" type="application/json">(.*?)</script>', html)
        self.assertIsNotNone(match)
        data = json.loads(match.group(1))
        self.assertEqual(data["threshold"], 0.96)
        self.assertEqual(len(data["items"]), 4)
        self.assertEqual(data["imageWidth"], 1338)
        self.assertEqual(data["items"][0]["path"], "facts.productName")
        self.assertTrue(all("boxPx" in evidence for item in data["items"] for evidence in item["evidence"]))


if __name__ == "__main__":
    unittest.main()
