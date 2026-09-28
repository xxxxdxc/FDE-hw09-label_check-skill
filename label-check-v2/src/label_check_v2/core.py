"""L1: deterministic nutrition and cross-position checks.

All arithmetic uses Decimal. Course formatting requirements and national-standard
requirements are reported under different rule IDs and authorities.
"""
import json
from collections import defaultdict
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, ROUND_HALF_UP
from pathlib import Path


DEFAULT_POLICY = Path(__file__).resolve().parents[2] / "knowledge" / "policy.json"
EXPECTED_UNITS = {
    "energy": "kJ", "sodium": "mg", "calcium": "mg", "iron": "mg", "zinc": "mg",
    "magnesium": "mg",
    "protein": "g", "fat": "g", "saturated_fat": "g", "carbohydrate": "g",
    "fiber": "g", "sugar": "g",
}
ALIASES = {
    "能量": "energy", "热量": "energy", "蛋白质": "protein", "脂肪": "fat",
    "饱和脂肪": "saturated_fat", "饱和脂肪酸": "saturated_fat",
    "碳水化合物": "carbohydrate", "膳食纤维": "fiber", "糖": "sugar", "钠": "sodium",
}


def load_policy(path=None):
    with open(path or DEFAULT_POLICY, encoding="utf-8") as stream:
        policy = json.load(stream)
    if policy.get("output_basis") != "per_100g":
        raise ValueError("场景09的课程输出口径必须是 per_100g")
    if policy.get("rounding") not in ("ROUND_HALF_UP", "ROUND_HALF_EVEN"):
        raise ValueError("未支持的修约方法")
    for item in policy["nrv"].values():
        if Decimal(item["value"]) <= 0:
            raise ValueError("NRV必须为正")
    return policy


def number(value, name="数值"):
    if isinstance(value, bool) or value is None:
        raise ValueError(name + "缺失或无效")
    text = str(value).strip().replace(",", "")
    if text.endswith("%"):
        text = text[:-1].strip()
    try:
        result = Decimal(text)
    except InvalidOperation:
        raise ValueError(name + "不是数字")
    if not result.is_finite() or result < 0:
        raise ValueError(name + "必须是非负有限数")
    return result


def metric_name(name):
    return ALIASES.get(name, name)


def finding(layer, status, rule_id, message, authority, evidence=None, details=None):
    return {
        "layer": layer, "status": status, "rule_id": rule_id,
        "authority": authority, "message": message,
        "evidence": evidence or [], "details": details or {},
    }


def _expected_unit(metric):
    if metric not in EXPECTED_UNITS:
        raise ValueError("营养项{}尚未纳入单位规则库".format(metric))
    return EXPECTED_UNITS[metric]


def _convert_unit(value, source, target):
    aliases = {"克": "g", "毫克": "mg", "千焦": "kJ", "千卡": "kcal", "卡": "kcal"}
    source, target = aliases.get(source, source), aliases.get(target, target)
    if source == target:
        return value
    if (source, target) == ("mg", "g"):
        return value / Decimal("1000")
    if (source, target) == ("g", "mg"):
        return value * Decimal("1000")
    if (source, target) == ("kcal", "kJ"):
        return value * Decimal("4.184")
    if (source, target) == ("kJ", "kcal"):
        return value / Decimal("4.184")
    raise ValueError("单位不能换算：{} → {}".format(source, target))


def _step(value):
    """Half of the last printed decimal place, for arithmetic uncertainty."""
    return Decimal(1).scaleb(value.as_tuple().exponent) / 2


def _factor(basis, serving_g):
    if basis == "per_100g":
        return Decimal(1)
    if basis == "per_serving":
        if serving_g is None or serving_g <= 0:
            raise ValueError("每份口径必须提供正数 serving_g")
        return Decimal(100) / serving_g
    raise ValueError("仅支持 per_100g 和 per_serving；体积不能无密度换成质量")


def normalize_item(metric, item, basis, serving_g):
    raw = number(item.get("value"), metric)
    unit = item.get("unit", _expected_unit(metric))
    expected = _expected_unit(metric)
    value = _convert_unit(raw, unit, expected)
    _factor(basis, serving_g)
    # The source precision is kept for energy-rounding review, not used to invent
    # a new nutritional value or a statutory tolerance.
    source_step = _convert_unit(_step(raw), unit, expected)
    return {
        "value": value if basis == "per_100g" else value * Decimal(100) / serving_g,
        "unit": expected,
        "half_step": source_step if basis == "per_100g" else source_step * Decimal(100) / serving_g,
        "input": item,
    }


def _ev(item):
    evidence = {}
    for key in ("source_text", "bbox", "source_file", "confidence", "source_refs", "review_status"):
        if key in item:
            evidence[key] = item[key]
    return [evidence] if evidence else []


def _round_int(value, policy):
    mode = ROUND_HALF_UP if policy["rounding"] == "ROUND_HALF_UP" else ROUND_HALF_EVEN
    return value.quantize(Decimal("1"), rounding=mode)


def evaluate_l1(data, policy=None):
    policy = policy or load_policy()
    findings = []
    basis = data.get("basis")
    try:
        serving_g = number(data["serving_g"], "serving_g") if basis == "per_serving" else None
        _factor(basis, serving_g)
    except (KeyError, ValueError) as exc:
        return {}, [finding("L1", "REVIEW", "INPUT-BASIS", str(exc), "input")]

    if basis != "per_100g" or data.get("table_header") != "每100g":
        header_uncertain = data.get("basis_confidence") is not None and number(data["basis_confidence"]) < Decimal("0.70")
        findings.append(finding(
            "L1", "REVIEW" if header_uncertain else "FAIL", "COURSE-100G-001",
            "本课程交付的营养表须明确写‘每100g’，并以该口径呈现含量和NRV%。",
            "course_requirement", details={"provided_basis": basis, "provided_header": data.get("table_header")},
        ))

    normalized = {}
    low_confidence = set()
    for raw_name, item in data.get("nutrients", {}).items():
        metric = metric_name(raw_name)
        if metric in normalized:
            findings.append(finding("L1", "REVIEW", "DUPLICATE-METRIC", metric + "重复", "input"))
            continue
        if not isinstance(item, dict):
            findings.append(finding("L1", "REVIEW", "INPUT-FIELD", metric + "不是结构化字段", "input"))
            continue
        try:
            normalized[metric] = normalize_item(metric, item, basis, serving_g)
        except ValueError as exc:
            findings.append(finding("L1", "REVIEW", "INPUT-FIELD", metric + "：" + str(exc), "input", _ev(item)))

    if data.get("nutrition_complete") is True:
        for metric in ("energy", "protein", "fat", "saturated_fat", "carbohydrate", "sugar", "sodium"):
            if metric not in normalized:
                findings.append(finding("L1", "REVIEW", "NUTRITION-MISSING-" + metric.upper(),
                                        "完整营养表未见" + metric + "，需核对原稿。", "national_standard"))

    for metric, item in normalized.items():
        source = item["input"]
        if source.get("confidence") is not None and number(source["confidence"], "confidence") < Decimal("0.70"):
            low_confidence.add(metric)
            findings.append(finding("L1", "REVIEW", "OCR-LOW-CONFIDENCE", metric + "识别分数低，依赖此字段的结论须核实。", "ocr", _ev(source)))
        if "nrv_percent" not in source:
            continue
        printed_nrv = str(source["nrv_percent"]).strip()
        if printed_nrv in ("", "-", "—", "–"):
            if metric in policy.get("nrv_not_defined", []):
                continue
            findings.append(finding("L1", "REVIEW", "NRV-BLANK",
                                    metric + "的NRV%为空或横线，需核对标签是否应填写。",
                                    "input", _ev(source)))
            continue
        if metric not in policy["nrv"]:
            if metric in policy.get("nrv_not_defined", []):
                findings.append(finding("L1", "REVIEW" if metric in low_confidence else "FAIL", "NRV-NOT-DEFINED",
                                        metric + "未规定NRV，不应填写计算所得百分比。", "national_standard", _ev(source)))
            else:
                findings.append(finding("L3", "REVIEW", "NRV-UNSUPPORTED",
                                        metric + "尚未纳入规则库，不能据此判错；须查阅标准附录。",
                                        "human_decision", _ev(source)))
            continue
        try:
            shown = number(source["nrv_percent"], "NRV%")
        except ValueError as exc:
            findings.append(finding("L1", "REVIEW", "NRV-INPUT", str(exc), "input", _ev(source)))
            continue
        reference = Decimal(policy["nrv"][metric]["value"])
        expected = _round_int(item["value"] / reference * 100, policy)
        if basis != "per_100g" or data.get("table_header") != "每100g":
            status = "REVIEW"  # the shown NRV belongs to the source serving column
            message = "{}换算到每100g后NRV%应为{}%；原每份列的{}%不能直接当每100g列使用，须整表改写。".format(metric, expected, shown)
        elif metric in low_confidence:
            status = "REVIEW"
            message = "{}的OCR读数待核实；按当前读数每100g NRV%应为{}%，图上似为{}%。".format(metric, expected, shown)
        else:
            status = "PASS" if shown == expected else "FAIL"
            message = "{}每100g NRV%应为{}%，输入为{}%。".format(metric, expected, shown)
        findings.append(finding(
            "L1", status, "NRV-100G-" + metric.upper(),
            message,
            "course_requirement+national_standard", _ev(source),
            {"value_per_100g": str(item["value"]), "reference": str(reference), "expected_percent": str(expected)},
        ))

    required = ("protein", "fat", "carbohydrate")
    if all(k in normalized for k in required):
        factors = policy["energy_factors_kj_per_g"]
        ingredients = required + (("fiber",) if "fiber" in normalized else ())
        computed = sum((normalized[k]["value"] * Decimal(factors[k]) for k in ingredients), Decimal(0))
        if "energy" in normalized:
            shown = normalized["energy"]["value"]
            uncertainty = normalized["energy"]["half_step"] + sum(
                (normalized[k]["half_step"] * Decimal(factors[k]) for k in ingredients), Decimal(0)
            )
            diff = abs(shown - computed)
            if any(k in low_confidence for k in ingredients + ("energy",)):
                status = "REVIEW"
            elif diff <= Decimal("0.5"):
                status = "PASS"
            elif diff <= uncertainty:
                status = "REVIEW"
            else:
                status = "FAIL"
            findings.append(finding(
                "L1", status, "ENERGY-ARITHMETIC",
                "每100g能量按已标示供能项算得{}kJ，标签换算值为{}kJ。".format(computed, shown),
                "national_standard", [entry for key in ("energy", *ingredients)
                                      for entry in _ev(normalized[key]["input"])],
                {"computed_kj_per_100g": str(computed), "shown_kj_per_100g": str(shown),
                 "rounding_interval_kj": str(uncertainty)},
            ))
        else:
            findings.append(finding("L1", "NOT_CHECKED", "ENERGY-MISSING", "缺少标示能量，无法核对；可计算值为{}kJ/100g。".format(computed), "input"))
    else:
        findings.append(finding("L1", "NOT_CHECKED", "ENERGY-MISSING", "缺蛋白质、脂肪或碳水，无法核对能量。", "input"))

    for claim in data.get("claims", []):
        rule = policy["claims"].get(claim)
        if rule is None:
            findings.append(finding("L3", "REVIEW", "CLAIM-UNKNOWN", "声称‘{}’未覆盖，转质量负责人判断。".format(claim), "human_decision"))
            continue
        metric = rule["nutrient"]
        if metric not in normalized:
            findings.append(finding("L1", "NOT_CHECKED", "CLAIM-" + claim.upper(), "缺{}含量，无法核对声称。".format(metric), "national_standard"))
            continue
        value = normalized[metric]["value"]
        if "min_per_100g" in rule:
            passes = value >= Decimal(rule["min_per_100g"])
            by_energy = None
            if "energy" in normalized and normalized["energy"]["value"] > 0:
                by_energy = value * Decimal(420) / normalized["energy"]["value"]
                passes = passes or by_energy >= Decimal(rule["min_per_420kj"])
            if not passes and by_energy is None:
                status = "REVIEW"  # the standard permits an energy-density route
            else:
                status = "PASS" if passes else "FAIL"
            details = {"value_per_100g": str(value), "value_per_420kj": str(by_energy) if by_energy is not None else None}
        else:
            status = "PASS" if value <= Decimal(rule["max_per_100g"]) else "FAIL"
            details = {"value_per_100g": str(value)}
        if metric in low_confidence or ("energy" in low_confidence and "min_per_420kj" in rule):
            status = "REVIEW"
        findings.append(finding("L1", status, "CLAIM-" + claim.upper(),
                                "{}声称检查：{}。".format(claim, status), "national_standard",
                                _ev(normalized[metric]["input"]), details))

    actual = data.get("actual_per_100g") or {}
    if not actual:
        findings.append(finding("L1", "NOT_CHECKED", "ACTUAL-MISSING",
                                "没有检测或可靠计算所得实际含量，允许误差项未检查。", "national_standard"))
    for raw_name, raw_value in actual.items():
        metric = metric_name(raw_name)
        rule = policy["actual_tolerance"].get(metric)
        if rule is None or metric not in normalized:
            findings.append(finding("L1", "NOT_CHECKED", "ACTUAL-UNSUPPORTED", metric + "无对应可执行比较。", "national_standard"))
            continue
        try:
            actual_value = number(raw_value, metric + "实际值")
        except ValueError as exc:
            findings.append(finding("L1", "REVIEW", "ACTUAL-INPUT", str(exc), "input"))
            continue
        if normalized[metric]["value"] == 0:
            findings.append(finding("L1", "REVIEW", "ACTUAL-ZERO-" + metric.upper(),
                                    metric + "标示为0，应按该营养项的‘0界限值’另行核对，不能直接套用百分比容差。",
                                    "national_standard", details={"actual_per_100g": str(actual_value)}))
            continue
        limit = normalized[metric]["value"] * Decimal(rule["factor"])
        passes = actual_value >= limit if rule["direction"] == "min" else actual_value <= limit
        findings.append(finding("L1", "REVIEW" if metric in low_confidence else ("PASS" if passes else "FAIL"), "ACTUAL-" + metric.upper(),
                                "{}实际值{}，界限{}。".format(metric, actual_value, limit),
                                "national_standard", details={"actual_per_100g": str(actual_value), "limit": str(limit)}))

    grouped = defaultdict(list)
    for statement in data.get("statements", []):
        if statement.get("fact_key"):
            key = (statement.get("product_id", data.get("product_id")),
                   statement.get("revision", data.get("revision")), statement["fact_key"])
            grouped[key].append(statement)
    for key, entries in grouped.items():
        if len(entries) < 2:
            continue
        metrics = {metric_name(entry.get("metric", key[2])) for entry in entries}
        if len(metrics) != 1:
            findings.append(finding("L3", "REVIEW", "CROSS-METRIC",
                                    "跨位置事实键相同但营养项不同，不能比较。", "human_decision",
                                    details={"metrics": sorted(metrics)}))
            continue
        normalized_values = []
        try:
            for entry in entries:
                metric = metric_name(entry.get("metric", key[2]))
                normalized_values.append(normalize_item(metric, entry, entry["basis"],
                                       number(entry["serving_g"]) if entry["basis"] == "per_serving" else None))
        except (KeyError, ValueError) as exc:
            findings.append(finding("L3", "REVIEW", "CROSS-INPUT", "跨位置数据不足：" + str(exc), "human_decision"))
            continue
        units = {x["unit"] for x in normalized_values}
        if len(units) != 1:
            findings.append(finding("L3", "REVIEW", "CROSS-UNIT", "跨位置单位或事实类型不同，不能直接比较。", "human_decision"))
            continue
        low = min(x["value"] - x["half_step"] for x in normalized_values)
        high = max(x["value"] + x["half_step"] for x in normalized_values)
        overlap = max(x["value"] - x["half_step"] for x in normalized_values) <= min(
            x["value"] + x["half_step"] for x in normalized_values)
        uncertain = any(number(entry.get("confidence", 1)) < Decimal("0.70") for entry in entries)
        status = "REVIEW" if uncertain else ("PASS" if len({x["value"] for x in normalized_values}) == 1 else ("REVIEW" if overlap else "FAIL"))
        evidence = [e for x in entries for e in _ev(x)]
        findings.append(finding("L1", status, "CROSS-" + str(key[2]),
                                "同SKU同版次同一事实跨位置比较：{}。".format(status),
                                "cross_position", evidence,
                                {"values_per_100g": [str(x["value"]) for x in normalized_values],
                                 "range": [str(low), str(high)]}))

    output = {}
    for metric, item in normalized.items():
        row = {"value": str(item["value"]), "unit": item["unit"], "basis": "per_100g"}
        for key in ("confidence", "source_refs", "review_status"):
            if key in item["input"]:
                row[key] = item["input"][key]
        output[metric] = row
    return output, findings
