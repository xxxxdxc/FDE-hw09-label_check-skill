import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from label_check_v2.channel import ChannelStore
from label_check_v2.core import evaluate_l1, load_policy
from label_check_v2.engine import run
from label_check_v2.retrieval import search


def example(name):
    return json.loads((ROOT / "examples" / name).read_text(encoding="utf-8"))


def by_rule(report, rule):
    return [f for f in report["findings"] if f["rule_id"] == rule]


class L1Tests(unittest.TestCase):
    def test_policy_is_versioned_and_course_rule_has_distinct_authority(self):
        p = load_policy()
        self.assertEqual(p["output_basis"], "per_100g")
        self.assertEqual(p["sources"]["course"]["authority"], "course_requirement")
        self.assertEqual(p["sources"]["nutrition"]["authority"], "national_standard")

    def test_teacher_example_rewrites_entire_row_to_per100g(self):
        report = run(example("course_40g.json"))
        self.assertEqual(report["normalized_per_100g"]["protein"]["value"], "7.25")
        self.assertEqual(by_rule(report, "COURSE-100G-001")[0]["status"], "FAIL")
        nrv = by_rule(report, "NRV-100G-PROTEIN")[0]
        self.assertEqual(nrv["details"]["expected_percent"], "12")
        self.assertEqual(nrv["status"], "REVIEW")  # source 5% is per serving, not per100g

    def test_per100g_good_values_pass_deterministic_checks(self):
        report = run(example("per100g_ok.json"))
        self.assertFalse(any(f["status"] == "FAIL" for f in report["findings"]))
        self.assertEqual(by_rule(report, "NRV-100G-PROTEIN")[0]["status"], "PASS")
        self.assertEqual(by_rule(report, "ENERGY-ARITHMETIC")[0]["status"], "PASS")
        self.assertEqual(by_rule(report, "CLAIM-LOW_SUGAR")[0]["status"], "PASS")
        self.assertEqual(by_rule(report, "ACTUAL-MISSING")[0]["status"], "NOT_CHECKED")

    def test_wrong_nrv_on_per100g_label_fails(self):
        data = example("per100g_ok.json")
        data["nutrients"]["protein"]["nrv_percent"] = "5"
        self.assertEqual(by_rule(run(data), "NRV-100G-PROTEIN")[0]["status"], "FAIL")

    def test_mg_to_g_and_kcal_to_kj(self):
        data = example("per100g_ok.json")
        data["nutrients"]["protein"] = {"value": "7250", "unit": "mg", "nrv_percent": "12"}
        data["nutrients"]["energy"] = {"value": "367.351816", "unit": "kcal"}
        report = run(data)
        self.assertEqual(float(report["normalized_per_100g"]["protein"]["value"]), 7.25)
        self.assertAlmostEqual(float(report["normalized_per_100g"]["energy"]["value"]), 1537, delta=.01)

    def test_invalid_negative_and_incompatible_units_are_not_guessed(self):
        data = example("per100g_ok.json")
        data["nutrients"]["protein"]["value"] = "-1"
        data["nutrients"]["fat"]["unit"] = "mL"
        report = run(data)
        self.assertGreaterEqual(len(by_rule(report, "INPUT-FIELD")), 2)
        self.assertEqual(by_rule(report, "ENERGY-MISSING")[0]["status"], "NOT_CHECKED")

    def test_no_density_based_ml_to_100g_conversion(self):
        data = example("per100g_ok.json")
        data["basis"] = "per_100ml"
        report = run(data)
        self.assertEqual(by_rule(report, "INPUT-BASIS")[0]["status"], "REVIEW")

    def test_sugar_blank_nrv_is_normal_but_filled_nrv_is_invalid(self):
        data = example("per100g_ok.json")
        self.assertFalse(by_rule(run(data), "NRV-NOT-DEFINED"))
        data["nutrients"]["sugar"]["nrv_percent"] = "2"
        self.assertEqual(by_rule(run(data), "NRV-NOT-DEFINED")[0]["status"], "FAIL")

    def test_low_confidence_downgrades_dependent_numeric_fail(self):
        data = example("per100g_ok.json")
        data["nutrients"]["protein"].update({"nrv_percent": "5", "confidence": 0.5})
        report = run(data)
        self.assertEqual(by_rule(report, "NRV-100G-PROTEIN")[0]["status"], "REVIEW")
        self.assertTrue(by_rule(report, "OCR-LOW-CONFIDENCE"))

    def test_actual_tolerance_boundary_and_overrun(self):
        data = example("per100g_ok.json")
        data["nutrients"]["protein"]["value"] = "10"
        data["nutrients"]["protein"].pop("nrv_percent")
        data["actual_per_100g"] = {"protein": "8"}
        self.assertEqual(by_rule(run(data), "ACTUAL-PROTEIN")[0]["status"], "PASS")
        data["actual_per_100g"]["protein"] = "7.9"
        self.assertEqual(by_rule(run(data), "ACTUAL-PROTEIN")[0]["status"], "FAIL")
        data["nutrients"]["energy"]["value"] = "1000"
        data["nutrients"]["energy"].pop("nrv_percent")
        data["actual_per_100g"] = {"energy": "1200"}
        self.assertEqual(by_rule(run(data), "ACTUAL-ENERGY")[0]["status"], "PASS")
        data["actual_per_100g"]["energy"] = "1201"
        self.assertEqual(by_rule(run(data), "ACTUAL-ENERGY")[0]["status"], "FAIL")

    def test_energy_wrong_far_beyond_rounding_is_fail(self):
        data = example("per100g_ok.json")
        data["nutrients"]["energy"]["value"] = "1100"
        self.assertEqual(by_rule(run(data), "ENERGY-ARITHMETIC")[0]["status"], "FAIL")

    def test_fiber_in_energy_is_not_double_counted(self):
        report = run(example("per100g_ok.json"))
        computed = by_rule(report, "ENERGY-ARITHMETIC")[0]["details"]["computed_kj_per_100g"]
        self.assertEqual(computed, "1537.25")

    def test_yellow_sample_high_fiber_is_candidate_problem(self):
        report = run(example("yellow_72g_transcription.json"))
        claim = by_rule(report, "CLAIM-HIGH_FIBER")[0]
        self.assertEqual(claim["status"], "FAIL")
        self.assertAlmostEqual(float(claim["details"]["value_per_100g"]), 5.69, places=2)
        self.assertEqual(by_rule(report, "COURSE-100G-001")[0]["status"], "FAIL")
        self.assertFalse(any(f["rule_id"].startswith("NRV-100G") and f["status"] == "FAIL" for f in report["findings"]))

    def test_claim_alternative_per420kj_is_supported(self):
        data = example("per100g_ok.json")
        data["nutrients"]["fiber"]["value"] = "5"
        data["nutrients"]["fiber"].pop("nrv_percent")
        data["nutrients"]["energy"]["value"] = "700"
        data["nutrients"]["energy"].pop("nrv_percent")
        data["claims"] = ["high_fiber"]
        self.assertEqual(by_rule(run(data), "CLAIM-HIGH_FIBER")[0]["status"], "PASS")

    def test_unknown_claim_routes_to_human(self):
        data = example("per100g_ok.json")
        data["claims"] = ["清洁标签"]
        report = run(data)
        self.assertEqual(by_rule(report, "CLAIM-UNKNOWN")[0]["layer"], "L3")

    def test_cross_position_same_fact_equal_after_conversion(self):
        data = example("per100g_ok.json")
        data["statements"] = [
            {"fact_key": "protein", "metric": "protein", "value": "2.9", "unit": "g", "basis": "per_serving", "serving_g": "40", "bbox": [0,0,1,1]},
            {"fact_key": "protein", "metric": "protein", "value": "7.25", "unit": "g", "basis": "per_100g", "bbox": [1,1,1,1]}
        ]
        self.assertEqual(by_rule(run(data), "CROSS-protein")[0]["status"], "PASS")

    def test_cross_position_real_conflict_marks_both_locations(self):
        data = example("per100g_ok.json")
        data["statements"] = [
            {"fact_key": "protein", "metric": "protein", "value": "2.9", "unit": "g", "basis": "per_serving", "serving_g": "40", "bbox": [0,0,1,1]},
            {"fact_key": "protein", "metric": "protein", "value": "12", "unit": "g", "basis": "per_100g", "bbox": [1,1,1,1]}
        ]
        finding = by_rule(run(data), "CROSS-protein")[0]
        self.assertEqual(finding["status"], "FAIL")
        self.assertEqual(len(finding["evidence"]), 2)

    def test_different_sku_or_ingredient_fact_is_not_compared(self):
        data = example("per100g_ok.json")
        data["statements"] = [
            {"fact_key": "corn_ingredient", "metric": "corn_ingredient", "value": "55", "unit": "g", "basis": "per_100g", "product_id": "A"},
            {"fact_key": "carbohydrate", "metric": "carbohydrate", "value": "48", "unit": "g", "basis": "per_100g", "product_id": "A"},
            {"fact_key": "protein", "metric": "protein", "value": "2", "unit": "g", "basis": "per_100g", "product_id": "A"},
            {"fact_key": "protein", "metric": "protein", "value": "12", "unit": "g", "basis": "per_100g", "product_id": "B"}
        ]
        self.assertFalse([f for f in run(data)["findings"] if f["rule_id"].startswith("CROSS-")])


class L2L3Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ChannelStore(Path(self.tmp.name) / "channels.sqlite")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_candidate_is_not_enforced_until_named_reviewer_confirms(self):
        card = self.store.submit_card("示例商超", "必须有专供标识", "contains", "专供", "邮件2026-01")
        data = example("per100g_ok.json")
        data["channel"] = "示例商超"
        data["label_text"] = "普通包装"
        before = run(data, self.store)
        self.assertFalse(any(f["layer"] == "L2" and f["status"] == "FAIL" for f in before["findings"]))
        self.assertTrue(any(f["layer"] == "L3" for f in before["findings"]))
        self.store.review_card(card, "confirmed", "质量负责人", "已对照渠道文件")
        after = run(data, self.store)
        self.assertEqual(by_rule(after, "CHANNEL-" + str(card))[0]["status"], "FAIL")
        data["label_text"] = "专供包装"
        self.assertEqual(by_rule(run(data, self.store), "CHANNEL-" + str(card))[0]["status"], "PASS")

    def test_expired_and_wrong_product_rule_do_not_apply(self):
        card = self.store.submit_card("渠道甲", "示例要求", "forbid", "禁词", "审核文件", category="坚果",
                                      valid_from="2025-01-01", valid_to="2025-12-31")
        self.store.review_card(card, "confirmed", "审核人")
        checks = self.store.evaluate("渠道甲", category="玉米片", label_text="禁词", on_date="2025-06-01")
        self.assertFalse(any(f["rule_id"] == "CHANNEL-" + str(card) for f in checks))
        checks = self.store.evaluate("渠道甲", category="坚果", label_text="禁词", on_date="2026-01-01")
        self.assertFalse(any(f["rule_id"] == "CHANNEL-" + str(card) for f in checks))

    def test_conflicting_confirmed_cards_go_to_human_not_hard_fail(self):
        ids = [
            self.store.submit_card("渠道甲", "要求出现X", "contains", "X", "文件A"),
            self.store.submit_card("渠道甲", "禁止出现X", "forbid", "X", "文件B"),
        ]
        for i in ids:
            self.store.review_card(i, "confirmed", "质量负责人")
        findings = self.store.evaluate("渠道甲", label_text="X")
        self.assertTrue(any(f["rule_id"] == "CHANNEL-CONFLICT" for f in findings))
        self.assertFalse(any(f["status"] == "FAIL" for f in findings))

    def test_missing_document_is_review_not_assumed_noncompliance(self):
        i = self.store.submit_card("渠道甲", "需要证书", "document", "证书A", "渠道文件")
        self.store.review_card(i, "confirmed", "质量负责人")
        findings = self.store.evaluate("渠道甲", documents=[])
        self.assertEqual([f for f in findings if f["rule_id"] == "CHANNEL-" + str(i)][0]["status"], "REVIEW")

    def test_human_decision_is_logged_but_not_promoted(self):
        self.store.record_human_decision("包装颜色争议", "保留蓝色", "会议纪要-脱敏", "质量负责人")
        self.assertEqual(self.store.list_cards(), [])

    def test_source_and_reviewer_are_required(self):
        with self.assertRaises(ValueError):
            self.store.submit_card("渠道甲", "要求", "contains", "标识", "")
        i = self.store.submit_card("渠道甲", "要求", "contains", "标识", "渠道文件")
        with self.assertRaises(ValueError):
            self.store.review_card(i, "confirmed", "")

    def test_search_returns_only_confirmed_cards(self):
        a = self.store.submit_card("渠道甲", "专供标识", "contains", "专供", "文档")
        self.store.submit_card("渠道乙", "专供标识", "contains", "专供", "未审文档")
        self.store.review_card(a, "confirmed", "负责人")
        self.assertEqual([x["id"] for x in self.store.search("专供标识")], [a])


class RetrievalTests(unittest.TestCase):
    def test_lexical_search_returns_traceable_rule(self):
        rows = search("每份数据统一换算为每100g", top_k=3)
        self.assertEqual(rows[0]["id"], "COURSE-100G-001")
        self.assertTrue(rows[0]["source"])


if __name__ == "__main__":
    unittest.main()
