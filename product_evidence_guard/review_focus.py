"""Conservative full-width context band for OCR-guided visual review."""
from __future__ import annotations

from pathlib import Path
import re


def choose_review_region(path: Path, all_lines, target_lines):
    if not target_lines or not all_lines or len(all_lines) > 128:
        return None
    # Preserve every suspected parameter, numeric line, and uncertain OCR line.
    # A full-width band keeps horizontal labels and units together.
    selected = [*target_lines, *(line for line in all_lines if re.search(r"\d", line.text) or line.confidence < .97)]
    boxes = []
    for line in selected:
        box = line.bbox_1000
        if box is None or len(box) != 4 or not all(isinstance(v, int) and not isinstance(v, bool) for v in box):
            return None
        if not (0 <= box[0] < box[2] <= 1000 and 0 <= box[1] < box[3] <= 1000):
            return None
        boxes.append(box)
    if not boxes:
        return None
    try:
        from PIL import Image
        with Image.open(path) as image:
            if image.getexif().get(274, 1) != 1:
                return None  # OCR and EXIF-transposed coordinates may differ.
            if image.width * image.height > 40_000_000:
                return None
    except (OSError, ValueError):
        return None
    margin = max(35, min(100, max(b[3] - b[1] for b in boxes) * 2))
    top = max(0, min(b[1] for b in boxes) - margin)
    bottom = min(1000, max(b[3] for b in boxes) + margin)
    if bottom - top > 800 or bottom - top < 100:
        return None
    return (0, top, 1000, bottom)
