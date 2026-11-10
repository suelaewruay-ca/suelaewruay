"""Login history is rendered only after server-side authorization."""

import streamlit as st

from admin_store import AccessDenied
from login_history import local_time, schedule_sync


def usage_counter(store, ticket):
    counts = store.usage_counts(ticket)
    st.markdown('<div class="usage-summary" aria-label="จำนวนครั้งเข้าใช้งาน">'
                '<div class="usage-title">จำนวนครั้งเข้าใช้งาน</div><div class="usage-counters">'
                f'<span>เข้าใช้งานทั้งหมด <strong>{counts["total"]:,}</strong> ครั้ง</span>'
                f'<span>วันนี้ <strong>{counts["today"]:,}</strong> ครั้ง</span>'
                '</div></div>', unsafe_allow_html=True)


def audit_panel(store, ticket):
    status = store.audit_settings(ticket)
    with st.container(border=True, key="audit-panel"):
        st.subheader("สถิติการเข้าใช้งาน")
        st.caption("บันทึกเมื่อเข้าสู่ระบบสำเร็จเท่านั้น การกดปุ่มหรือเปลี่ยนหน้าไม่เพิ่มรายการล็อกอิน")
        left, right = st.columns(2)
        left.metric("เข้าสู่ระบบทั้งหมด", status["total"])
        right.metric("รอส่งไป Google Sheets", status["pending"])
        if notice := st.session_state.pop("audit_notice", None):
            st.success(notice)
        if status["syncing"]:
            st.info("กำลังส่งประวัติไป Google Sheets กดรีเฟรชสถานะเพื่อตรวจผล")
        elif status["error"]:
            st.warning(status["error"])
        elif status["last_success"]:
            st.caption("ส่งสำเร็จล่าสุด: " + local_time(status["last_success"]) + " (UTC+08:00)")
        if not status["enabled"]:
            st.info("เก็บประวัติบนเครื่องแล้ว ยังไม่เปิดการส่งไป Google Sheets ตั้งค่าการเชื่อมต่อด้านล่างเพื่อเริ่มส่ง")
        if status["spreadsheet_id"]:
            st.link_button("เปิดชีตประวัติ", "https://docs.google.com/spreadsheets/d/" + status["spreadsheet_id"])
        st.caption("แสดงจำนวนครั้งที่ล็อกอินสำเร็จ ไม่ใช่จำนวนบุคคลที่ไม่ซ้ำ · ยอดวันนี้ใช้เวลา UTC+08:00")
        first, second = st.columns(2)
        if first.button("รีเฟรชสถานะประวัติ", width="stretch"):
            st.rerun()
        if second.button("ส่งรายการที่รอไป Google Sheets", disabled=not status["enabled"] or status["syncing"], width="stretch"):
            schedule_sync(store, ticket, force=True)
            st.rerun()
        with st.expander("เชื่อมต่อ Google Sheets สำหรับประวัติ", expanded=not status["enabled"]):
            st.caption("เปิด Google Sheets API ใน Google Cloud และแชร์ชีตให้ client_email ของบัญชีบริการเป็น Editor")
            if status["account_email"]:
                st.caption("บัญชีบริการ: " + status["account_email"])
            st.caption("ไฟล์คีย์เก็บเฉพาะฝั่งเซิร์ฟเวอร์ ไม่แสดงคีย์เดิมในหน้าเว็บ เว้นไฟล์ว่างเพื่อใช้คีย์เดิม")
            with st.form("audit_connection", clear_on_submit=False):
                url = st.text_input("ลิงก์ Google Sheets สำหรับประวัติ", value=(
                    "https://docs.google.com/spreadsheets/d/" + status["spreadsheet_id"] if status["spreadsheet_id"] else ""),
                    key="audit_sheet_url", max_chars=250)
                tab = st.text_input("ชื่อแท็บประวัติ", value=status["tab"], max_chars=100, key="audit_sheet_tab")
                upload = st.file_uploader("Service account JSON", type=["json"], key="audit_account_upload")
                enabled = st.checkbox("เปิดการส่งประวัติไป Google Sheets", value=status["enabled"], key="audit_enabled")
                save = st.form_submit_button("บันทึกการเชื่อมต่อประวัติ", type="primary", width="stretch")
            if save:
                try:
                    store.save_audit_settings(ticket, url, tab, enabled,
                                              upload.getvalue() if upload else None)
                    st.session_state["clear_admin_fields"] = ["audit_account_upload"]
                    st.session_state["audit_notice"] = "บันทึกการตั้งค่าแล้ว หากเปิดการส่ง ระบบจะส่งรายการที่รออยู่และแสดงผลในสถานะประวัติ"
                    schedule_sync(store, ticket, force=True)
                    st.rerun()
                except AccessDenied:
                    raise
                except ValueError as error:
                    st.error(str(error))
