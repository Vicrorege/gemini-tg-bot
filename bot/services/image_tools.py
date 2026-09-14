"""Image tools for processing and building collages from user photos."""

import io
import re
from typing import List, Optional
from PIL import Image, ImageDraw, ImageOps

COLLAGE_TRIGGERS = re.compile(
    r"\b(коллаж|объедини|склей|собери в одно|собери в один|сделай одно фото|сделай одну картинку|составь коллаж|сделай коллаж|сгенерируй.*коллаж)\b",
    re.IGNORECASE
)


def is_collage_requested(text: str) -> bool:
    """Check if the prompt requests building a collage or merging photos."""
    if not text:
        return False
    return bool(COLLAGE_TRIGGERS.search(text))


def optimize_image_for_vision(
    image_bytes: bytes,
    max_dimension: int = 1568,
    quality: int = 85
) -> bytes:
    """
    Resize image to fit within max_dimension (preserving aspect ratio)
    and compress to optimized JPEG to avoid large payloads (HTTP 413) and speed up vision inference.
    """
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            img = img.convert("RGB")
            if max(img.size) > max_dimension:
                img.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            return buf.getvalue()
    except Exception:
        return image_bytes


def build_collage(
    images_bytes: List[bytes],
    cell_size: tuple = (1000, 700),
    gap: int = 20,
    margin: int = 24,
    radius: int = 16
) -> Optional[bytes]:
    """
    Assemble a list of image byte streams into an aesthetic 2x2 or auto-grid collage.
    """
    if not images_bytes or len(images_bytes) < 2:
        return None

    imgs = []
    for b in images_bytes:
        try:
            im = Image.open(io.BytesIO(b)).convert("RGB")
            imgs.append(im)
        except Exception:
            continue

    n = len(imgs)
    if n < 2:
        return None

    # Calculate optimal grid layout
    if n == 4:
        cols, rows = 2, 2
    elif n <= 3:
        cols, rows = n, 1
    elif n <= 6:
        cols, rows = 3, 2
    elif n <= 8:
        cols, rows = 4, 2
    else:
        cols, rows = 3, (n + 2) // 3

    cell_w, cell_h = cell_size
    total_w = margin * 2 + cell_w * cols + gap * (cols - 1)
    total_h = margin * 2 + cell_h * rows + gap * (rows - 1)

    # Elegant dark background
    canvas = Image.new("RGB", (total_w, total_h), (20, 22, 28))

    for idx, im in enumerate(imgs[: cols * rows]):
        r = idx // cols
        c = idx % cols
        x = margin + c * (cell_w + gap)
        y = margin + r * (cell_h + gap)

        fitted = ImageOps.fit(im, (cell_w, cell_h), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))

        mask = Image.new("L", (cell_w, cell_h), 0)
        draw = ImageDraw.Draw(mask)
        draw.rounded_rectangle([(0, 0), (cell_w, cell_h)], radius=radius, fill=255)

        fitted.putalpha(mask)
        canvas.paste(fitted, (x, y), fitted)

    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=95)
    return buf.getvalue()
