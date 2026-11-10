"""Server-side settings and password-only access. Never send this store to clients."""

from contextlib import contextmanager
import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from pathlib import Path

from google.auth.exceptions import GoogleAuthError

from appearance import (DEFAULT_APPEARANCE, DEFAULT_NAME, DEFAULT_THEME, POSITIONS,
                        default_image, validate_background)
from login_history import DEFAULT_AUDIT, destination, local_day_start, parse_service_account, validate_destination
from formula_source import bundled_library, inspect_values
from sheets import fetch_matrix

ITERATIONS = 600_000
SESSION_SECONDS = 12 * 60 * 60
MAX_FAILURES = 10
LOCK_SECONDS = 60


class AccessDenied(ValueError):
    pass


class LoginLimited(ValueError):
    pass


def validate_password(password):
    if not isinstance(password, str) or not 8 <= len(password) <= 256 or not password.strip():
        raise ValueError("รหัสผ่านต้องมี 8–256 ตัวอักษร")


def hash_password(password):
    validate_password(password)
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), ITERATIONS).hex()
    return f"pbkdf2_sha256${ITERATIONS}${salt}${digest}"


def verify_password(password, encoded):
    if not isinstance(password, str) or len(password) > 256 or not encoded:
        return False
    try:
        algorithm, iterations, salt, expected = encoded.split("$")
        if algorithm != "pbkdf2_sha256" or int(iterations) != ITERATIONS:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations)).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def credential(password):
    return {"hash": hash_password(password), "version": secrets.token_hex(32)}


class SettingsStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS login_limit (id INTEGER PRIMARY KEY CHECK(id=1), failures INTEGER NOT NULL, started REAL NOT NULL, blocked_until REAL NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS background_themes (id TEXT PRIMARY KEY, name TEXT NOT NULL, mime TEXT NOT NULL, image BLOB NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS login_events (id TEXT PRIMARY KEY, occurred_at REAL NOT NULL, role TEXT NOT NULL, synced_destination TEXT NOT NULL DEFAULT '')")
            db.execute("CREATE TABLE IF NOT EXISTS audit_sync (id INTEGER PRIMARY KEY CHECK(id=1), owner TEXT NOT NULL, lease_until REAL NOT NULL, last_attempt REAL NOT NULL, last_success REAL NOT NULL, error TEXT NOT NULL)")
            db.execute("INSERT OR IGNORE INTO audit_sync VALUES (1, '', 0, 0, 0, '')")
            db.execute("INSERT OR IGNORE INTO settings VALUES (1, ?)", (json.dumps({
                "admin": None, "member": None, "api_key": "", "model": ""
            }),))
            db.execute("INSERT OR IGNORE INTO login_limit VALUES (1, 0, 0, 0)")

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _read(db):
        return json.loads(db.execute("SELECT value FROM settings WHERE id=1").fetchone()[0])

    @staticmethod
    def _write(db, config):
        db.execute("UPDATE settings SET value=? WHERE id=1", (json.dumps(config),))

    def configured(self):
        with self._connection() as db:
            return bool(self._read(db)["admin"])

    def bootstrap(self, admin_password, member_password="", api_key="", model=""):
        """One-time initialization from a trusted CLI or server secrets, never a public form."""
        with self._connection() as db:
            config = self._read(db)
            if config["admin"] or not admin_password:
                return False
            if member_password and hmac.compare_digest(admin_password.encode(), member_password.encode()):
                raise ValueError("รหัสผู้ดูแลและรหัสสมาชิกต้องต่างกัน")
            config.update(admin=credential(admin_password),
                          member=credential(member_password) if member_password else None,
                          api_key=api_key.strip(), model=model.strip())
            self._write(db, config)
            return True

    @staticmethod
    def _role(config, ticket):
        if not isinstance(ticket, dict):
            return None
        role = ticket.get("role")
        stored = config.get(role) if role in ("admin", "member") else None
        if (not stored or not isinstance(ticket.get("version"), str)
                or not isinstance(ticket.get("expires"), (int, float))
                or not ticket["expires"] > time.time()):
            return None
        return role if hmac.compare_digest(stored["version"], ticket["version"]) else None

    def role(self, ticket):
        with self._connection() as db:
            return self._role(self._read(db), ticket)

    @classmethod
    def _require(cls, config, ticket, admin=False):
        role = cls._role(config, ticket)
        if not role or (admin and role != "admin"):
            raise AccessDenied("กรุณาเข้าสู่ระบบอีกครั้งด้วยสิทธิ์ที่ถูกต้อง")

    def login(self, password):
        # A shared server-side bucket survives reconnects; no trust in client-provided IPs.
        # Commit failed attempts before raising/returning so refreshing cannot reset them.
        with self._connection() as db:
            config = self._read(db)
            failures, started, blocked = db.execute(
                "SELECT failures, started, blocked_until FROM login_limit WHERE id=1").fetchone()
            now = time.time()
            if blocked > now:
                raise LoginLimited("กรอกรหัสผิดหลายครั้ง กรุณารอ 1 นาทีแล้วลองใหม่")
            if now - started >= 300 or (blocked and blocked <= now):
                failures, started = 0, now
            matched = None
            for role in ("member", "admin"):
                if config[role] and verify_password(password, config[role]["hash"]):
                    matched = role
            if matched:
                db.execute("UPDATE login_limit SET failures=0, started=0, blocked_until=0 WHERE id=1")
                db.execute("INSERT INTO login_events (id, occurred_at, role) VALUES (?, ?, ?)",
                           (secrets.token_hex(16), now, matched))
                return {"role": matched, "version": config[matched]["version"],
                        "expires": now + SESSION_SECONDS}
            failures += 1
            db.execute("UPDATE login_limit SET failures=?, started=?, blocked_until=? WHERE id=1",
                       (failures, started, now + LOCK_SECONDS if failures >= MAX_FAILURES else 0))
            return None

    def summary(self, ticket):
        with self._connection() as db:
            config = self._read(db)
            self._require(config, ticket, admin=True)
            return {"has_api_key": bool(config["api_key"]), "model": config["model"],
                    "has_member_password": bool(config["member"])}

    def ai_credentials(self, ticket):
        with self._connection() as db:
            config = self._read(db)
            self._require(config, ticket)
            return config["api_key"], config["model"]

    @staticmethod
    def _formula_library(config):
        source = config.get("formula_source")
        if source:
            return {"version": source["version"], "values": source["values"], "sheet_name": source["tab"]}
        return bundled_library()

    def formula_library(self, ticket):
        """Return only calculation data; never return the source credentials to a member."""
        with self._connection() as db:
            config = self._read(db)
            self._require(config, ticket)
            library = self._formula_library(config)
            return {key: library[key] for key in ("version", "values", "sheet_name")}

    def formula_settings(self, ticket):
        with self._connection() as db:
            config = self._read(db)
            self._require(config, ticket, admin=True)
            source = config.get("formula_source")
            if source:
                account = source["service_account"]
                return {key: source[key] for key in ("version", "url", "tab", "count", "updated_at", "public")} | {
                    "custom": True, "account_email": account["client_email"] if account else ""}
            library = bundled_library()
            count = inspect_values(library["values"], library["sheet_name"])
            return {"version": library["version"], "url": library.get("source_url", ""),
                    "tab": library["sheet_name"], "count": count, "updated_at": 0,
                    "custom": False, "public": False, "account_email": ""}

    def prepare_formula_source(self, ticket, url, tab, account_json=None, *, public=False):
        """Read once outside the DB lock. Keep this draft in the admin's server session."""
        with self._connection() as db:
            config = self._read(db)
            self._require(config, ticket, admin=True)
            previous = config.get("formula_source") or {}
            base_version = self._formula_library(config)["version"]
        sheet_id, tab = validate_destination(url, tab)
        if type(public) is not bool:
            raise ValueError("กรุณาเลือกวิธีอ่านชีตสูตร")
        if public:
            account = None
        else:
            account = parse_service_account(account_json) if account_json is not None else previous.get("service_account")
            if not account:
                raise ValueError("กรุณาอัปโหลด Service account JSON และแชร์ชีตสูตรให้บัญชีบริการเป็น Viewer")
        url = "https://docs.google.com/spreadsheets/d/" + sheet_id + "/edit"
        try:
            values = fetch_matrix(url, tab, account)
        except GoogleAuthError:
            raise ValueError("ยืนยันตัวตนกับ Google ไม่สำเร็จ กรุณาตรวจคีย์บัญชีบริการและสิทธิ์ Viewer ของชีตสูตร") from None
        count = inspect_values(values, tab)
        with self._connection() as db:
            current = self._read(db)
            self._require(current, ticket, admin=True)
            if self._formula_library(current)["version"] != base_version:
                raise ValueError("มีการเปลี่ยนฐานสูตรระหว่างโหลด กรุณาตรวจสอบและโหลดอีกครั้ง")
        return {"base_version": base_version, "url": url, "tab": tab, "public": public,
                "service_account": account, "values": values, "count": count,
                "prepared_at": time.time(), "owner": ticket["version"]}

    def apply_formula_source(self, ticket, draft):
        with self._connection() as db:
            config = self._read(db)
            self._require(config, ticket, admin=True)
            if (not isinstance(draft, dict) or draft.get("owner") != ticket["version"]
                    or time.time() - draft.get("prepared_at", 0) > 900
                    or draft.get("base_version") != self._formula_library(config)["version"]):
                raise ValueError("ตัวอย่างสูตรหมดอายุหรือฐานสูตรถูกเปลี่ยนแล้ว กรุณาตรวจสอบและโหลดใหม่")
            sheet_id, tab = validate_destination(draft["url"], draft["tab"])
            count = inspect_values(draft["values"], tab)
            public = draft["public"]
            if type(public) is not bool:
                raise ValueError("วิธีอ่านชีตสูตรไม่ถูกต้อง")
            account = None if public else parse_service_account(json.dumps(draft["service_account"]).encode())
            config["formula_source"] = {"url": "https://docs.google.com/spreadsheets/d/" + sheet_id + "/edit",
                "tab": tab, "public": public, "service_account": account,
                "values": draft["values"], "count": count, "version": secrets.token_hex(16),
                "updated_at": time.time()}
            self._write(db, config)

    def audit_settings(self, ticket):
        with self._connection() as db:
            config = self._read(db)
            self._require(config, ticket, admin=True)
            audit = {**DEFAULT_AUDIT, **config.get("audit_sheet", {})}
            account = audit.pop("service_account")
            total, pending = db.execute(
                "SELECT COUNT(*), COALESCE(SUM(synced_destination != ?), 0) FROM login_events", (destination(audit),)).fetchone()
            attempt, success, error, lease = db.execute(
                "SELECT last_attempt, last_success, error, lease_until FROM audit_sync WHERE id=1").fetchone()
            return {**audit, "account_email": account["client_email"] if account else "", "has_credentials": bool(account),
                    "total": total, "pending": pending, "last_attempt": attempt, "last_success": success,
                    "error": error, "syncing": lease > time.time()}

    def save_audit_settings(self, ticket, url, tab, enabled, account_json=None):
        with self._connection() as db:
            config = self._read(db)
            self._require(config, ticket, admin=True)
            previous = {**DEFAULT_AUDIT, **config.get("audit_sheet", {})}
            sheet_id, tab = validate_destination(url, tab) if url.strip() else ("", DEFAULT_AUDIT["tab"])
            account = parse_service_account(account_json) if account_json is not None else previous["service_account"]
            if type(enabled) is not bool:
                raise ValueError("การตั้งค่าประวัติไม่ถูกต้อง")
            if enabled and (not sheet_id or not account):
                raise ValueError("กรุณาใส่ลิงก์ชีตและอัปโหลด Service account JSON ก่อนเปิดการส่งประวัติ")
            config["audit_sheet"] = {"spreadsheet_id": sheet_id, "tab": tab, "enabled": enabled,
                                     "service_account": account}
            self._write(db, config)
            db.execute("UPDATE audit_sync SET last_attempt=0, error='' WHERE id=1")

    def usage_counts(self, ticket):
        with self._connection() as db:
            config = self._read(db)
            self._require(config, ticket)
            start = local_day_start(time.time())
            total, today = db.execute("SELECT COUNT(*), COALESCE(SUM(occurred_at >= ? AND occurred_at < ?), 0) FROM login_events",
                                      (start, start + 86400)).fetchone()
            return {"total": total, "today": today}

    def login_history(self, ticket, limit=100):
        with self._connection() as db:
            config = self._read(db)
            self._require(config, ticket, admin=True)
            audit = {**DEFAULT_AUDIT, **config.get("audit_sheet", {})}
            rows = db.execute("SELECT id, occurred_at, role, synced_destination FROM login_events ORDER BY occurred_at DESC, rowid DESC LIMIT ?",
                              (max(1, min(500, int(limit))),)).fetchall()
            return [{"id": row[0], "occurred_at": row[1], "role": row[2], "synced": row[3] == destination(audit)} for row in rows]

    def claim_audit_sync(self, ticket, *, force=False):
        with self._connection() as db:
            config = self._read(db)
            self._require(config, ticket, admin=force)
            audit = {**DEFAULT_AUDIT, **config.get("audit_sheet", {})}
            if not (audit["enabled"] and audit["spreadsheet_id"] and audit["service_account"]):
                return None
            lease, attempt = db.execute("SELECT lease_until, last_attempt FROM audit_sync WHERE id=1").fetchone()
            now = time.time()
            if lease > now or (not force and now - attempt < 60):
                return None
            dest = destination(audit)
            rows = db.execute("SELECT id, occurred_at, role FROM login_events WHERE synced_destination != ? ORDER BY occurred_at, rowid LIMIT 100", (dest,)).fetchall()
            if not rows:
                return None
            owner = secrets.token_hex(16)
            db.execute("UPDATE audit_sync SET owner=?, lease_until=?, last_attempt=?, error='' WHERE id=1", (owner, now + 180, now))
            return {"owner": owner, "destination": dest, "config": audit,
                    "events": [{"id": row[0], "occurred_at": row[1], "role": row[2]} for row in rows]}

    def finish_audit_sync(self, owner, dest, sent_ids, error):
        with self._connection() as db:
            if db.execute("SELECT owner FROM audit_sync WHERE id=1").fetchone()[0] != owner:
                return
            db.executemany("UPDATE login_events SET synced_destination=? WHERE id=?", ((dest, event_id) for event_id in sent_ids))
            db.execute("UPDATE audit_sync SET owner='', lease_until=0, error=? WHERE id=1", (error,))
            if not error:
                db.execute("UPDATE audit_sync SET last_success=? WHERE id=1", (time.time(),))

    def appearance(self, theme_id=None):
        """Only public display data; also needed on the anonymous login screen."""
        with self._connection() as db:
            config = self._read(db)
            settings = {**DEFAULT_APPEARANCE, **config.get("appearance", {})}
            selected = settings["theme_id"] if theme_id is None else theme_id
            row = db.execute("SELECT name, mime, image FROM background_themes WHERE id=?", (selected,)).fetchone()
        if row:
            name, mime, raw = row
        else:
            selected, name = DEFAULT_THEME, DEFAULT_NAME
            raw, mime = default_image()
        return {**settings, "theme_id": selected, "name": name, "mime": mime, "image": raw}

    def background_themes(self, ticket):
        with self._connection() as db:
            self._require(self._read(db), ticket, admin=True)
            rows = db.execute("SELECT id, name FROM background_themes ORDER BY rowid").fetchall()
        return [{"id": DEFAULT_THEME, "name": DEFAULT_NAME},
                *({"id": row[0], "name": row[1]} for row in rows)]

    def add_background(self, ticket, name, raw):
        # Check permission before decoding a potentially large upload.
        with self._connection() as db:
            self._require(self._read(db), ticket, admin=True)
        name = name.strip()
        if not name or len(name) > 80:
            raise ValueError("กรุณาตั้งชื่อธีม 1–80 ตัวอักษร")
        mime = validate_background(raw)
        theme_id = secrets.token_hex(16)
        with self._connection() as db:
            self._require(self._read(db), ticket, admin=True)
            db.execute("INSERT INTO background_themes VALUES (?, ?, ?, ?)", (theme_id, name, mime, raw))
        return theme_id

    def save_appearance(self, ticket, theme_id, enabled, darkness, position):
        with self._connection() as db:
            config = self._read(db)
            self._require(config, ticket, admin=True)
            if (type(enabled) is not bool or type(darkness) is not int or not 0 <= darkness <= 90
                    or position not in POSITIONS):
                raise ValueError("การตั้งค่าพื้นหลังไม่ถูกต้อง")
            if theme_id != DEFAULT_THEME and not db.execute(
                    "SELECT 1 FROM background_themes WHERE id=?", (theme_id,)).fetchone():
                raise ValueError("ไม่พบภาพธีมที่เลือก กรุณาเลือกใหม่")
            config["appearance"] = {"theme_id": theme_id, "enabled": enabled,
                                    "darkness": darkness, "position": position}
            self._write(db, config)

    def save_ai(self, ticket, api_key, model, clear_key=False):
        model, api_key = model.strip(), api_key.strip()
        if len(model) > 200 or len(api_key) > 500 or any(c in model + api_key for c in "\r\n"):
            raise ValueError("API key หรือชื่อโมเดลมีรูปแบบไม่ถูกต้อง")
        if clear_key and api_key:
            raise ValueError("เลือกอย่างใดอย่างหนึ่ง: ใส่คีย์ใหม่ หรือลบคีย์เดิม")
        with self._connection() as db:
            config = self._read(db)
            self._require(config, ticket, admin=True)
            if (api_key or (config["api_key"] and not clear_key)) and not model:
                raise ValueError("กรุณาระบุชื่อโมเดล")
            if clear_key:
                config["api_key"] = ""
            elif api_key:
                config["api_key"] = api_key
            config["model"] = model
            self._write(db, config)

    def change_password(self, ticket, role, password):
        if role not in ("admin", "member"):
            raise ValueError("ประเภทผู้ใช้ไม่ถูกต้อง")
        validate_password(password)
        with self._connection() as db:
            config = self._read(db)
            self._require(config, ticket, admin=True)
            other = config["member" if role == "admin" else "admin"]
            if other and verify_password(password, other["hash"]):
                raise ValueError("รหัสผู้ดูแลและรหัสสมาชิกต้องต่างกัน")
            config[role] = credential(password)
            self._write(db, config)

    def reset_admin_locally(self, password):
        """Recovery is available only to the server operator through the CLI."""
        validate_password(password)
        with self._connection() as db:
            config = self._read(db)
            if config["member"] and verify_password(password, config["member"]["hash"]):
                raise ValueError("รหัสผู้ดูแลและรหัสสมาชิกต้องต่างกัน")
            config["admin"] = credential(password)
            self._write(db, config)
            db.execute("UPDATE login_limit SET failures=0, started=0, blocked_until=0 WHERE id=1")
