#!/usr/bin/env python3
"""Command line entry point for the three-layer label preflight."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from label_check_v2.channel import ChannelStore  # noqa: E402
from label_check_v2.engine import run  # noqa: E402
from label_check_v2.retrieval import evaluate, search  # noqa: E402


def emit(value, output=None):
    content = json.dumps(value, ensure_ascii=False, indent=2)
    if output:
        Path(output).write_text(content + "\n", encoding="utf-8")
    else:
        print(content)


def parser():
    app = argparse.ArgumentParser(description="场景09：三层包装标签初检")
    sub = app.add_subparsers(dest="action", required=True)

    check = sub.add_parser("check", help="从结构化JSON执行三层初检")
    check.add_argument("--input", required=True, help="结构化输入JSON")
    check.add_argument("--product-id")
    check.add_argument("--revision")
    check.add_argument("--channel")
    check.add_argument("--category", default="*")
    check.add_argument("--sku", default="*")
    check.add_argument("--db", help="已确认的渠道要求数据库；不传则渠道层不执行")
    check.add_argument("--output", help="显式指定才保存报告；默认只打印")

    cards = sub.add_parser("cards", help="查看、提交及审核渠道要求卡")
    cards.add_argument("--db", required=True)
    cards_sub = cards.add_subparsers(dest="card_action", required=True)
    submit = cards_sub.add_parser("submit")
    for arg in ("channel", "requirement", "matcher", "target", "source-ref"):
        submit.add_argument("--" + arg, required=True)
    submit.add_argument("--source-kind", default="channel_doc")
    submit.add_argument("--category", default="*")
    submit.add_argument("--sku", default="*")
    submit.add_argument("--valid-from")
    submit.add_argument("--valid-to")
    approve = cards_sub.add_parser("review")
    approve.add_argument("id", type=int)
    approve.add_argument("--decision", choices=("confirmed", "rejected"), required=True)
    approve.add_argument("--reviewer", required=True)
    approve.add_argument("--note", default="")
    listing = cards_sub.add_parser("list")
    listing.add_argument("--status", choices=("candidate", "confirmed", "rejected"))

    human = sub.add_parser("human", help="记录脱敏后的L3人工决策；不会自动晋级渠道规则")
    human.add_argument("--db", required=True)
    for arg in ("question", "decision", "source-ref", "reviewer"):
        human.add_argument("--" + arg, required=True)

    lookup = sub.add_parser("search", help="只检索依据，不自动执行规则")
    lookup.add_argument("query")
    lookup.add_argument("--method", choices=("lexical", "embedding"), default="lexical")
    lookup.add_argument("--top-k", type=int, default=3)
    lookup.add_argument("--cache-dir")
    lookup.add_argument("--db", help="同时检索已确认渠道卡")

    bench = sub.add_parser("retrieval-eval", help="24条人工查询检索小测")
    bench.add_argument("--method", choices=("lexical", "embedding"), default="lexical")
    bench.add_argument("--cache-dir")
    return app


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.action == "check":
            data = json.loads(Path(args.input).read_text(encoding="utf-8"))
            for key in ("product_id", "revision", "channel", "category", "sku"):
                value = getattr(args, key, None)
                if value and (key not in data or data.get(key) in (None, "*")):
                    data[key] = value
            store = ChannelStore(args.db) if args.db else None
            try:
                result = run(data, store)
            finally:
                if store:
                    store.close()
            emit(result, args.output)
        elif args.action == "cards":
            store = ChannelStore(args.db)
            try:
                if args.card_action == "submit":
                    result = {"candidate_id": store.submit_card(args.channel, args.requirement,
                        args.matcher, args.target, args.source_ref, args.source_kind,
                        args.category, args.sku, args.valid_from, args.valid_to)}
                elif args.card_action == "review":
                    store.review_card(args.id, args.decision, args.reviewer, args.note)
                    result = {"id": args.id, "status": args.decision}
                else:
                    result = store.list_cards(args.status)
                emit(result)
            finally:
                store.close()
        elif args.action == "human":
            store = ChannelStore(args.db)
            try:
                emit({"decision_id": store.record_human_decision(
                    args.question, args.decision, args.source_ref, args.reviewer)})
            finally:
                store.close()
        elif args.action == "search":
            result = {"topics": search(args.query, args.method, args.top_k, cache_dir=args.cache_dir)}
            if args.db:
                store = ChannelStore(args.db)
                try:
                    result["confirmed_channel_cards"] = store.search(args.query, args.top_k)
                finally:
                    store.close()
            emit(result)
        else:
            emit(evaluate(args.method, args.cache_dir))
        return 0
    except (ValueError, RuntimeError, OSError, json.JSONDecodeError) as exc:
        print("error: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
