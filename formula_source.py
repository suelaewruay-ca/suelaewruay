"""Validate complete formula snapshots before an administrator activates them."""

import hashlib
import json
import math
from pathlib import Path

from sheets import MAX_BYTES, parse_matrix

ROOT = Path(__file__).resolve().parent


def bundled_library():
    raw = (ROOT / "data/formulas_snapshot.json").read_bytes()
    snapshot = json.loads(raw)
    return {**snapshot, "version": "bundled-" + hashlib.sha256(raw).hexdigest()}


def inspect_values(values, tab):
    if not isinstance(values, list) or len(values) != 11:
        raise ValueError("ตารางสูตรต้องมีหัวตาราง 1 แถว และเลขตั้งต้น 0–9 อีก 10 แถว")
    if any(not isinstance(row, list) or not 1 <= len(row) <= 201 for row in values):
        raise ValueError("ตารางสูตรต้องมีคอลัมน์เลขตั้งต้น และรองรับไม่เกิน 200 สูตร")
    for row in values:
        for cell in row:
            if (type(cell) not in (str, int, float) or len(str(cell)) > 200
                    or (isinstance(cell, float) and not math.isfinite(cell))):
                raise ValueError("ข้อมูลแต่ละช่องต้องเป็นข้อความหรือตัวเลขไม่เกิน 200 ตัวอักษร")
    if len(json.dumps(values, ensure_ascii=False).encode("utf-8")) > MAX_BYTES:
        raise ValueError("ข้อมูลสูตรเกิน 2 MB กรุณาแยกชุดสูตร")
    formulas, rejected = parse_matrix(values, tab)
    if rejected:
        details = "; ".join(item["reason"] for item in rejected[:3])
        raise ValueError(f"พบ {len(rejected)} สูตรไม่ผ่านการตรวจ: {details} กรุณาแก้ชีตก่อนนำเข้า")
    return len(formulas)
