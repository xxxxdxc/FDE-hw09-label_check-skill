"""Prepare a table-focused image while keeping an exact map to original pixels."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from .images import image_size


@dataclass
class TableImage:
    data: bytes
    origin: tuple[int, int]
    scale: tuple[float, float]
    inputSize: tuple[int, int]
    cropBox: tuple[int, int, int, int]


def _title_crop(raw: dict, width: int, height: int) -> tuple[int, int, int, int] | None:
    titles = [item for item in raw.get("words_result", []) if "营养成分表" in str(item.get("words", ""))
              and item.get("location")]
    if len(titles) != 1:
        return None
    location = titles[0]["location"]
    y = int(location["top"])
    w, h = int(location["width"]), int(location["height"])
    if min(w, h) <= 0:
        return None
    nutrient_names = ("能量", "蛋白质", "脂肪", "碳水化合物", "糖", "钠", "钙", "膳食纤维")
    nutrient_rows = []
    for item in raw.get("words_result", []):
        place = item.get("location") or {}
        words = str(item.get("words", ""))
        row_y = place.get("top", -1)
        if row_y > y and any(words.strip().lstrip("-—－") == name for name in nutrient_names):
            nutrient_rows.append(place)
    # The title may be centered over the table while the label and NRV columns
    # sit beyond its horizontal extent. Never trim either side of a known table.
    left, right = 0, width
    top = max(0, y - max(35, h))
    bottom = min(height, max([y + max(550, h * 14),
                              *(int(p["top"]) + int(p["height"]) + max(100, h * 2)
                                for p in nutrient_rows)]))
    return (left, top, right, bottom) if right - left >= 100 and bottom - top >= 100 else None


def prepare_table_image(image_path: Path, accurate_raw: dict) -> TableImage:
    """Crop/resize only when useful; returned transform maps V2 pixels to original pixels."""
    width, height = image_size(image_path)
    source_bytes = image_path.read_bytes()
    full = (0, 0, width, height)
    if len(source_bytes) <= 4_000_000 and max(width, height) <= 2500:
        return TableImage(source_bytes, (0, 0), (1, 1), (width, height), full)
    try:
        from PIL import Image
    except ImportError as exc:
        raise ValueError("Pillow is required to crop/resize large images for Table V2; install requirements.txt") from exc
    crop_box = _title_crop(accurate_raw, width, height) or full
    with Image.open(image_path) as original:
        image = original.convert("RGB").crop(crop_box)
        original_crop_size = image.size
        while True:
            if max(image.size) > 8192:
                image.thumbnail((8192, 8192), Image.Resampling.LANCZOS)
            buffer = BytesIO()
            image.save(buffer, format="JPEG", quality=90, optimize=True)
            data = buffer.getvalue()
            # Baidu's request limit applies to the encoded image. Leave room for form encoding.
            if len(data) * 4 / 3 < 7_000_000:
                break
            if min(image.size) <= 30:
                raise ValueError("Table image cannot be reduced within the API size limit")
            image = image.resize((max(15, int(image.width * 0.8)), max(15, int(image.height * 0.8))),
                                 Image.Resampling.LANCZOS)
        scale = (original_crop_size[0] / image.width, original_crop_size[1] / image.height)
        return TableImage(data, crop_box[:2], scale, image.size, crop_box)
