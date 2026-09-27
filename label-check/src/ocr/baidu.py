"""Baidu accurate OCR adapter. Secrets and raw images are never logged."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
ACCURATE_URL = "https://aip.baidubce.com/rest/2.0/ocr/v1/accurate"


class OcrServiceError(RuntimeError):
    pass


def _load_local_env(project_root: Path) -> None:
    env_file = project_root / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


def _post(url: str, data: dict[str, str]) -> dict:
    request = Request(
        url,
        data=urlencode(data).encode("ascii"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urlopen(request, timeout=35) as response:
            payload = json.load(response)
    except HTTPError as exc:
        raise OcrServiceError(f"Baidu OCR HTTP {exc.code}") from None
    except (URLError, TimeoutError) as exc:
        raise OcrServiceError(f"Baidu OCR network error: {exc.reason if isinstance(exc, URLError) else 'timeout'}") from None
    if not isinstance(payload, dict):
        raise OcrServiceError("Baidu OCR returned a non-object response")
    if "error_code" in payload:
        raise OcrServiceError(
            f"Baidu OCR error {payload['error_code']}: {payload.get('error_msg', 'unknown')}"
        )
    return payload


def _access_token(project_root: Path) -> str:
    _load_local_env(project_root)
    api_key = os.environ.get("BAIDU_API_KEY")
    secret_key = os.environ.get("BAIDU_SECRET_KEY")
    if not api_key or not secret_key:
        raise OcrServiceError("BAIDU_API_KEY and BAIDU_SECRET_KEY are required")
    token_data = _post(TOKEN_URL, {
        "grant_type": "client_credentials",
        "client_id": api_key,
        "client_secret": secret_key,
    })
    token = token_data.get("access_token")
    if not token:
        raise OcrServiceError("Baidu OCR token response had no access_token")
    return token


def recognize_accurate(image_path: Path, project_root: Path) -> dict:
    token = _access_token(project_root)
    image_b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    payload = _post(ACCURATE_URL + "?access_token=" + token, {
        "image": image_b64,
        "language_type": "CHN_ENG",
        "probability": "true",
    })
    if not isinstance(payload.get("words_result"), list):
        raise OcrServiceError("Baidu accurate OCR response lacks words_result")
    return payload
