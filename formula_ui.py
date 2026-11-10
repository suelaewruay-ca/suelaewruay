"""Administrator-only preview and activation of Google Sheets formula sets."""

import hashlib
import json

import streamlit as st

from admin_store import AccessDenied
from login_history import local_time
from sheets import column_name


def formula_panel(store, ticket):
    current = store.formula_settings(ticket)
    with st.container(border=True, key="formula-source-panel"):
        st.subheader("ตั้งค่าฐานสูตร")
        st.caption("ใช้ฐานสูตรชุดเดียวกันทั้งแอป เปลี่ยนชีตสำหรับทีมใหม่ได้โดยตรวจตัวอย่างก่อนนำไปใช้")
        if notice := st.session_state.pop("formula_notice", None):
            st.success(notice)
        origin = "Google Sheets ที่นำเข้าล่าสุด" if current["custom"] else "ฐานสูตรเริ่มต้น"
        st.write(f"กำลังใช้งาน: {origin} · {current['count']} สูตร")
        if current["custom"]:
            st.caption(f"แท็บ {current['tab']} · นำเข้า {local_time(current['updated_at'])} (UTC+08:00)")
            st.link_button("เปิดชีตสูตรที่ใช้งาน", current["url"])
        st.caption("เมื่อแก้ข้อมูลในชีต ให้โหลดและนำเข้าอีกครั้ง เว็บใช้ชุดที่บันทึกล่าสุดระหว่างรออัปเดต")
        with st.expander("เปลี่ยนลิงก์หรืออัปเดตสูตร", expanded=False, key="formula_source_expander"):
            url = st.text_input("ลิงก์ Google Sheets สำหรับสูตร", value=current["url"],
                                key="formula_sheet_url", max_chars=250)
            tab = st.text_input("ชื่อแท็บสูตร", value=current["tab"], key="formula_sheet_tab", max_chars=100)
            public = st.radio("วิธีอ่านชีตสูตร", [False, True], index=int(current["public"]),
                              format_func=lambda value: "ชีตสาธารณะ" if value else "Service account (ชีตส่วนตัว)",
                              horizontal=True, key="formula_public")
            upload = None
            upload_generation = st.session_state.get("formula_upload_generation", 0)
            upload_key = f"formula_account_upload_{upload_generation}"
            if not public:
                st.caption("เปิด Google Sheets API และแชร์ชีตสูตรให้ client_email เป็น Viewer")
                if current["account_email"]:
                    st.caption("บัญชีบริการที่บันทึกไว้: " + current["account_email"] + " · เว้นไฟล์ว่างเพื่อใช้คีย์เดิม")
                upload = st.file_uploader("Service account JSON สำหรับอ่านสูตร", type=["json"],
                                          key=upload_key)
            else:
                st.caption("ใช้กับชีตที่อ่านได้โดยไม่เข้าสู่ระบบ Google เท่านั้น")
            st.caption("รูปแบบชีต: A1 เป็น เลขตั้งต้น · A2:A11 มี 0–9 ครบ · B1 เป็นต้นไปเป็นชื่อสูตร · แต่ละช่องมีเลขเว้น 3 ตัวไม่ซ้ำ · สูงสุด 200 สูตร")
            raw = upload.getvalue() if upload else None
            signature = hashlib.sha256(json.dumps([url, tab, public, current["version"],
                hashlib.sha256(raw).hexdigest() if raw is not None else ""], ensure_ascii=False).encode()).hexdigest()
            if st.session_state.get("formula_draft_signature") != signature:
                st.session_state.pop("formula_draft", None)
            if st.button("ตรวจสอบและโหลดสูตร", key="formula_load", width="stretch"):
                st.session_state.pop("formula_draft", None)
                try:
                    with st.spinner("กำลังอ่านและตรวจสอบสูตรจาก Google Sheets..."):
                        draft = store.prepare_formula_source(ticket, url, tab, raw, public=public)
                    st.session_state["formula_draft"] = draft
                    st.session_state["formula_draft_signature"] = signature
                except AccessDenied:
                    raise
                except ValueError as error:
                    st.error(str(error))
            draft = st.session_state.get("formula_draft")
            if draft:
                st.success(f"ตรวจผ่านครบ {draft['count']} สูตร · ยังไม่ได้เปลี่ยนฐานสูตรที่ใช้งาน")
                st.link_button("เปิดชีตที่ตรวจสอบแล้ว", draft["url"])
                st.text("แท็บที่จะนำเข้า: " + draft["tab"])
                st.caption("ตัวอย่างเลขตั้งต้นและ 5 สูตรแรก")
                # Only table values are rendered. The draft's key stays in the server session.
                st.dataframe({f"{draft['values'][0][index]} ({column_name(index + 1)})":
                              [str(row[index]) if index < len(row) else "" for row in draft["values"][1:]]
                              for index in range(min(6, len(draft["values"][0])))},
                             hide_index=True, width="stretch")
                st.caption("การนำเข้าจะใช้แทนชุดปัจจุบันสำหรับสมาชิกทุกคนในการใช้งานครั้งถัดไป และให้วิเคราะห์ใหม่")
                if st.button("นำสูตรชุดนี้ไปใช้", key="formula_apply", type="primary", width="stretch"):
                    try:
                        store.apply_formula_source(ticket, draft)
                        st.session_state.pop("formula_draft", None)
                        # A new widget ID clears the browser's uploaded file as well as server state.
                        st.session_state["formula_upload_generation"] = upload_generation + 1
                        st.session_state["clear_admin_fields"] = [upload_key]
                        st.session_state["formula_notice"] = f"เปลี่ยนฐานสูตรแล้ว {draft['count']} สูตร สมาชิกจะใช้ชุดใหม่ในการใช้งานครั้งถัดไป"
                        st.rerun()
                    except AccessDenied:
                        raise
                    except ValueError as error:
                        st.session_state.pop("formula_draft", None)
                        st.error(str(error))
