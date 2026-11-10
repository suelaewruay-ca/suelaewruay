"""Read-only Google Sheets adapter for the user's seed-by-formula matrix."""

import csv
import io
import re
from urllib.parse import urlparse, quote

import requests

from engine import DIGITS, formula_from_dict

DEFAULT_URL = "https://docs.google.com/spreadsheets/d/1pV5479t6AY_-hIYYr5kUEjpUSGT0HDEFs-5Cbx03kiU/edit?gid=1717121509"
DEFAULT_TAB = "สูตร"
MAX_BYTES = 2_000_000


def spreadsheet_id(url):
    if re.fullmatch(r"[A-Za-z0-9_-]{20,100}", url):
        return url
    parsed = urlparse(url)
    match = re.fullmatch(r"/spreadsheets/d/([A-Za-z0-9_-]{20,100})(?:/.*)?", parsed.path)
    if parsed.scheme != "https" or parsed.hostname != "docs.google.com" or not match:
        raise ValueError("ใช้ลิงก์ Google Sheets ที่ขึ้นต้นด้วย https://docs.google.com/spreadsheets/d/ เท่านั้น")
    return match.group(1)


def column_name(index):
    result = ""
    while index:
        index, rem = divmod(index - 1, 26)
        result = chr(65 + rem) + result
    return result


def parse_triple(value):
    text = str(value).strip()
    # CSV / FORMATTED_VALUE preserves 058. Numeric cells such as 58 mean 058.
    if re.fullmatch(r"[0-9]{1,3}", text):
        text = text.zfill(3)
        digits = [int(digit) for digit in text]
    else:
        tokens = re.split(r"[\s,;|\-]+", text)
        if len(tokens) != 3 or any(not re.fullmatch(r"[0-9]", token) for token in tokens):
            raise ValueError("ต้องเป็นเลข 3 ตัว เช่น 058 หรือ 0,5,8")
        digits = [int(token) for token in tokens]
    if len(set(digits)) != 3:
        raise ValueError("เลขเว้น 3 ตัวต้องไม่ซ้ำกัน")
    return digits


def parse_matrix(values, source="สูตร"):
    if not isinstance(values, list) or len(values) < 11 or len(values) > 100:
        raise ValueError("ตารางต้องมีหัวคอลัมน์ และเลขตั้งต้นครบ 0–9")
    if len(values[0]) > 201:
        raise ValueError("รองรับสูงสุด 200 สูตรต่อชุด")
    if str(values[0][0]).strip().lstrip('\ufeff') != "เลขตั้งต้น":
        raise ValueError("ช่อง A1 ต้องเป็น เลขตั้งต้น และ B1 เป็นต้นไปเป็นชื่อสูตร")
    seed_rows = {}
    for row_index, row in enumerate(values[1:], start=2):
        if not any(str(cell).strip() for cell in row):
            continue
        seed = str(row[0]).strip()
        if seed not in set(map(str, DIGITS)) or seed in seed_rows:
            raise ValueError(f"แถว {row_index}: เลขตั้งต้นต้องเป็น 0–9 และไม่ซ้ำ")
        seed_rows[seed] = (row_index, row)
    if set(seed_rows) != set(map(str, DIGITS)):
        raise ValueError("เลขตั้งต้นไม่ครบ 0–9")
    formulas, errors = [], []
    header = values[0]
    width = max(len(row) for row in values)
    for col in range(1, width):
        name = str(header[col]).strip() if col < len(header) else ""
        if not name and all(len(row) <= col or not str(row[col]).strip() for _, row in seed_rows.values()):
            continue
        cell_column = column_name(col + 1)
        try:
            if not name:
                raise ValueError("ขาดชื่อสูตรในแถว 1")
            mapping = {}
            for seed, (row_index, row) in seed_rows.items():
                try:
                    mapping[seed] = parse_triple(row[col] if col < len(row) else "")
                except ValueError as error:
                    raise ValueError(f"{cell_column}{row_index}: {error}") from error
            formulas.append(formula_from_dict({"id": f"sheet-{cell_column}", "name": name,
                "kind": "lookup", "params": {"mapping": mapping}, "min_history": 1,
                "source": f"{source}!{cell_column}1:{cell_column}11",
                "description": "ใช้ผลล่าสุดเป็นเลขตั้งต้น เว้นเลข 3 ตัวจากช่องของสูตร เลือกอีก 7 ตัว"}))
        except ValueError as error:
            errors.append({"formula": name or cell_column, "reason": str(error)})
    if not formulas:
        raise ValueError("ไม่พบสูตรที่มีข้อมูลครบและถูกต้อง")
    return formulas, errors


def _read_limited(response):
    response.raise_for_status()
    chunks, total = [], 0
    for chunk in response.iter_content(65536):
        total += len(chunk)
        if total > MAX_BYTES:
            raise ValueError("ข้อมูลชีตเกิน 2 MB กรุณาแยกชุดสูตร")
        chunks.append(chunk)
    return b"".join(chunks)


def fetch_matrix(url, tab, service_account=None):
    sheet_id = spreadsheet_id(url)
    if not tab.strip() or len(tab) > 100:
        raise ValueError("กรุณาระบุชื่อแท็บ")
    try:
        if service_account:
            from google.oauth2.service_account import Credentials
            from google.auth.transport.requests import AuthorizedSession
            import json
            info = dict(service_account)
            # Credential endpoints are fixed; source files cannot control requests.
            info["token_uri"] = "https://oauth2.googleapis.com/token"
            info["universe_domain"] = "googleapis.com"
            creds = Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
            cell_range = "'" + tab.replace("'", "''") + "'!A1:GT12"
            with AuthorizedSession(creds, refresh_timeout=12) as session:
                with session.get(f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{quote(cell_range, safe='')}",
                                 params={"valueRenderOption": "FORMATTED_VALUE"}, timeout=30, stream=True) as response:
                    try:
                        values = json.loads(_read_limited(response))["values"]
                    except (KeyError, TypeError, json.JSONDecodeError):
                        raise ValueError("Google Sheets ไม่ได้ส่งตารางสูตร กรุณาตรวจชื่อแท็บและข้อมูลในชีต") from None
        else:
            with requests.get(f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq",
                              params={"tqx": "out:csv", "sheet": tab, "headers": "0", "range": "A1:GT12"},
                              timeout=30, stream=True) as response:
                body = _read_limited(response).decode("utf-8-sig")
            if body.lstrip().startswith("<"):
                raise ValueError("ชีตนี้ต้องมีสิทธิ์อ่าน กรุณาเลือก Service account และแชร์ชีตให้บัญชีบริการเป็น Viewer")
            values = list(csv.reader(io.StringIO(body)))
        if not isinstance(values, list) or any(not isinstance(row, list) for row in values):
            raise ValueError("Google Sheets ตอบตารางสูตรผิดรูปแบบ กรุณาตรวจข้อมูลในชีต")
        # GT is a sentinel column, beyond 200 supported formulas; never silently truncate.
        if any(len(row) > 201 and any(str(v).strip() for v in row[201:]) for row in values):
            raise ValueError("มีมากกว่า 200 สูตร กรุณาแยกชุดก่อนโหลด")
        if len(values) > 11 and any(str(v).strip() for row in values[11:] for v in row):
            raise ValueError("พบข้อมูลหลังแถว 11 กรุณาใช้รูปแบบหัวตาราง + เลขตั้งต้น 10 แถว")
        return values[:11]
    except requests.HTTPError as error:
        if error.response is not None and error.response.status_code in (401, 403):
            raise ValueError("ชีตนี้ต้องมีสิทธิ์อ่าน: เปิด Sheets API และแชร์ให้บัญชีบริการเป็น Viewer") from None
        raise ValueError("Google Sheets ส่งข้อผิดพลาด ตรวจลิงก์ ชื่อแท็บ และลองใหม่") from error
    except requests.RequestException as error:
        raise ValueError("โหลดชีตไม่ได้ ตรวจอินเทอร์เน็ต ลิงก์ ชื่อแท็บ และสิทธิ์ Viewer ของ service account") from error

