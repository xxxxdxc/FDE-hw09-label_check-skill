"""Three-layer routing and report assembly."""
from .core import evaluate_l1, finding, load_policy
from .packaging import evaluate_packaging


def review_needed(findings):
    return any(item["status"] in ("REVIEW", "NOT_CHECKED") for item in findings)


def run(data, store=None, policy=None):
    if not isinstance(data, dict):
        raise ValueError("输入必须是JSON对象")
    policy = policy or load_policy()
    normalized, findings = evaluate_l1(data, policy)
    findings.extend(evaluate_packaging(data))
    if data.get("ocr_error"):
        findings.append(finding("L3", "REVIEW", "OCR-ERROR", data["ocr_error"], "human_decision"))
    for question in data.get("subjective_questions", []):
        findings.append(finding("L3", "REVIEW", "HUMAN-QUESTION", str(question), "human_decision"))
    if data.get("channel"):
        if store is None:
            findings.append(finding("L2", "NOT_CHECKED", "CHANNEL-NO-KB",
                                    "未提供渠道知识库，渠道审核未检查。", "channel_rule"))
        else:
            findings.extend(store.evaluate(
                data["channel"], data.get("category", "*"), data.get("sku", "*"),
                data.get("label_text", ""), data.get("documents", []), data.get("on_date"),
            ))
    if any(x["status"] == "FAIL" for x in findings):
        status = "FAIL"
    elif review_needed(findings):
        status = "REVIEW"
    else:
        status = "PASS"
    return {
        "product_id": data.get("product_id"), "revision": data.get("revision"),
        "policy_version": policy["version"], "output_header": "每100g",
        "normalized_per_100g": normalized, "status": status,
        "scope": "仅对已提供数据及已确认规则完成初检，不代表最终印刷或上市放行。",
        "findings": findings,
        "human_queue": [x for x in findings if x["layer"] == "L3" or
                        (x["status"] in ("REVIEW", "NOT_CHECKED") and x["layer"] != "L3")],
    }
