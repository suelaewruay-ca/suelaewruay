"""Login and administrator screens; all writes are authorized by SettingsStore."""

import os
from html import escape
from pathlib import Path
import sqlite3

import streamlit as st

from admin_store import AccessDenied, LoginLimited, SettingsStore
from branding import APP_NAME, brand_html
from appearance import POSITIONS, background_css, background_value
from history_ui import audit_panel, usage_counter
from formula_ui import formula_panel
from login_history import schedule_sync

ROOT = Path(__file__).resolve().parent


def secret(name, default=""):
    try:
        return st.secrets.get(name, os.getenv(name, default))
    except (FileNotFoundError, KeyError):
        return os.getenv(name, default)


def get_store():
    store = SettingsStore(str(secret("ZANI_SETTINGS_DB", str(ROOT / ".private/settings.db"))))
    store.bootstrap(str(secret("ADMIN_PASSWORD")), str(secret("APP_PASSWORD")),
                    str(secret("GOOGLE_API_KEY")), str(secret("GEMINI_MODEL")))
    return store


def logout(message=None):
    st.session_state.clear()
    if message:
        st.session_state["login_notice"] = message
    st.rerun()


def authenticate(store, brand):
    ticket = st.session_state.get("access_ticket")
    role = store.role(ticket)
    if role:
        return role
    if ticket:
        # Clear previous user's uploads, history, password widgets and administrator state.
        st.session_state.clear()
        st.session_state["login_notice"] = "รหัสผ่านมีการเปลี่ยนแปลงหรือหมดเวลาใช้งาน กรุณาเข้าสู่ระบบใหม่"
    _, center, _ = st.columns([1, 1.3, 1])
    with center:
        brand()
        with st.container(border=True, key="login-panel"):
            st.subheader("เข้าสู่ระบบ")
            st.caption(f"กรอกรหัสผ่านเพื่อเข้าใช้งาน {APP_NAME}")
            if not store.configured():
                st.info("ระบบยังไม่เปิดให้เข้าใช้งาน กรุณาติดต่อผู้ดูแลเพื่อตั้งค่าครั้งแรก")
                return None
            if notice := st.session_state.pop("login_notice", None):
                st.info(notice)
            with st.form("login", clear_on_submit=True):
                entered = st.text_input("รหัสผ่าน", type="password", key="login_password", max_chars=256)
                submit = st.form_submit_button("เข้าสู่ระบบ →", type="primary", width="stretch")
            if submit:
                try:
                    ticket = store.login(entered)
                    if ticket:
                        st.session_state["access_ticket"] = ticket
                        st.session_state["active_page"] = "admin" if ticket["role"] == "admin" else "app"
                        st.rerun()
                    st.error("รหัสผ่านไม่ถูกต้อง")
                except LoginLimited as error:
                    st.error(str(error))
    return None


def navigation(role):
    with st.sidebar:
        st.markdown(brand_html(compact=True), unsafe_allow_html=True)
        st.caption("ผู้ดูแลระบบ" if role == "admin" else "สมาชิก")
        if role == "admin":
            if st.button("หน้าใช้งาน", key="nav_app", width="stretch"):
                st.session_state.active_page = "app"
                st.rerun()
            if st.button("หลังบ้าน", key="nav_admin", width="stretch"):
                st.session_state.active_page = "admin"
                st.rerun()
        if st.button("ออกจากระบบ", width="stretch"):
            logout()


def appearance_panel(store, ticket):
    current = store.appearance()
    themes = store.background_themes(ticket)
    names = {theme["id"]: theme["name"] for theme in themes}
    with st.container(border=True, key="appearance-panel"):
        st.subheader("ภาพพื้นหลังและธีมเทศกาล")
        st.caption("เลือกภาพจากคลังและดูตัวอย่างก่อนบันทึก ใช้ร่วมกันทั้งหน้าเข้าสู่ระบบ หน้าสมาชิก และหลังบ้าน")
        if new_theme := st.session_state.pop("appearance_select_next", None):
            st.session_state["appearance_theme"] = new_theme
        left, right = st.columns([1, 1.15], gap="large")
        with left:
            selected = st.selectbox("ธีมพื้นหลัง", list(names), index=list(names).index(current["theme_id"]),
                                    format_func=names.get, key="appearance_theme")
            enabled = st.toggle("แสดงภาพพื้นหลัง", value=current["enabled"], key="appearance_enabled")
            darkness = st.slider("ความเข้มของชั้นสีทับภาพ (%)", 0, 90, current["darkness"],
                                 help="ค่าสูงทำให้ภาพมืดลง แนะนำ 65–80% สำหรับภาพสีสด", key="appearance_darkness")
            position = st.selectbox("ตำแหน่งภาพ", list(POSITIONS),
                                    index=list(POSITIONS).index(current["position"]),
                                    format_func=POSITIONS.get, key="appearance_position")
            if st.button("บันทึกพื้นหลัง", type="primary", width="stretch"):
                try:
                    store.save_appearance(ticket, selected, enabled, darkness, position)
                    st.session_state["admin_notice"] = "บันทึกพื้นหลังแล้ว ผู้ใช้อื่นจะเห็นเมื่อรีเฟรชหรือใช้งานครั้งถัดไป"
                    st.rerun()
                except AccessDenied:
                    raise
                except ValueError as error:
                    st.error(str(error))
        with right:
            draft = {**store.appearance(selected), "enabled": enabled, "darkness": darkness, "position": position}
            style = escape(f"background-image:{background_value(draft)};background-position:{position}", quote=True)
            st.markdown(f'<div class="background-preview" style="{style}">'
                        '<div class="background-preview-card"><strong>ตัวอย่างพื้นหลัง</strong>'
                        '<p>สีทองและเขียวมิ้นต์บนแผงสีกรมท่า ช่วยให้ข้อความอ่านง่าย</p>'
                        '<span class="background-preview-action">วิเคราะห์เลข →</span></div></div>',
                        unsafe_allow_html=True)
            st.caption("ตัวอย่างโดยประมาณ · ภาพจะครอบเต็มจอและถูกตัดขอบตามสัดส่วนของแต่ละอุปกรณ์")
        with st.expander("เพิ่มภาพธีมใหม่"):
            st.caption("PNG, JPG หรือ WebP ไม่เกิน 10 MB · แนะนำแนวนอน 1920 × 1080 พิกเซล")
            with st.form("upload_background", clear_on_submit=True):
                name = st.text_input("ชื่อธีม", max_chars=80, placeholder="เช่น ปีใหม่ สงกรานต์ ลอยกระทง", key="new_background_name")
                upload = st.file_uploader("ภาพพื้นหลังใหม่", type=["png", "jpg", "jpeg", "webp"], key="new_background_file")
                add = st.form_submit_button("เพิ่มภาพเข้าคลัง", width="stretch")
            if add:
                try:
                    if upload is None:
                        raise ValueError("กรุณาเลือกภาพพื้นหลังก่อนเพิ่มเข้าคลัง")
                    theme_id = store.add_background(ticket, name, upload.getvalue())
                    st.session_state["appearance_select_next"] = theme_id
                    st.session_state["admin_notice"] = "เพิ่มภาพเข้าคลังแล้ว ตรวจตัวอย่างและกดบันทึกพื้นหลังเพื่อใช้งาน"
                    st.rerun()
                except AccessDenied:
                    raise
                except ValueError as error:
                    st.error(str(error))


def admin_panel(store, brand):
    ticket = st.session_state.get("access_ticket")
    summary = store.summary(ticket)  # Reject member and revoked sessions before rendering.
    brand()
    st.markdown('<div class="eyebrow">ADMIN / SETTINGS</div>', unsafe_allow_html=True)
    st.title("จัดการหลังบ้าน")
    st.caption("ตั้งค่าฐานสูตร การเชื่อมต่อ AI รหัสเข้าใช้งาน และภาพพื้นหลังของเว็บ")
    if notice := st.session_state.pop("admin_notice", None):
        st.success(notice)
    left, right = st.columns([1.15, 1], gap="large")
    with left, st.container(border=True, key="admin-ai-panel"):
        st.subheader("การเชื่อมต่อ AI")
        st.caption("บันทึก API key แล้ว" if summary["has_api_key"] else "ยังไม่ได้ตั้งค่า API key")
        st.caption("คีย์เดิมจะไม่แสดงในหน้าเว็บ เว้นช่องคีย์ใหม่ว่างไว้เพื่อใช้ค่าเดิม")
        with st.form("admin_ai", clear_on_submit=True):
            key = st.text_input("Gemini API key ใหม่", type="password", key="admin_api_key", max_chars=500)
            model = st.text_input("Gemini model", value=summary["model"], key="admin_model", max_chars=200,
                                  placeholder="ชื่อโมเดลที่บัญชีคุณใช้งานได้")
            clear = st.checkbox("ลบ API key ที่บันทึกไว้", key="admin_clear_api")
            save = st.form_submit_button("บันทึกการตั้งค่า AI", type="primary", width="stretch")
        if save:
            try:
                store.save_ai(ticket, key, model, clear_key=clear)
                st.session_state["clear_admin_fields"] = ["admin_api_key", "admin_model", "admin_clear_api"]
                st.session_state["admin_notice"] = "บันทึกการตั้งค่า AI แล้ว ทุกคนจะใช้ค่าใหม่เมื่อใช้งานครั้งถัดไป"
                st.rerun()
            except ValueError as error:
                st.error(str(error))
        st.caption("ภาพและข้อมูลวิเคราะห์จะส่งไป Google เฉพาะเมื่อผู้ใช้กดใช้ AI")
    with right:
        for role, label in (("member", "สมาชิก"), ("admin", "ผู้ดูแล")):
            with st.container(border=True, key=f"admin-{role}-panel"):
                st.subheader(f"รหัสผ่าน{label}")
                if role == "member":
                    st.caption("สมาชิกใช้รหัสร่วมกันเพื่อเข้าใช้งาน" if summary["has_member_password"]
                               else "ตั้งรหัสสมาชิกเพื่อเปิดให้เข้าใช้งาน")
                st.caption("อย่างน้อย 8 ตัวอักษร · รหัสสมาชิกกับผู้ดูแลต้องต่างกัน")
                with st.form(f"change_{role}", clear_on_submit=True):
                    password = st.text_input(f"รหัส{label}ใหม่", type="password", key=f"new_{role}_password", max_chars=256)
                    confirmation = st.text_input(f"ยืนยันรหัส{label}ใหม่", type="password", key=f"confirm_{role}_password", max_chars=256)
                    save = st.form_submit_button(f"บันทึกรหัส{label}", width="stretch")
                if save:
                    try:
                        if password != confirmation:
                            raise ValueError("รหัสผ่านทั้งสองช่องไม่ตรงกัน")
                        store.change_password(ticket, role, password)
                        if role == "admin":
                            logout("เปลี่ยนรหัสผู้ดูแลแล้ว กรุณาเข้าสู่ระบบด้วยรหัสใหม่")
                        st.session_state["clear_admin_fields"] = ["new_member_password", "confirm_member_password"]
                        st.session_state["admin_notice"] = "เปลี่ยนรหัสสมาชิกแล้ว สมาชิกต้องเข้าสู่ระบบใหม่เมื่อใช้งานครั้งถัดไป"
                        st.rerun()
                    except ValueError as error:
                        st.error(str(error))

    formula_panel(store, ticket)
    appearance_panel(store, ticket)
    audit_panel(store, ticket)


def run_protected(main, brand):
    for key in st.session_state.pop("clear_admin_fields", []):
        st.session_state.pop(key, None)
    try:
        store = get_store()
        st.markdown("<style>" + background_css(store.appearance()) + "</style>", unsafe_allow_html=True)
        role = authenticate(store, brand)
        if not role:
            return
        schedule_sync(store, st.session_state.get("access_ticket"))
        navigation(role)
        if role == "admin" and st.session_state.get("active_page") == "admin":
            admin_panel(store, brand)
        else:
            key, model = store.ai_credentials(st.session_state.get("access_ticket"))
            main(key, model, store.formula_library(st.session_state.get("access_ticket")))
        usage_counter(store, st.session_state.get("access_ticket"))
    except AccessDenied:
        logout("กรุณาเข้าสู่ระบบใหม่")
    except (OSError, sqlite3.Error):
        st.error("ไม่สามารถอ่านหรือบันทึกการตั้งค่าระบบได้ กรุณาติดต่อผู้ดูแล")
    except ValueError:
        st.error("การตั้งค่าเริ่มต้นไม่ถูกต้อง กรุณาให้ผู้ดูแลตรวจรหัสผ่านในค่าฝั่งเซิร์ฟเวอร์")
