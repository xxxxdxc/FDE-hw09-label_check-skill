# coding: utf-8
"""Run Baidu OCR on the front packaging design image."""

import base64
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
IMAGE = ROOT / "M2-包装设计稿正面.png"
TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
OCR_URL = "https://aip.baidubce.com/rest/2.0/ocr/v1/accurate"


def load_local_env():
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


def post_json(url, data):
    request = Request(
        url,
        data=urlencode(data).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    load_local_env()
    api_key = os.environ.get("BAIDU_API_KEY")
    secret_key = os.environ.get("BAIDU_SECRET_KEY")
    if not api_key or not secret_key:
        raise RuntimeError("Missing BAIDU_API_KEY or BAIDU_SECRET_KEY in .env or environment")
    if not IMAGE.is_file():
        raise FileNotFoundError(IMAGE)

    token_data = post_json(TOKEN_URL, {
        "grant_type": "client_credentials",
        "client_id": api_key,
        "client_secret": secret_key,
    })
    token = token_data.get("access_token")
    if not token:
        raise RuntimeError("Token request failed: " + json.dumps(token_data, ensure_ascii=False))

    image_b64 = base64.b64encode(IMAGE.read_bytes()).decode("ascii")
    data = post_json(OCR_URL + "?access_token=" + token, {
        "image": image_b64,
        "language_type": "CHN_ENG",
        "probability": "true",
    })
    if "error_code" in data:
        raise RuntimeError("OCR request failed: " + json.dumps(data, ensure_ascii=False))

    print("Image:", IMAGE.name)
    print("Recognized lines:", data.get("words_result_num", 0))
    for item in data.get("words_result", []):
        probability = item.get("probability") or {}
        print(json.dumps({
            "text": item.get("words", ""),
            "location": item.get("location"),
            "confidence": probability.get("average"),
        }, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (HTTPError, URLError, TimeoutError, OSError, RuntimeError) as error:
        raise SystemExit(str(error)) from None
