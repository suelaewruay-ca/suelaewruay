import hashlib
import json
from collections import Counter
from html import escape
from pathlib import Path

import pandas as pd
import streamlit as st

from ai_service import ai_error_message, choose_formula, prepare_image, scan_image
from access_ui import run_protected
from bankroll import (GROSS_RETURN, INITIAL_CAPITAL, calculate_bankroll, cash,
                      ledger_balance, rebase_ledger, settlement)
from branding import APP_NAME, LOGO_PATH, brand_html
from engine import (DIGITS, DISPLAY_DIGITS, fingerprint, make_plan,
                    parse_digits, rank_formulas, road_columns, selection)
from sheets import parse_matrix

ROOT = Path(__file__).resolve().parent
st.set_page_config(page_title=f"{APP_NAME} · วิเคราะห์เลข", page_icon=str(LOGO_PATH), layout="wide")
st.markdown("<style>" + (ROOT / "ui.css").read_text(encoding="utf-8") + "</style>", unsafe_allow_html=True)


def brand():
    st.markdown(brand_html(), unsafe_allow_html=True)


def init(library):
    defaults = {"history": [], "history_text": "", "scan": None, "image_id": None,
                "analysis": None, "live_log": [], "history_source": "กรอกเอง",
                "money_settings": {"initial_capital": INITIAL_CAPITAL, "unit": 1.0}}
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)
    if "history_text_next" in st.session_state:
        st.session_state.history_text = st.session_state.pop("history_text_next")
    st.session_state.money_settings.setdefault("initial_capital", INITIAL_CAPITAL)
    if st.session_state.get("library_version") != library["version"]:
        changed = "library_version" in st.session_state
        st.session_state.library = library
        st.session_state.library_version = library["version"]
        st.session_state.analysis = None
        if changed:
            st.info("ผู้ดูแลอัปเดตฐานสูตรแล้ว กรุณาวิเคราะห์ใหม่ด้วยชุดสูตรล่าสุด")


def set_history(history, source):
    st.session_state.history = list(history)
    st.session_state.history_text = " ".join(map(str, history))
    st.session_state.history_source = source
    st.session_state.analysis = None
    st.session_state.live_log = []


def append_result(digit):
    history = st.session_state.history
    if len(history) >= 2000:
        return
    analysis = st.session_state.analysis
    if analysis and analysis.get("history") == history:
        next_row = analysis["plan"][0]
        hit = digit in next_row["include"]
        row = {"งวด": len(history) + 1,
            "สูตร": analysis["chosen_name"], "เลขเลือก": " ".join(map(str, next_row["include"])),
            "ผลจริง": str(digit), "ผลตรวจ": "เข้า" if hit else "ไม่เข้า"}
        if analysis.get("money"):
            plan = analysis["money"]["plan"]
            result = settlement(plan["stake_per_digit"], plan["gross_return"], hit)
            row.update({"เงินต่อเลขจำลอง": plan["stake_per_digit"], "รวมเงินลงจำลอง": result["total_stake"],
                        "อัตราจ่ายรวมทุน": plan["gross_return"], "รับคืนจำลอง": result["returned"],
                        "กำไร/ขาดทุนจำลอง": result["net"],
                        "ยอดคงเหลือจำลอง": cash(ledger_balance(st.session_state.live_log, plan["initial_capital"]) + result["net"]),
                        "การเดินเงิน": "คำนวณตามเงินต่อเลขที่กรอก"})
        st.session_state.live_log.append(row)
    st.session_state.history = history + [digit]
    st.session_state.history_text_next = " ".join(map(str, st.session_state.history))
    st.session_state.history_source = "ประวัติที่ยืนยัน + ผลจริงที่เพิ่ม"
    st.session_state.analysis = None


def undo_result():
    if st.session_state.history:
        removed_round = len(st.session_state.history)
        st.session_state.history = st.session_state.history[:-1]
        st.session_state.history_text_next = " ".join(map(str, st.session_state.history))
        st.session_state.live_log = [row for row in st.session_state.live_log if row["งวด"] != removed_round]
        st.session_state.analysis = None


def digits_html(take, omit):
    return '<div class="digit-line">' + "".join(
        f'<span class="digit {"take" if digit in take else "omit"}" '
        f'title="{ "เลือก" if digit in take else "เว้น"} {digit}">{digit}</span>' for digit in DISPLAY_DIGITS) + '</div>'


def render_road(history):
    columns = road_columns(history)
    shown = columns[-100:]
    rows = max(6, max((len(column) for column in shown), default=0))
    count = max(24, len(shown))
    table = '<div class="road-scroll"><table class="road" aria-label="ประวัติผล จากซ้ายไปขวา เลขซ้ำลงล่าง"><tbody>'
    for row in range(rows):
        table += "<tr>"
        for col in range(count):
            digit = shown[col][row] if col < len(shown) and row < len(shown[col]) else None
            table += f'<td class="d{digit}">{digit if digit is not None else ""}</td>'
        table += "</tr>"
    table += '</tbody></table></div><div class="road-axis">อดีต <span>เรียงตามคอลัมน์จากซ้าย → ขวา · เลขซ้ำอ่านบน ↓ ล่าง</span> ล่าสุด</div>'
    st.markdown(table, unsafe_allow_html=True)
    if len(columns) > 100:
        st.caption("แสดง 100 คอลัมน์ล่าสุด · การวิเคราะห์ใช้ประวัติที่ยืนยันทั้งหมด")


def history_panel(api_key, model):
    with st.container(border=True, key="history-panel"):
        st.markdown('<div class="section-label">01 / HISTORY</div>', unsafe_allow_html=True)
        st.subheader("เริ่มจากประวัติผล")
        st.caption("อัปโหลดภาพ แล้วตรวจเลขก่อนวิเคราะห์ หรือกรอกประวัติด้วยตัวเอง")
        st.caption("เลข 0 และ 10 ถือเป็นเลขเดียวกัน · ระบบใช้ 0 ในประวัติและผลวิเคราะห์")
        uploaded = st.file_uploader("ภาพตารางผล PNG / JPG", type=["png", "jpg", "jpeg"], key="history_upload")
        if uploaded is not None:
            raw = uploaded.getvalue()
            image_id = hashlib.sha256(raw).hexdigest()
            if image_id != st.session_state.image_id:
                st.session_state.image_id = image_id
                st.session_state.scan = None
                set_history([], "ภาพใหม่ · ยังไม่ได้อ่าน")
            try:
                prepare_image(raw)
                st.image(raw, caption="ภาพต้นฉบับ · อ่านเลขซ้ำลงให้ครบก่อนเลื่อนไปทางขวา", width="stretch")
                if st.button("ให้ AI อ่านเลขจากภาพ", type="primary", disabled=not (api_key and model), width="stretch"):
                    try:
                        with st.spinner("AI กำลังอ่านตาราง · หากบริการสะดุดจะลองใหม่อัตโนมัติ สูงสุด 3 ครั้ง"):
                            result = scan_image(api_key, model, raw)
                        st.session_state.scan = result
                        st.session_state.history_text = result["text"]
                        st.session_state.history = []
                        st.session_state.analysis = None
                        st.session_state.history_source = "AI อ่านจากภาพ · รอตรวจยืนยัน"
                    except Exception as error:
                        st.error(ai_error_message(error))
            except ValueError as error:
                st.error(str(error))
        if not (api_key and model):
            st.caption("AI ยังไม่พร้อมใช้งาน กรุณาติดต่อผู้ดูแล · กรอกเลขและคำนวณสูตรได้ตามปกติ")
        if st.session_state.scan:
            for warning in st.session_state.scan["warnings"][:10]:
                st.warning(warning)
        st.text_area("ประวัติ: เก่าสุด → ล่าสุด", key="history_text", height=110,
                     placeholder="เช่น 1 2 0 7 1 8 2 8 6 4 9 7 9 6 6 5 5 4 8 8 8",
                     help="คั่นเลขด้วยช่องว่าง ใช้ 10 แทน 0 ได้ เลขซ้ำทุกตัวถือเป็นคนละงวด และต้องแก้ ? ให้ครบ")
        if st.button("ยืนยันประวัติ", type="primary", width="stretch"):
            try:
                history = parse_digits(st.session_state.history_text)
                if not history:
                    raise ValueError("กรุณาใส่ประวัติอย่างน้อย 1 งวด")
                if history != st.session_state.history:
                    st.session_state.live_log = []
                st.session_state.history = history
                st.session_state.analysis = None
                st.session_state.history_source = "ประวัติที่ผู้ใช้ตรวจยืนยัน"
            except ValueError as error:
                st.error(str(error))
        try:
            confirmed = bool(st.session_state.history) and parse_digits(st.session_state.history_text) == st.session_state.history
        except ValueError:
            confirmed = False
        if confirmed:
            st.success(f"ยืนยันแล้ว {len(st.session_state.history)} งวด · ผลล่าสุด {st.session_state.history[-1]}")
        elif st.session_state.history_text.strip():
            st.info("ตรวจแก้เลข แล้วกดยืนยันประวัติก่อนวิเคราะห์")
        return confirmed


def save_money_settings():
    capital = st.session_state.money_initial_capital
    unit = st.session_state.money_unit
    try:
        calculate_bankroll(unit, GROSS_RETURN, initial_capital=capital)
    except ValueError as exc:
        st.session_state.money_settings_error = str(exc)
        return
    st.session_state.live_log = rebase_ledger(st.session_state.live_log, capital)
    st.session_state.money_settings = {"initial_capital": capital, "unit": unit}
    st.session_state.pop("money_settings_error", None)
    st.session_state.money_settings_saved = True


def money_plan():
    preferences = st.session_state.money_settings
    capital = preferences["initial_capital"]
    return calculate_bankroll(preferences["unit"], GROSS_RETURN,
                              balance=ledger_balance(st.session_state.live_log, capital),
                              initial_capital=capital)


def bankroll_menu():
    preferences = st.session_state.money_settings
    capital, unit = preferences["initial_capital"], preferences["unit"]
    capital_label = f"{capital:,.2f}".rstrip("0").rstrip(".")
    with st.container(key="bankroll-menu-content"):
        st.markdown("**คำนวณเงิน**")
        with st.form("money_settings_form", border=False):
            st.number_input("ทุนเริ่มต้น (บาท)", min_value=0.0,
                            value=capital, step=100.0, format="%.2f", key="money_initial_capital")
            st.number_input("เงินต่อเลข (บาท)", min_value=0.01,
                            value=unit, step=1.0, format="%.2f", key="money_unit")
            st.caption("อัตราจ่ายคงที่ 9.90 เท่ารวมทุน · เงินลงทั้งรอบ = เงินต่อเลข × 7")
            st.form_submit_button("บันทึก", type="primary", width="stretch", on_click=save_money_settings)
        if st.session_state.get("money_settings_error"):
            st.error(st.session_state.money_settings_error)
        if st.session_state.pop("money_settings_saved", False):
            st.toast("บันทึกการตั้งค่าแล้ว", icon="✅")
        plan = money_plan()
        st.caption(f"ทุนเริ่มต้น {capital_label} บาท · ยอดจำลองล่าสุด {plan['balance']:,.2f} บาท")
        st.markdown(f'<div class="money-recommendation">เงินต่อเลข <strong>{plan["stake_per_digit"]:,.2f} บาท</strong>'
                    f'<span>รวม 7 เลข = {plan["total_stake"]:,.2f} บาทต่อรอบ</span></div>', unsafe_allow_html=True)
        st.write(f"หากเข้า: กำไร/ขาดทุนสุทธิ **{plan['net_if_hit']:+,.2f} บาท** · หากไม่เข้า: **{plan['net_if_miss']:,.2f} บาท**")



def actual_result_input(confirmed):
    st.markdown("**เพิ่มผลจริงล่าสุด**")
    with st.container(key="digit-inputs"):
        for offset in range(0, len(DISPLAY_DIGITS), 5):
            buttons = st.columns(5)
            for col, digit in zip(buttons, DISPLAY_DIGITS[offset:offset + 5]):
                if col.button(str(digit), key=f"actual_{digit}",
                              width="stretch", disabled=not confirmed or len(st.session_state.history) >= 2000):
                    append_result(digit)
                    st.rerun()
    st.caption("ถ้าผลออก 10 ให้กดปุ่ม 0 · เมื่อเพิ่มผลจริงแล้วให้กดวิเคราะห์อีกครั้งเพื่อปรับแผน")
    if st.button("↶ ย้อนกลับ 1 งวด", disabled=not st.session_state.history):
        undo_result()
        st.rerun()


def analysis_panel(formulas, confirmed, api_key, model):
    history = st.session_state.history
    with st.container(border=True, key="analysis-panel"):
        st.markdown('<div class="section-label">02 / ANALYSIS</div>', unsafe_allow_html=True)
        st.subheader("เลือกสูตรสำหรับงวดถัดไป")
        st.caption("เทียบทุกสูตรกับผลย้อนหลัง แล้วแสดงเลขเลือก 7 ตัวและเลขเว้น 3 ตัว")
        use_ai = st.toggle("ให้ AI ช่วยเลือกจาก 5 สูตรอันดับแรก", value=bool(api_key and model), disabled=not (api_key and model))
        scenario_mode = st.selectbox("สมมติฐานสำหรับแผนงวดถัดไป",
                                    ["คำนวณจากผลงวดล่าสุด", "กำหนดผลสมมติ 19 งวดเอง"])
        scenario, scenario_valid = None, True
        if scenario_mode.startswith("กำหนด"):
            scenario_text = st.text_area("ผลสมมติ 19 ตัว — ไม่ใช่ผลทำนาย", key="scenario_text",
                                         placeholder="ใส่ผลสมมติ 19 งวด คั่นด้วยช่องว่าง", height=80)
            try:
                scenario = parse_digits(scenario_text, limit=19)
                if len(scenario) != 19:
                    raise ValueError("ต้องระบุผลสมมติให้ครบ 19 ตัว")
            except ValueError as error:
                scenario_valid = False
                st.caption(str(error))
        st.caption("งวดแรกใช้ผลจริงล่าสุด งวดที่ 2–20 ใช้ผลสมมติที่เลือก เมื่อมีผลจริงใหม่ให้เพิ่มผลและวิเคราะห์อีกครั้ง")
        analyze = st.button("วิเคราะห์สูตรและคำนวณ 20 งวด →", type="primary", width="stretch",
                            disabled=not (confirmed and len(history) >= 3 and scenario_valid))
        key = fingerprint(history, formulas)
        options_key = json.dumps([scenario, scenario_mode, bool(use_ai)], ensure_ascii=False)
        current = st.session_state.analysis
        if current and (current["key"] != key or current["options_key"] != options_key or not confirmed):
            st.session_state.analysis = None
            current = None
        if analyze:
            try:
                with st.spinner(f"กำลังทดสอบ {len(formulas)} สูตรกับประวัติจริง..."):
                    ranked, unavailable = rank_formulas(formulas, history)
                    if not ranked:
                        raise ValueError("ประวัติไม่พอ หรือไม่มีสูตรที่คำนวณได้ กรุณาตรวจฐานสูตร")
                    chosen = ranked[0]
                    ai_result, ai_error = None, None
                    if use_ai:
                        try:
                            ai_result = choose_formula(api_key, model, ranked, history)
                            chosen = next(item for item in ranked if item["formula"].id == ai_result["formula_id"])
                        except Exception as error:
                            ai_error = ai_error_message(error) + " · ครั้งนี้ใช้สูตรอันดับหนึ่งจากผลทดสอบย้อนหลัง"
                    plan = make_plan(chosen["formula"], history, scenario=scenario)
                    current = {"key": key, "options_key": options_key, "history": list(history),
                        "ranked": ranked, "unavailable": unavailable, "chosen": chosen,
                        "chosen_name": chosen["formula"].name, "plan": plan,
                        "ai": ai_result, "ai_error": ai_error, "scenario_mode": scenario_mode}
                    st.session_state.analysis = current
            except ValueError as error:
                st.error(str(error))
        if not current:
            st.markdown('''<div class="empty-result"><div class="empty-icon">◈</div>
            <div class="empty-title">จากประวัติ สู่แผนที่ตรวจสอบได้</div>
            <p>ยืนยันประวัติอย่างน้อย 3 งวด<br>แล้วเริ่มวิเคราะห์เพื่อเทียบสูตรทั้งหมด</p></div>''', unsafe_allow_html=True)
            actual_result_input(confirmed)
            return
        plan = current["plan"]
        label = "AI เลือกจากผลทดสอบ" if current["ai"] else "อันดับหนึ่งจากผลทดสอบย้อนหลัง"
        omitted = "-".join(map(str, plan[0]["exclude"]))
        formula_label = current["chosen_name"].removeprefix("สูตร").strip()
        st.markdown(f'<div class="recommend-label">{label}</div><div class="recommend-name">เว้น {omitted}</div>', unsafe_allow_html=True)
        st.caption(f"ผลงวดล่าสุด {history[-1]}")
        st.markdown(digits_html(plan[0]["include"], plan[0]["exclude"]), unsafe_allow_html=True)
        st.markdown('<div class="analysis-result-footer">'
                    f'<span><strong>กด:</strong> {" · ".join(map(str, plan[0]["include"]))}</span>'
                    f'<span><strong>สูตร:</strong> {escape(formula_label)}</span></div>', unsafe_allow_html=True)

        with st.expander("เหตุผลจาก AI", expanded=True):
            if current["ai"]:
                st.write(current["ai"]["reason"])
                st.caption(current["ai"]["caution"])
            elif current["ai_error"]:
                st.info(current["ai_error"])
            else:
                st.caption("ครั้งนี้เลือกสูตรจากสถิติย้อนหลัง โดยไม่ได้ใช้ AI")
        actual_result_input(confirmed)

    current["money"] = {"plan": money_plan()}


def main(api_key, model, library):
    init(library)
    with st.container(horizontal=True, wrap=False, vertical_alignment="center", key="page-header"):
        with st.container():
            brand()
        menu = st.popover("☰", help="คำนวณเงินและตั้งค่าทุน", key="page-menu")
    st.markdown('<div class="eyebrow">ปักหลัก / 0—9</div>', unsafe_allow_html=True)
    st.title("วิเคราะห์เลขเด็ดด้วย AI")
    st.markdown('<p class="intro">อ่านประวัติและวิเคราะห์ภาพสถิติด้วยหาสูตรอย่างแม่นยำ AI<br>เลือก 7 เลข เว้น 3 เลข</p>', unsafe_allow_html=True)
    formulas, rejected = parse_matrix(st.session_state.library["values"], st.session_state.library["sheet_name"])
    if rejected:
        st.warning(f"ฐานข้อมูลมี {len(rejected)} สูตรที่ไม่ผ่านการตรวจ กำลังใช้เฉพาะ {len(formulas)} สูตรที่ครบ")
    summary = st.container(key="summary-strip")
    left, right = st.columns([1, 1.25], gap="large")
    with left:
        confirmed = history_panel(api_key, model)
    with right:
        analysis_panel(formulas, confirmed, api_key, model)
    with summary:
        current = st.session_state.analysis
        chosen = current["chosen"] if current else None
        metric_help = "ผลทดสอบย้อนหลังของสูตรที่เลือก ไม่ใช่ความน่าจะเป็นของงวดหน้า · กดวิเคราะห์เพื่ออัปเดต"
        status1, status2, status3 = st.columns(3)
        status1.metric("เข้าย้อนหลัง", f"{chosen['hits']}/{chosen['count']}" if chosen else "—", help=metric_help)
        status2.metric("อัตราเข้าย้อนหลัง", f"{chosen['rate']:.1%}" if chosen else "—", help=metric_help)
        status3.metric("หลุดต่อเนื่องสูงสุด", f"{chosen['max_losses']} งวด" if chosen else "—", help=metric_help)
    with menu:
        bankroll_menu()
    with st.container(border=True, key="road-panel"):
        st.markdown('<div class="section-label">HISTORY ROAD</div>', unsafe_allow_html=True)
        st.subheader("กระดานประวัติผล")
        st.caption(st.session_state.history_source)
        render_road(st.session_state.history)
        if st.session_state.history:
            counts = Counter(st.session_state.history)
            st.caption("ความถี่ทั้งหมด  ·  " + "   /   ".join(f"{digit}: {counts[digit]}" for digit in DISPLAY_DIGITS))
    with st.expander("บันทึกตรวจผลใหม่หลังวิเคราะห์", expanded=True):
        st.caption("ตรวจชุดเลขและงบที่แสดงก่อนเพิ่มผลจริง · ยอดเงินเป็นการจำลองว่าลงตามแผนทุกงวดที่บันทึก")
        if st.session_state.live_log:
            live_frame = pd.DataFrame(st.session_state.live_log)
            st.dataframe(live_frame, hide_index=True, width="stretch")
            st.download_button("ดาวน์โหลดบันทึกผลใหม่", live_frame.to_csv(index=False).encode("utf-8-sig"),
                               f"{APP_NAME}-บันทึกผล.csv", mime="text/csv")
        else:
            st.caption("ยังไม่มีบันทึก · วิเคราะห์แล้วกดเพิ่มผลจริงเพื่อเริ่มบันทึก")
    st.markdown(f'<div class="app-footer"><span>{APP_NAME} / วิเคราะห์เลข</span><span>สถิติย้อนหลังและแผนสมมติ ไม่รับประกันผลจริง · ประวัติเก็บใน session นี้</span></div>', unsafe_allow_html=True)


run_protected(main, brand)
