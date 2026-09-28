"""Synthetic cases chosen around business decision boundaries, with no OCR dependency."""
import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from label_check_v2.channel import ChannelStore
from label_check_v2.engine import run


BASE = json.loads((ROOT / "examples" / "per100g_ok.json").read_text(encoding="utf-8"))


def result(rule, data, store=None):
    matches = [x for x in run(data, store)["findings"] if x["rule_id"] == rule]
    if len(matches) != 1:
        raise AssertionError("Expected one finding for {}: {}".format(rule, matches))
    return matches[0]


class NutritionBoundaryCases(unittest.TestCase):
    def fresh(self):
        return deepcopy(BASE)

    def test_full_structured_package_has_only_subjective_review(self):
        data = json.loads((ROOT / "examples" / "full_packaging.json").read_text(encoding="utf-8"))
        report = run(data)
        self.assertEqual(report["status"], "REVIEW")
        self.assertEqual([x["rule_id"] for x in report["human_queue"]], ["HUMAN-QUESTION"])
        self.assertFalse([x for x in report["findings"] if x["status"] == "FAIL"])

    def test_nrv_rounding_at_half_percent(self):
        for value, printed, expected in (("6.9", "12", "PASS"),
                                         ("6.9", "11", "FAIL"),
                                         ("6.89", "11", "PASS")):
            with self.subTest(value=value, printed=printed):
                data = self.fresh()
                data["nutrients"]["protein"].update(value=value, nrv_percent=printed)
                self.assertEqual(result("NRV-100G-PROTEIN", data)["status"], expected)

    def test_sodium_mg_g_conversion(self):
        data = self.fresh()
        data["nutrients"]["sodium"].update(value="0.1", unit="g")
        report = run(data)
        self.assertEqual(report["normalized_per_100g"]["sodium"],
                         {"value": "100.0", "unit": "mg", "basis": "per_100g"})
        self.assertEqual(result("NRV-100G-SODIUM", data)["status"], "PASS")

    def test_unknown_nrv_is_review_and_sugar_dash_is_allowed(self):
        data = self.fresh()
        data["nutrients"]["magnesium"] = {"value": "120", "unit": "mg", "nrv_percent": "15"}
        data["nutrients"]["sugar"]["nrv_percent"] = "—"
        report = run(data)
        self.assertEqual(result("NRV-UNSUPPORTED", data)["status"], "REVIEW")
        self.assertFalse(any(x["rule_id"] == "NRV-NOT-DEFINED" for x in report["findings"]))

    def test_common_mineral_nrvs(self):
        data = self.fresh()
        data["nutrients"].update({
            "calcium": {"value": "120", "unit": "mg", "nrv_percent": "15"},
            "iron": {"value": "3", "unit": "mg", "nrv_percent": "20"},
            "zinc": {"value": "2.2", "unit": "mg", "nrv_percent": "20"},
        })
        for metric in ("CALCIUM", "IRON", "ZINC"):
            self.assertEqual(result("NRV-100G-" + metric, data)["status"], "PASS")

    def test_complete_nutrition_table_missing_saturated_fat_is_review(self):
        data = self.fresh()
        data["nutrition_complete"] = True
        self.assertEqual(result("NUTRITION-MISSING-SATURATED_FAT", data)["status"], "REVIEW")

    def test_claim_thresholds_on_both_sides(self):
        cases = [
            ("low_sugar", "sugar", "5", "PASS"),
            ("low_sugar", "sugar", "5.01", "FAIL"),
            ("sugar_free", "sugar", "0.5", "PASS"),
            ("sugar_free", "sugar", "0.51", "FAIL"),
            ("high_protein", "protein", "12", "PASS"),
            ("high_protein", "protein", "11.99", "FAIL"),
            ("high_fiber", "fiber", "6", "PASS"),
            ("high_fiber", "fiber", "5.99", "FAIL"),
        ]
        for claim, nutrient, value, expected in cases:
            with self.subTest(claim=claim, value=value):
                data = self.fresh()
                data["claims"] = [claim]
                data["nutrients"][nutrient]["value"] = value
                self.assertEqual(result("CLAIM-" + claim.upper(), data)["status"], expected)

    def test_claim_missing_nutrient_is_not_checked(self):
        data = self.fresh()
        del data["nutrients"]["fiber"]
        data["claims"] = ["high_fiber"]
        self.assertEqual(result("CLAIM-HIGH_FIBER", data)["status"], "NOT_CHECKED")

    def test_energy_rounding_review_band_and_clear_error(self):
        cases = (("1537.25", "PASS"), ("1540", "REVIEW"), ("1600", "FAIL"))
        for energy, expected in cases:
            with self.subTest(energy=energy):
                data = self.fresh()
                data["nutrients"]["energy"]["value"] = energy
                self.assertEqual(result("ENERGY-ARITHMETIC", data)["status"], expected)

    def test_serving_size_zero_and_volume_basis_need_review(self):
        for basis, serving in (("per_serving", "0"), ("per_100ml", None)):
            with self.subTest(basis=basis):
                data = self.fresh()
                data["basis"] = basis
                data["serving_g"] = serving
                self.assertEqual(result("INPUT-BASIS", data)["status"], "REVIEW")

    def test_cross_position_rounded_values_get_review_not_false_alarm(self):
        data = self.fresh()
        data["statements"] = [
            {"fact_key": "protein", "value": "2.9", "unit": "g", "basis": "per_serving", "serving_g": "40"},
            {"fact_key": "protein", "value": "7.3", "unit": "g", "basis": "per_100g"},
        ]
        self.assertEqual(result("CROSS-protein", data)["status"], "REVIEW")

    def test_missing_actual_value_and_l3_subjective_question_are_queued(self):
        data = self.fresh()
        data["subjective_questions"] = ["包装颜色符合品牌调性吗？"]
        report = run(data)
        self.assertEqual(report["status"], "REVIEW")
        self.assertIn("ACTUAL-MISSING", [x["rule_id"] for x in report["human_queue"]])
        self.assertIn("HUMAN-QUESTION", [x["rule_id"] for x in report["human_queue"]])

    def test_actual_tolerances_for_core_nutrients(self):
        cases = [
            ("carbohydrate", "48", "47.9"),
            ("fiber", "2.4", "2.39"),
            ("sugar", "4.8", "4.81"),
            ("saturated_fat", "2.4", "2.41"),
        ]
        for metric, boundary, beyond in cases:
            with self.subTest(metric=metric):
                data = self.fresh()
                if metric == "saturated_fat":
                    data["nutrients"][metric] = {"value": "2", "unit": "g", "nrv_percent": "10"}
                data["actual_per_100g"] = {metric: boundary}
                self.assertEqual(result("ACTUAL-" + metric.upper(), data)["status"], "PASS")
                data["actual_per_100g"][metric] = beyond
                self.assertEqual(result("ACTUAL-" + metric.upper(), data)["status"], "FAIL")

    def test_zero_label_value_does_not_get_false_percentage_fail(self):
        data = self.fresh()
        data["nutrients"]["sugar"]["value"] = "0"
        data["actual_per_100g"] = {"sugar": "0.2"}
        self.assertEqual(result("ACTUAL-ZERO-SUGAR", data)["status"], "REVIEW")

    def test_cross_position_different_nutrients_cannot_be_compared(self):
        data = self.fresh()
        data["statements"] = [
            {"fact_key": "nutrition_fact", "metric": "protein", "value": "5", "unit": "g", "basis": "per_100g"},
            {"fact_key": "nutrition_fact", "metric": "sugar", "value": "5", "unit": "g", "basis": "per_100g"},
        ]
        self.assertEqual(result("CROSS-METRIC", data)["status"], "REVIEW")

    def test_complete_packaging_field_gap_is_review(self):
        data = self.fresh()
        data["packaging"] = {"complete": True, "fields": {
            "food_name": "玉米片", "ingredients_text": "玉米、牛奶", "net_content": "100g",
            "producer": "示例工厂", "shelf_life": "12个月", "storage": "阴凉干燥处",
        }}
        self.assertEqual(result("PACKAGE-FIELD-LICENSE_NUMBER", data)["status"], "REVIEW")
        self.assertEqual(result("PACKAGE-FIELD-FOOD_NAME", data)["status"], "PASS")

    def test_ingredient_sorting_with_two_percent_exception(self):
        data = self.fresh()
        data["packaging"] = {"ingredients": [
            {"name": "玉米", "amount_percent": "50"},
            {"name": "牛奶", "amount_percent": "10"},
            {"name": "盐", "amount_percent": "1"},
            {"name": "香辛料", "amount_percent": "1.5"},
        ], "allergen_declarations": ["milk"]}
        self.assertEqual(result("INGREDIENT-ORDER", data)["status"], "PASS")
        self.assertEqual(result("ALLERGEN-MILK", data)["status"], "PASS")
        data["packaging"]["ingredients"][1]["amount_percent"] = "55"
        self.assertEqual(result("INGREDIENT-ORDER", data)["status"], "FAIL")

    def test_allergen_candidate_requires_human_confirmation(self):
        data = self.fresh()
        data["packaging"] = {"ingredients": [{"name": "花生碎"}, {"name": "乳清蛋白"}]}
        self.assertEqual(result("ALLERGEN-PEANUT", data)["status"], "REVIEW")
        self.assertEqual(result("ALLERGEN-MILK", data)["status"], "REVIEW")
        self.assertEqual(result("INGREDIENT-ORDER", data)["status"], "NOT_CHECKED")

    def test_impossible_ingredient_percent_is_review(self):
        data = self.fresh()
        data["packaging"] = {"ingredients": [{"name": "玉米", "amount_percent": "120"}]}
        self.assertEqual(result("INGREDIENT-AMOUNT", data)["status"], "REVIEW")


class ChannelBoundaryCases(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ChannelStore(Path(self.tmp.name) / "cards.sqlite")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_forbidden_term_and_missing_label_text(self):
        card = self.store.submit_card("渠道A", "禁用某表达", "forbid", "绝对安全", "函件-001")
        self.store.review_card(card, "confirmed", "质量经理")
        for text, expected in (("", "REVIEW"), ("普通包装", "PASS"),
                               ("绝对安全包装", "FAIL")):
            with self.subTest(text=text):
                self.assertEqual(self.store.evaluate("渠道A", label_text=text)[0]["status"], expected)

    def test_document_and_manual_requirements(self):
        doc = self.store.submit_card("渠道A", "附带证明文件", "document", "证书A", "协议-001")
        manual = self.store.submit_card("渠道A", "颜色由渠道确认", "manual", "颜色", "协议-002")
        self.store.review_card(doc, "confirmed", "质量经理")
        self.store.review_card(manual, "confirmed", "质量经理")
        findings = self.store.evaluate("渠道A", documents=["证书A"])
        status = {x["rule_id"]: x["status"] for x in findings}
        self.assertEqual(status["CHANNEL-" + str(doc)], "PASS")
        self.assertEqual(status["CHANNEL-" + str(manual)], "REVIEW")

    def test_rejected_card_cannot_be_enforced(self):
        card = self.store.submit_card("渠道A", "一次退回经验", "contains", "旧标识", "退回记录-001")
        self.store.review_card(card, "rejected", "质量经理")
        findings = self.store.evaluate("渠道A", label_text="新版包装")
        self.assertFalse(any(x["rule_id"] == "CHANNEL-" + str(card) for x in findings))
        self.assertTrue(any(x["rule_id"] == "CHANNEL-NO-CONFIRMED-RULES" for x in findings))

    def test_confirmed_card_respects_sku_and_date(self):
        card = self.store.submit_card("渠道A", "专供字样", "contains", "专供", "协议-003",
                                      sku="SKU-01", valid_from="2026-01-01", valid_to="2026-12-31")
        self.store.review_card(card, "confirmed", "质量经理")
        for sku, date, should_apply in (("SKU-01", "2026-06-01", True),
                                        ("SKU-02", "2026-06-01", False),
                                        ("SKU-01", "2027-01-01", False)):
            with self.subTest(sku=sku, date=date):
                findings = self.store.evaluate("渠道A", sku=sku, on_date=date, label_text="普通包装")
                self.assertEqual(any(x["rule_id"] == "CHANNEL-" + str(card) for x in findings), should_apply)


if __name__ == "__main__":
    unittest.main()
