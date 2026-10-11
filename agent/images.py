"""Turn an item's image(s) into a 1080x1080 JPEG ready to upload.

Single image: square crop placed on the most detailed part of the scene
(gradient energy), so a wide satellite scene keeps the river delta rather
than an empty corner.

Before/after pair: the two images stacked into one square (each 1080x540),
with their date labels, so the change is visible without a slider.
"""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

from .scout import HEADERS, Item

SIZE = 1080
MAX_BYTES = 40 * 1024 * 1024
LOW_RES = 700  # source square smaller than this gets upscaled noticeably


def download(url: str, timeout: int = 60) -> Image.Image:
    with requests.get(url, headers=HEADERS, timeout=timeout, stream=True) as r:
        r.raise_for_status()
        buf = io.BytesIO()
        for chunk in r.iter_content(1 << 16):
            buf.write(chunk)
            if buf.tell() > MAX_BYTES:
                raise ValueError("image larger than 40 MB")
    Image.MAX_IMAGE_PIXELS = 400_000_000  # hi-res satellite JPGs are big
    img = Image.open(io.BytesIO(buf.getvalue()))
    img.seek(0)  # first frame of an animated GIF
    img = ImageOps.exif_transpose(img)
    return img.convert("RGB")


def _energy_offset(img: Image.Image, win_w: int, win_h: int) -> tuple[int, int]:
    """Top-left corner of the win_w x win_h window with the most detail."""
    w, h = img.size
    if win_w >= w and win_h >= h:
        return 0, 0
    scale = 256 / max(w, h)
    small = np.asarray(img.convert("L").resize((max(1, int(w * scale)), max(1, int(h * scale)))),
                       dtype=np.float32)
    gy, gx = np.gradient(small)
    energy = np.hypot(gx, gy)
    sw, sh = max(1, int(win_w * scale)), max(1, int(win_h * scale))
    if win_w < w:  # slide horizontally
        cols = energy.sum(axis=0)
        sums = np.convolve(cols, np.ones(sw), "valid")
        # gentle pull towards the centre so near-ties keep the classic framing
        centre = (len(sums) - 1) / 2
        sums = (sums + sums.mean() * 0.01 + 1e-6) * (1 - 0.15 * np.abs(np.arange(len(sums)) - centre) / max(centre, 1))
        x = int(np.argmax(sums) / scale)
        return min(x, w - win_w), 0
    rows = energy.sum(axis=1)
    sums = np.convolve(rows, np.ones(sh), "valid")
    centre = (len(sums) - 1) / 2
    sums = (sums + sums.mean() * 0.01 + 1e-6) * (1 - 0.15 * np.abs(np.arange(len(sums)) - centre) / max(centre, 1))
    y = int(np.argmax(sums) / scale)
    return 0, min(y, h - win_h)


def crop_box(img: Image.Image, ratio: float) -> tuple[float, float, float, float]:
    """Crop window with width/height == ratio around the most detailed region,
    as fractions of the image size (so it can be reused on a twin image)."""
    w, h = img.size
    if abs(w / h - ratio) < 0.01:
        return 0.0, 0.0, 1.0, 1.0
    if w / h > ratio:
        cw, ch = int(round(h * ratio)), h
    else:
        cw, ch = w, int(round(w / ratio))
    x, y = _energy_offset(img, cw, ch)
    return x / w, y / h, (x + cw) / w, (y + ch) / h


def apply_box(img: Image.Image, box) -> Image.Image:
    w, h = img.size
    return img.crop((round(box[0] * w), round(box[1] * h), round(box[2] * w), round(box[3] * h)))


def crop_to(img: Image.Image, ratio: float) -> Image.Image:
    return apply_box(img, crop_box(img, ratio))


def _font(size: int):
    for name in ("DejaVuSans-Bold.ttf", "Arial Bold.ttf", "arialbd.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _label(img: Image.Image, text: str) -> None:
    if not text:
        return
    d = ImageDraw.Draw(img, "RGBA")
    f = _font(30)
    pad = 12
    x0, y0, x1, y1 = d.textbbox((0, 0), text, font=f)
    tw, th = x1 - x0, y1 - y0
    d.rectangle([16, 16, 16 + tw + 2 * pad, 16 + th + 2 * pad], fill=(0, 0, 0, 160))
    d.text((16 + pad - x0, 16 + pad - y0), text, font=f, fill=(255, 255, 255, 255))


def make_square(images: list[Image.Image], labels: list[str]) -> tuple[Image.Image, int]:
    """Return the 1080x1080 image and the source pixels behind each output
    row/column (for a low-resolution warning)."""
    if len(images) >= 2:
        canvas = Image.new("RGB", (SIZE, SIZE), "white")
        src_px = []
        # One window for both dates, chosen on the "after" image, so the two
        # halves always show the same ground area.
        box = crop_box(images[1], 2.0)
        for i, (im, lab) in enumerate(zip(images[:2], labels[:2])):
            half = apply_box(im, box)
            src_px.append(half.size[0])
            half = half.resize((SIZE, SIZE // 2), Image.LANCZOS)
            _label(half, lab)
            canvas.paste(half, (0, i * SIZE // 2))
        # thin divider
        ImageDraw.Draw(canvas).rectangle([0, SIZE // 2 - 2, SIZE, SIZE // 2 + 1], fill="white")
        return canvas, min(src_px)
    sq = crop_to(images[0], 1.0)
    src = sq.size[0]
    sq = sq.resize((SIZE, SIZE), Image.LANCZOS)
    _label(sq, labels[0] if labels else "")
    return sq, src


def build(item: Item, out_path: Path, fetch=None) -> Item:
    """Download the item's image(s), write the square JPEG, record notes."""
    fetch = fetch or download
    if not item.images:
        item.image_note = "no image found on the page"
        return item
    try:
        pics = [fetch(i["url"]) for i in item.images[:2]]
    except Exception as exc:
        # a pair that half-fails still gives a usable single image
        if len(item.images) > 1:
            try:
                pics = [fetch(item.images[1]["url"])]
                item.images = item.images[1:]
            except Exception:
                item.image_note = f"image download failed: {exc}"
                return item
        else:
            item.image_note = f"image download failed: {exc}"
            return item
    labels = [i.get("label", "") for i in item.images[:2]] if len(pics) > 1 else [""]
    square, src_px = make_square(pics, labels)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    square.save(out_path, "JPEG", quality=90, optimize=True, progressive=True)
    item.square_path = str(out_path)
    kind = "before/after pair stacked" if len(pics) > 1 else "square crop"
    item.image_note = f"{kind}, from {src_px}px of source width"
    if src_px < LOW_RES:
        item.image_note += " (low resolution: upscaled, check sharpness)"
    return item
