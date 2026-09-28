#!/usr/bin/env python3
"""Public-facing, read-only demo API for the scene 09 three-layer checker."""
import argparse
import json
import sys
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


HERE = Path(__file__).resolve().parent
SKILL = HERE.parent / "label-check-v2"
sys.path.insert(0, str(SKILL / "src"))

from label_check_v2.channel import ChannelStore  # noqa: E402
from label_check_v2.engine import run  # noqa: E402
from ocr_data import apply_review, catalog, checker_input, image_path, review_page, sample  # noqa: E402


DEMO_DB = Path.home() / ".cache" / "label-check-v2" / "demo-channel.sqlite"
SAMPLE_FILES = {
    "full": "full_packaging.json",
    "serving": "course_40g.json",
    "claim": "yellow_72g_transcription.json",
}


def samples():
    loaded = {}
    for key, filename in SAMPLE_FILES.items():
        loaded[key] = json.loads((SKILL / "examples" / filename).read_text(encoding="utf-8"))
    channel = deepcopy(loaded["full"])
    channel.update({
        "product_id": "DEMO-CHANNEL",
        "subjective_questions": [],
        "channel": "演示商超",
        "category": "玉米片",
        "sku": "DEMO-01",
        "label_text": "玉米片 低糖 每100g",
        "documents": [],
    })
    loaded["channel"] = channel
    clean = deepcopy(loaded["full"])
    clean["product_id"] = "DEMO-PASS"
    clean["subjective_questions"] = []
    loaded["pass"] = clean
    return loaded


def prepare_demo_cards():
    store = ChannelStore(DEMO_DB)
    try:
        if store.list_cards():
            return
        first = store.submit_card("演示商超", "包装正面须有‘专供’字样", "contains",
                                  "专供", "演示渠道要求-001", category="玉米片", sku="DEMO-01",
                                  valid_from="2026-01-01")
        store.review_card(first, "confirmed", "演示质量负责人")
        second = store.submit_card("演示商超", "须提交营养检测报告", "document",
                                   "营养检测报告", "演示渠道要求-002", category="玉米片",
                                   valid_from="2026-01-01")
        store.review_card(second, "confirmed", "演示质量负责人")
        store.submit_card("演示商超", "拟新增绿色角标要求", "manual", "绿色角标",
                          "一次退回反馈-待核实", category="玉米片", valid_from="2026-01-01")
    finally:
        store.close()


def check_payload(value, depth=0):
    if depth > 8:
        raise ValueError("JSON嵌套过深")
    if isinstance(value, dict):
        if len(value) > 100:
            raise ValueError("JSON字段过多")
        for key, item in value.items():
            if len(str(key)) > 120:
                raise ValueError("JSON字段名过长")
            check_payload(item, depth + 1)
    elif isinstance(value, list):
        if len(value) > 120:
            raise ValueError("JSON列表过长")
        for item in value:
            check_payload(item, depth + 1)
    elif isinstance(value, str):
        if len(value) > 2000:
            raise ValueError("文本字段过长")
    elif isinstance(value, (int, float)) and len(str(value)) > 100:
        raise ValueError("数值字段过长")


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, data, content_type="application/json; charset=utf-8"):
        body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path in ("/", "/index.html"):
            self._send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif path in ("/ocr", "/ocr.html"):
            self._send(200, (HERE / "ocr.html").read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/ocr/flow-image":
            self._send(200, (HERE.parent / "label-check" / "docs" / "ocr-review-flow.png").read_bytes(), "image/png")
        elif path == "/api/ocr/samples":
            try:
                self._send(200, {"source": "dual_v3/final_candidates", "ocrLive": False,
                                 "humanVerified": False, "items": catalog()})
            except FileNotFoundError as exc:
                self._send(503, {"error": str(exc)})
        elif path.startswith("/api/ocr/sample/"):
            try:
                self._send(200, sample(path.rsplit("/", 1)[-1]))
            except ValueError as exc:
                self._send(404, {"error": str(exc)})
        elif path.startswith("/api/ocr/image/"):
            try:
                image = image_path(path.rsplit("/", 1)[-1])
                if not image.is_file():
                    raise ValueError("原图不在本机数据包中")
                self._send(200, image.read_bytes(), "image/jpeg")
            except ValueError as exc:
                self._send(404, {"error": str(exc)})
        elif path.startswith("/api/ocr/review-page/"):
            try:
                self._send(200, review_page(path.rsplit("/", 1)[-1]).encode("utf-8"), "text/html; charset=utf-8")
            except (ValueError, FileNotFoundError) as exc:
                self._send(404, {"error": str(exc)})
        elif path.startswith("/api/ocr/check/"):
            try:
                code = path.rsplit("/", 1)[-1]
                payload, gate = checker_input(code)
                if payload is None:
                    self._send(409, {"gate": gate, "message": "OCR 门禁或计算口径尚未放行"})
                else:
                    self._send(200, {"gate": gate, "report": run(payload),
                                     "notice": "仅检查缓存数据中已放行的营养字段；不代表人工验收或整张包装合规。"})
            except ValueError as exc:
                self._send(404, {"error": str(exc)})
        elif path == "/api/samples":
            self._send(200, samples())
        elif path == "/api/health":
            self._send(200, {"ok": True, "service": "fde09-label-check", "input": "structured-json"})
        else:
            self._send(404, {"error": "页面不存在"})

    def do_POST(self):
        if urlsplit(self.path).path.startswith("/api/ocr/review/"):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 262144:
                    raise ValueError("复核决定必须是256KB以内的JSON")
                self.connection.settimeout(10)
                decisions = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(decisions, dict):
                    raise ValueError("复核决定必须是JSON对象")
                self._send(200, apply_review(urlsplit(self.path).path.rsplit("/", 1)[-1], decisions))
            except (ValueError, UnicodeError, TimeoutError, KeyError, TypeError, FileNotFoundError) as exc:
                self._send(400, {"error": str(exc)})
            return
        if urlsplit(self.path).path == "/api/ocr/ingest":
            self._send(501, {"error": "实时 OCR 接口已预留，当前演示只读取 dual_v3 缓存结果。"})
            return
        if urlsplit(self.path).path != "/api/check":
            self._send(404, {"error": "接口不存在"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 65536:
                raise ValueError("请求必须是64KB以内的JSON")
            self.connection.settimeout(10)
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("输入必须是JSON对象")
            check_payload(data)
            store = ChannelStore(DEMO_DB)
            try:
                report = run(data, store)
            finally:
                store.close()
            self._send(200, report)
        except (ValueError, UnicodeError, TimeoutError, KeyError, TypeError) as exc:
            self._send(400, {"error": str(exc)})
        except Exception:
            self._send(500, {"error": "校验服务暂时不可用，请稍后重试"})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    prepare_demo_cards()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print("FDE09 demo listening on http://{}:{}".format(args.host, args.port), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
