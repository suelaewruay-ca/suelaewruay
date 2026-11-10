"""Public background assets and presentation, independent of account settings."""

import base64
from functools import lru_cache
from io import BytesIO
from pathlib import Path
import warnings

from PIL import Image, UnidentifiedImageError

ROOT = Path(__file__).resolve().parent
DEFAULT_THEME = "default"
DEFAULT_NAME = "ลอยกระทง · bg.png"
DEFAULT_APPEARANCE = {"theme_id": DEFAULT_THEME, "enabled": True, "darkness": 72,
                      "position": "center top"}
POSITIONS = {"center top": "ด้านบน", "center center": "กึ่งกลาง", "center bottom": "ด้านล่าง"}
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 24_000_000
MIME_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


def validate_background(raw):
    """Inspect the actual image, not the untrusted upload name or content type."""
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("กรุณาใช้ภาพขนาดไม่เกิน 10 MB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as picture:
                if picture.format not in MIME_TYPES:
                    raise ValueError("รองรับเฉพาะภาพ PNG, JPG และ WebP")
                mime = MIME_TYPES[picture.format]
                if picture.width * picture.height > MAX_IMAGE_PIXELS:
                    raise ValueError("กรุณาใช้ภาพไม่เกิน 24 ล้านพิกเซล")
                if getattr(picture, "n_frames", 1) != 1:
                    raise ValueError("กรุณาใช้ภาพนิ่งสำหรับพื้นหลัง")
                picture.verify()
            # Decode too: a valid header alone does not guarantee a usable image.
            with Image.open(BytesIO(raw)) as picture:
                picture.load()
        return mime
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as error:
        raise ValueError("ไม่สามารถอ่านภาพนี้ได้ กรุณาเลือกไฟล์ภาพใหม่") from error


@lru_cache(maxsize=2)
def _default_image(path, modified, size):
    raw = Path(path).read_bytes()
    return raw, validate_background(raw)


def default_image():
    path = ROOT / "bg.png"
    try:
        stat = path.stat()
        return _default_image(str(path), stat.st_mtime_ns, stat.st_size)
    except (OSError, ValueError):
        return b"", "image/png"


@lru_cache(maxsize=4)
def image_uri(raw, mime):
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")


def background_value(settings):
    raw, mime = settings["image"], settings["mime"]
    if not settings["enabled"] or not raw:
        return "none"
    alpha = settings["darkness"] / 100
    return (f"linear-gradient(rgba(12,19,28,{alpha}),rgba(12,19,28,{alpha})),"
            f'url("{image_uri(raw, mime)}")')


def background_css(settings):
    # A fixed layer works on mobile too, without background-attachment: fixed.
    return (".stApp::before {background-image:" + background_value(settings) + ";"
            "background-position:" + settings["position"] + ";}")
