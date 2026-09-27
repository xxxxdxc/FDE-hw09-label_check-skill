"""Build a self-contained, offline human-review page from a LabelDocument."""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
from pathlib import Path

from src.ocr.images import image_size
from src.shared import collect_review_items, document_from_dict, validate_document
from src.shared.confidence import LOW_OCR_CONFIDENCE

TEMPLATE = Path(__file__).with_name("template.html")


def render_page(document_path: Path, image_path: Path) -> tuple[str, int]:
    document = document_from_dict(json.loads(document_path.read_text(encoding="utf-8")))
    validate_document(document)
    if len(document.images) != 1:
        raise ValueError("Review page currently supports one image per LabelDocument")
    image = document.images[0]
    if Path(image.fileName).name != image_path.name:
        raise ValueError("Review image filename does not match the structured document")
    width, height = image_size(image_path)
    if (width, height) != (image.widthPx, image.heightPx):
        raise ValueError("Review image dimensions do not match the structured document")
    image_bytes = image_path.read_bytes()
    suffix = image_path.suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg"}:
        raise ValueError("Review image must be PNG or JPEG")
    mime = "image/png" if suffix == ".png" else "image/jpeg"
    data = {
        "documentId": document.documentId,
        "productId": document.productId,
        "imageName": image_path.name,
        "imageSha256": hashlib.sha256(image_bytes).hexdigest(),
        "imageWidth": width,
        "imageHeight": height,
        "threshold": LOW_OCR_CONFIDENCE,
        "items": collect_review_items(document),
        "tableItems": [{
            "tableId": item.tableId, "code": item.code, "message": item.message,
            "evidenceRefs": item.evidenceRefs, "boxPx": vars(item.boxPx) if item.boxPx else None,
        } for item in document.tableDiagnostics if item.status == "needs_review" and item.tableId],
    }
    payload = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    rendered = (TEMPLATE.read_text(encoding="utf-8")
                .replace("__TITLE__", html.escape(document.documentId))
                .replace("__IMAGE_DATA__", f"data:{mime};base64,{base64.b64encode(image_bytes).decode('ascii')}")
                .replace("__REVIEW_DATA__", payload))
    return rendered, len(data["items"])


def build_page(document_path: Path, image_path: Path, output_path: Path) -> int:
    if output_path.suffix.lower() != ".html" or output_path.resolve() in {
        document_path.resolve(), image_path.resolve()
    }:
        raise ValueError("Review page output must be a separate .html file")
    rendered, count = render_page(document_path, image_path)
    output_path.write_text(rendered, encoding="utf-8")
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description="Create an offline image-and-fields review page")
    parser.add_argument("--document", required=True, type=Path, help="Structured LabelDocument JSON")
    parser.add_argument("--image", required=True, type=Path, help="Original image used for OCR")
    parser.add_argument("--output", required=True, type=Path, help="Output HTML path")
    args = parser.parse_args()
    count = build_page(args.document, args.image, args.output)
    print(f"Review page saved: {args.output} ({count} fields)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
