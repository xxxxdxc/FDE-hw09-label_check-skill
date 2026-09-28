"""Structured packaging preflight. Uncertain legal facts are routed to review."""
from .core import finding, number


BASIC_FIELDS = {
    "food_name": "食品名称", "ingredients_text": "配料表", "net_content": "净含量",
    "producer": "生产者信息", "shelf_life": "保质期", "storage": "贮存条件",
    "license_number": "生产许可信息",
}

ALLERGEN_TERMS = {
    "gluten": ("小麦", "黑麦", "大麦", "燕麦"),
    "crustacean": ("虾", "蟹", "龙虾"),
    "fish": ("鱼",),
    "egg": ("鸡蛋", "鸭蛋", "蛋粉"),
    "peanut": ("花生",),
    "soy": ("大豆", "黄豆", "豆浆", "豆腐"),
    "milk": ("牛奶", "奶粉", "乳清", "奶酪", "芝士"),
    "tree_nut": ("杏仁", "核桃", "腰果", "榛子"),
}


def evaluate_packaging(data):
    pack = data.get("packaging")
    if pack is None:
        return []
    if not isinstance(pack, dict):
        return [finding("L1", "REVIEW", "PACKAGE-INPUT", "包装信息必须是结构化对象。", "input")]
    findings = []
    if pack.get("complete") is True:
        fields = pack.get("fields") or {}
        if not isinstance(fields, dict):
            return [finding("L1", "REVIEW", "PACKAGE-FIELDS", "包装字段格式不正确。", "input")]
        for key, label in BASIC_FIELDS.items():
            present = bool(str(fields.get(key, "")).strip())
            findings.append(finding(
                "L1", "PASS" if present else "REVIEW", "PACKAGE-FIELD-" + key.upper(),
                label + ("已提供。" if present else "未见，需核实完整包装稿与适用要求。"),
                "packaging_completeness",
            ))

    ingredients = pack.get("ingredients")
    if ingredients is None:
        return findings
    if not isinstance(ingredients, list) or any(not isinstance(x, dict) for x in ingredients):
        findings.append(finding("L1", "REVIEW", "INGREDIENT-INPUT", "配料须为结构化列表。", "input"))
        return findings
    names = [str(x.get("name", "")) for x in ingredients]
    proportions = []
    try:
        for item in ingredients:
            proportions.append(number(item["amount_percent"], "配料占比") if "amount_percent" in item else None)
    except ValueError as exc:
        findings.append(finding("L1", "REVIEW", "INGREDIENT-AMOUNT", str(exc), "input"))
        proportions = []
    if any(x is not None and x > 100 for x in proportions):
        findings.append(finding("L1", "REVIEW", "INGREDIENT-AMOUNT",
                                "单一配料占比超过100%，请核对数据。", "input"))
        proportions = []
    if proportions:
        inversions = []
        for i, left in enumerate(proportions):
            if left is None:
                continue
            for j in range(i + 1, len(proportions)):
                right = proportions[j]
                if right is not None and right > 2 and left < right:
                    inversions.append({"earlier": names[i], "later": names[j],
                                       "earlier_percent": str(left), "later_percent": str(right)})
        if inversions:
            findings.append(finding("L1", "FAIL", "INGREDIENT-ORDER",
                                    "已给出的配方占比与配料顺序不符，存在大于2%的后项反超前项。",
                                    "structured_ingredient_check", details={"inversions": inversions}))
        elif any(x is None for x in proportions):
            findings.append(finding("L1", "NOT_CHECKED", "INGREDIENT-ORDER",
                                    "部分配料无占比，无法完整检查顺序。", "input"))
        else:
            findings.append(finding("L1", "PASS", "INGREDIENT-ORDER",
                                    "按提供的配方占比未见大于2%配料排序倒置。",
                                    "structured_ingredient_check"))

    detected = {category for category, terms in ALLERGEN_TERMS.items()
                if any(any(term in name for term in terms) for name in names)}
    declarations = pack.get("allergen_declarations") or []
    if not isinstance(declarations, list):
        findings.append(finding("L1", "REVIEW", "ALLERGEN-INPUT",
                                "致敏物质声明应为类别列表。", "input"))
        declarations = []
    declared = set(str(x) for x in declarations)
    for category in sorted(detected):
        findings.append(finding(
            "L1", "PASS" if category in declared else "REVIEW",
            "ALLERGEN-" + category.upper(),
            category + ("已在结构化声明中出现。" if category in declared else
                        "由配料提示，需人工确认标签上是否有足够明确的致敏物质标示。"),
            "allergen_screen", details={"ingredient_names": names, "declared": sorted(declared)},
        ))
    return findings
