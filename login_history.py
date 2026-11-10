"""Authenticated login audit delivery to Google Sheets; no Streamlit dependencies."""

from datetime import datetime, timedelta, timezone
import json
import re
import secrets
import threading
from urllib.parse import quote

from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import AuthorizedSession
from google.oauth2.service_account import Credentials
import requests

from sheets import spreadsheet_id

HEADERS = ["รหัสรายการ", "เวลา (UTC)", "เวลา (UTC+08:00)", "สิทธิ์ผู้ใช้", "กิจกรรม"]
LOCAL_TZ = timezone(timedelta(hours=8))
DEFAULT_AUDIT = {"enabled": False, "spreadsheet_id": "", "tab": "ประวัติเข้าใช้งาน",
                 "service_account": None}
ROLES = {"admin": "ผู้ดูแล", "member": "สมาชิก"}


class AuditSyncError(ValueError):
    """Safe, user-facing messages without Google response bodies or credentials."""


def parse_service_account(raw):
    if not isinstance(raw, bytes) or not raw or len(raw) > 64_000:
        raise ValueError("กรุณาใช้ไฟล์ Service account JSON ขนาดไม่เกิน 64 KB")
    try:
        source = json.loads(raw)
        if not isinstance(source, dict) or source.get("type") != "service_account":
            raise ValueError()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.iam\.gserviceaccount\.com", source.get("client_email", "")):
            raise ValueError()
        info = {key: source[key] for key in ("type", "project_id", "private_key_id", "private_key", "client_email", "client_id")}
        info["token_uri"] = "https://oauth2.googleapis.com/token"
        credentials(info)  # Validate the private key locally; no network request.
        return info
    except (ValueError, TypeError, KeyError, GoogleAuthError) as error:
        raise ValueError("ไฟล์บัญชีบริการไม่ถูกต้อง กรุณาใช้ JSON key จาก Google Cloud") from error


def credentials(info):
    return Credentials.from_service_account_info(
        {**info, "token_uri": "https://oauth2.googleapis.com/token", "universe_domain": "googleapis.com"},
        scopes=["https://www.googleapis.com/auth/spreadsheets"])


def validate_destination(url, tab):
    sheet_id = spreadsheet_id(url.strip())
    tab = tab.strip()
    if not tab or len(tab) > 100 or re.search(r"[\[\]*?:/\\\x00-\x1f]", tab):
        raise ValueError("ชื่อแท็บไม่ถูกต้อง กรุณาใช้ชื่อไม่เกิน 100 ตัวอักษร")
    return sheet_id, tab


def destination(config):
    return config["spreadsheet_id"] + "/" + config["tab"]


def local_time(timestamp):
    return datetime.fromtimestamp(timestamp, LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")


def local_day_start(timestamp):
    return datetime.fromtimestamp(timestamp, LOCAL_TZ).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def event_row(event):
    return [event["id"], datetime.fromtimestamp(event["occurred_at"], timezone.utc).isoformat(timespec="seconds"),
            local_time(event["occurred_at"]), ROLES[event["role"]], "เข้าสู่ระบบสำเร็จ"]


class SheetsAuditClient:
    def __init__(self, session, sheet_id, tab):
        self.session = session
        self.base = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}"
        self.tab = tab

    def request(self, method, suffix="", **kwargs):
        try:
            with self.session.request(method, self.base + suffix, timeout=12, **kwargs) as response:
                status = response.status_code
                if status in (401, 403):
                    raise AuditSyncError("Google ปฏิเสธสิทธิ์ ตรวจว่าเปิด Sheets API และแชร์ชีตให้บัญชีบริการเป็น Editor แล้ว")
                if status == 404:
                    raise AuditSyncError("ไม่พบชีต หรือบัญชีบริการยังไม่มีสิทธิ์เข้าถึง")
                if status == 429 or status >= 500:
                    raise AuditSyncError("Google Sheets ยังไม่พร้อม รายการยังรอส่งและจะลองใหม่เมื่อมีการใช้งาน")
                if status >= 400:
                    raise AuditSyncError("ส่งประวัติไม่สำเร็จ กรุณาตรวจลิงก์ชีตและชื่อแท็บ")
                return response.json()
        except (requests.RequestException, GoogleAuthError) as error:
            raise AuditSyncError("เชื่อมต่อ Google Sheets ไม่สำเร็จ รายการยังรอส่ง กรุณาตรวจอินเทอร์เน็ตและบัญชีบริการ") from error
        except (ValueError, KeyError) as error:
            if isinstance(error, AuditSyncError):
                raise
            raise AuditSyncError("Google Sheets ตอบข้อมูลผิดรูปแบบ รายการยังรอส่ง") from error

    def range_path(self, cells):
        return "/values/" + quote("'" + self.tab.replace("'", "''") + "'!" + cells, safe="")

    def deliver(self, events):
        metadata = self.request("GET", params={"fields": "sheets.properties"})
        props = next((sheet["properties"] for sheet in metadata.get("sheets", [])
                      if sheet["properties"]["title"] == self.tab), None)
        if props is None:
            sheet_id = secrets.randbelow(2**30)
            result = self.request("POST", ":batchUpdate", json={"requests": [
                {"addSheet": {"properties": {"sheetId": sheet_id, "title": self.tab,
                    "gridProperties": {"rowCount": 1000, "columnCount": 5, "frozenRowCount": 1}}}},
                {"updateCells": {"start": {"sheetId": sheet_id, "rowIndex": 0, "columnIndex": 0},
                    "rows": [{"values": [{"userEnteredValue": {"stringValue": label}, "userEnteredFormat": {
                        "backgroundColor": {"red": .94, "green": .94, "blue": .94}, "textFormat": {"bold": True}}}
                        for label in HEADERS]}], "fields": "userEnteredValue,userEnteredFormat"}},
                {"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 5},
                    "properties": {"pixelSize": 240}, "fields": "pixelSize"}}]})
            props = result["replies"][0]["addSheet"]["properties"]
        header = self.request("GET", self.range_path("A1:E1")).get("values", [])
        if header != [HEADERS]:
            raise AuditSyncError("หัวตารางในแท็บนี้ไม่ตรงกับประวัติการเข้าใช้งาน กรุณาใช้ชื่อแท็บใหม่เพื่อรักษาข้อมูลเดิม")
        rows = props["gridProperties"]["rowCount"]
        if rows > 200_000:
            raise AuditSyncError("แท็บประวัติมีขนาดใหญ่ กรุณาเปลี่ยนไปใช้แท็บใหม่")
        existing = self.request("GET", self.range_path(f"A2:A{max(rows, 2)}")).get("values", [])
        known_ids = {row[0] for row in existing if row}
        new_events = [event for event in events if event["id"] not in known_ids]
        if new_events:
            result = self.request("POST", self.range_path("A:E") + ":append",
                                  params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
                                  json={"values": [event_row(event) for event in new_events]})
            if result.get("updates", {}).get("updatedRows") != len(new_events):
                raise AuditSyncError("ยังยืนยันการบันทึกลงชีตไม่ได้ ระบบจะตรวจรหัสรายการก่อนลองส่งใหม่")
        return [event["id"] for event in events]


def deliver_job(store, job):
    sent, error = [], ""
    try:
        with AuthorizedSession(credentials(job["config"]["service_account"]), refresh_timeout=12) as session:
            client = SheetsAuditClient(session, job["config"]["spreadsheet_id"], job["config"]["tab"])
            sent = client.deliver(job["events"])
    except AuditSyncError as caught:
        error = str(caught)
    except Exception:
        # Never propagate credential contents or provider exception text to the UI/logs.
        error = "ส่งประวัติไม่สำเร็จ รายการยังอยู่ในคิว กรุณาตรวจบัญชีบริการและลองใหม่"
    finally:
        store.finish_audit_sync(job["owner"], job["destination"], sent, error)


def schedule_sync(store, ticket, *, force=False):
    job = store.claim_audit_sync(ticket, force=force)
    if job:
        threading.Thread(target=deliver_job, args=(store, job), daemon=True, name="login-history-sync").start()
        return True
    return False
