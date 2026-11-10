"""Shared public branding for the app, login, and administrator screens."""

import base64
from functools import lru_cache
from pathlib import Path

APP_NAME = "ซื้อแล้วรวย"
LOGO_PATH = Path(__file__).resolve().parent / "assets" / "logo.png"


@lru_cache(maxsize=1)
def logo_data_uri():
    return "data:image/png;base64," + base64.b64encode(LOGO_PATH.read_bytes()).decode("ascii")


def brand_html(*, compact=False):
    variant = " brand--compact" if compact else ""
    return (f'<div class="brand{variant}">'
            f'<img class="brand-logo" src="{logo_data_uri()}" alt="โลโก้ {APP_NAME}">'
            f'<div class="brand-copy"><div class="brand-name">{APP_NAME}</div>'
            '<div class="brand-sub">วิเคราะห์เลข · เทียบสูตรด้วย AI</div></div></div>')
