"""Baidu Table OCR V2 adapter. Raw cells remain separate from accurate OCR lines."""

from __future__ import annotations

import base64
from pathlib import Path

from .baidu import OcrServiceError, _access_token, _post

TABLE_URL = "https://aip.baidubce.com/rest/2.0/ocr/v1/table"


def recognize_table_v2(image_bytes: bytes, project_root: Path) -> dict:
    """Recognize table cells from one image/crop; request in-cell line coordinates."""
    token = _access_token(project_root)
    payload = _post(TABLE_URL + "?access_token=" + token, {
        "image": base64.b64encode(image_bytes).decode("ascii"),
        "cell_contents": "true",
        "return_excel": "false",
    })
    if not isinstance(payload.get("table_num"), int):
        raise OcrServiceError("Baidu Table V2 response lacks table_num")
    # The service omits tables_result entirely when it detects zero tables.
    if payload["table_num"] == 0 and "tables_result" not in payload:
        payload["tables_result"] = []
    if not isinstance(payload.get("tables_result"), list):
        raise OcrServiceError("Baidu Table V2 response lacks tables_result")
    return payload
