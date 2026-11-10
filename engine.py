"""Deterministic formula evaluation. Never execute code received from a Sheet or AI."""

import ast
import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass

DIGITS = tuple(range(10))
DISPLAY_DIGITS = (1, 2, 3, 4, 5, 6, 7, 8, 9, 0)
SAMPLE_HISTORY = [1, 2, 0, 7, 1, 8, 2, 8, 6, 4, 9, 7, 9, 6, 6, 5, 5, 4, 8, 8, 8]
MAX_HISTORY = 2000


def parse_digits(text, *, limit=MAX_HISTORY):
    """Accept 0–9 and the whole token 10 as an alias of 0; reject other multi-digit tokens."""
    cleaned = text.strip().replace("→", " ").replace("-->", " ")
    if not cleaned:
        return []
    tokens = re.split(r"[\s,;|\-]+", cleaned)
    if any(not re.fullmatch(r"(?:[0-9]|10)", token) for token in tokens):
        raise ValueError("ใส่เลข 0–9 หรือ 10 (แทน 0) คั่นด้วยช่องว่างหรือจุลภาค เช่น 6 6 10 5")
    if len(tokens) > limit:
        raise ValueError(f"รองรับไม่เกิน {limit:,} งวด")
    return [0 if token == "10" else int(token) for token in tokens]


def check_history(history):
    if not isinstance(history, list) or not history or len(history) > MAX_HISTORY:
        raise ValueError("ต้องมีประวัติ 1–2,000 งวด")
    if any(type(d) is not int or d not in DIGITS for d in history):
        raise ValueError("ผลทุกงวดต้องเป็นเลข 0–9")


def road_columns(history):
    columns = []
    for digit in history:
        if columns and columns[-1][0] == digit:
            columns[-1].append(digit)
        else:
            columns.append([digit])
    return columns


def digit_set(value, count=3):
    if not isinstance(value, list) or len(value) != count:
        raise ValueError(f"ต้องมีเลข {count} ตัว")
    if any(type(d) is not int or d not in DIGITS for d in value) or len(set(value)) != count:
        raise ValueError(f"ต้องมีเลข 0–9 จำนวน {count} ตัวที่ไม่ซ้ำกัน")
    return sorted(value)


def expression(expr, history):
    """Tiny arithmetic language: last, p1..p20, n; + - * % and parentheses only."""
    if not isinstance(expr, str) or len(expr) > 150:
        raise ValueError("นิพจน์ยาวเกินไป")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ValueError("รูปแบบนิพจน์ไม่ถูกต้อง") from exc
    if len(list(ast.walk(tree))) > 70:
        raise ValueError("นิพจน์ซับซ้อนเกินไป")

    def visit(node):
        if isinstance(node, ast.Expression):
            value = visit(node.body)
        elif isinstance(node, ast.Constant) and type(node.value) is int and abs(node.value) <= 1000:
            value = node.value
        elif isinstance(node, ast.Name):
            if node.id == "n":
                value = len(history) + 1
            elif node.id == "last":
                if not history:
                    raise ValueError("ประวัติไม่พอ")
                value = history[-1]
            elif re.fullmatch(r"p([1-9]|1[0-9]|20)", node.id):
                lag = int(node.id[1:])
                if len(history) < lag:
                    raise ValueError("ประวัติไม่พอ")
                value = history[-lag]
            else:
                raise ValueError("อนุญาตเฉพาะ last, p1–p20 และ n")
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Mod)):
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Add):
                value = left + right
            elif isinstance(node.op, ast.Sub):
                value = left - right
            elif isinstance(node.op, ast.Mult):
                value = left * right
            else:
                if right == 0:
                    raise ValueError("หารเอาเศษด้วยศูนย์ไม่ได้")
                value = left % right
        else:
            raise ValueError("อนุญาตเฉพาะเลข ตัวแปร + - * % และวงเล็บ")
        if abs(value) > 1_000_000:
            raise ValueError("ค่านิพจน์เกินขอบเขต")
        return value

    return visit(tree) % 10


@dataclass(frozen=True)
class Formula:
    id: str
    name: str
    kind: str
    params: dict
    min_history: int = 1
    source: str = ""
    description: str = ""

    def to_dict(self):
        return asdict(self)


def formula_from_dict(data):
    if not isinstance(data, dict):
        raise ValueError("ข้อมูลสูตรต้องเป็น object")
    allowed = {"id", "name", "kind", "params", "min_history", "source", "description"}
    if set(data) - allowed:
        raise ValueError("มีฟิลด์สูตรที่ไม่รองรับ")
    formula = Formula(**data)
    for name in ("id", "name", "kind", "source", "description"):
        if not isinstance(getattr(formula, name), str) or len(getattr(formula, name)) > 2000:
            raise ValueError("ชื่อหรือรายละเอียดสูตรไม่ถูกต้อง")
    if not formula.id.strip() or not formula.name.strip():
        raise ValueError("สูตรต้องมี id และ name")
    if type(formula.min_history) is not int or not 1 <= formula.min_history <= 500:
        raise ValueError("min_history ต้องอยู่ระหว่าง 1–500")
    p = formula.params
    if not isinstance(p, dict):
        raise ValueError("params ต้องเป็น JSON object")
    required = {
        "fixed": {"exclude"}, "offsets": {"offsets"},
        "frequency": {"window", "mode"}, "lookup": {"mapping"},
        "cycle": {"sets"}, "expression": {"expressions"},
    }
    if formula.kind not in required or set(p) != required[formula.kind]:
        raise ValueError("ประเภทสูตรหรือ params ไม่ตรงรูปแบบที่รองรับ")
    if formula.kind == "fixed":
        digit_set(p["exclude"])
    elif formula.kind == "offsets":
        digit_set(p["offsets"])
    elif formula.kind == "frequency":
        if type(p["window"]) is not int or not 1 <= p["window"] <= 500:
            raise ValueError("window ต้องอยู่ระหว่าง 1–500")
        if p["mode"] not in ("least", "most") or formula.min_history < p["window"]:
            raise ValueError("frequency ใช้ mode least/most และ min_history ไม่น้อยกว่า window")
    elif formula.kind == "lookup":
        if not isinstance(p["mapping"], dict) or set(p["mapping"]) != set(map(str, DIGITS)):
            raise ValueError("lookup ต้องระบุเลขต้นทางครบ 0–9")
        for values in p["mapping"].values():
            digit_set(values)
    elif formula.kind == "cycle":
        if not isinstance(p["sets"], list) or not 1 <= len(p["sets"]) <= 100:
            raise ValueError("cycle ต้องมี 1–100 ชุด")
        for values in p["sets"]:
            digit_set(values)
    else:
        if not isinstance(p["expressions"], list) or len(p["expressions"]) != 3:
            raise ValueError("expression ต้องมี 3 นิพจน์")
        for expr in p["expressions"]:
            expression(expr, [0] * formula.min_history)
    return formula


def load_formulas(items):
    if not isinstance(items, list) or not 1 <= len(items) <= 200:
        raise ValueError("รองรับ 1–200 สูตรต่อครั้ง")
    formulas = [formula_from_dict(item) for item in items]
    if len({f.id for f in formulas}) != len(formulas):
        raise ValueError("id ของสูตรต้องไม่ซ้ำกัน")
    return formulas


def excluded(formula, history):
    if len(history) < formula.min_history:
        raise ValueError(f"ต้องมีประวัติอย่างน้อย {formula.min_history} งวด")
    p = formula.params
    if formula.kind == "fixed":
        result = p["exclude"]
    elif formula.kind == "offsets":
        result = [(history[-1] + offset) % 10 for offset in p["offsets"]]
    elif formula.kind == "frequency":
        counts = Counter(history[-p["window"]:])
        sign = 1 if p["mode"] == "least" else -1
        result = sorted(DIGITS, key=lambda digit: (sign * counts[digit], digit))[:3]
    elif formula.kind == "lookup":
        result = p["mapping"][str(history[-1])]
    elif formula.kind == "cycle":
        result = p["sets"][len(history) % len(p["sets"]) ]
    else:
        result = [expression(expr, history) for expr in p["expressions"]]
    return digit_set(result)


def selection(formula, history):
    omit = excluded(formula, history)
    return [digit for digit in DISPLAY_DIGITS if digit not in omit], omit


def wilson_lower(hits, count):
    if not count:
        return 0.0
    z, p = 1.96, hits / count
    return (p + z*z/(2*count) - z*math.sqrt((p*(1-p)+z*z/(4*count))/count)) / (1+z*z/count)


def rank_formulas(formulas, history):
    """All candidates share a time interval; predict before revealing each real result."""
    check_history(history)
    ready = [formula for formula in formulas if formula.min_history < len(history)]
    start = max((formula.min_history for formula in ready), default=len(history))
    results, unavailable = [], []
    for formula in formulas:
        if formula not in ready:
            unavailable.append({"name": formula.name, "reason": "ประวัติไม่พอสำหรับทดสอบย้อนหลัง"})
            continue
        records, losses, max_losses = [], 0, 0
        try:
            for index in range(start, len(history)):
                take, omit = selection(formula, history[:index])
                hit = history[index] in take
                losses = 0 if hit else losses + 1
                max_losses = max(max_losses, losses)
                records.append({"round": index + 1, "actual": history[index], "include": take,
                                "exclude": omit, "hit": hit})
            selection(formula, history)
        except ValueError as error:
            unavailable.append({"name": formula.name, "reason": str(error)})
            continue
        hits, count = sum(row["hit"] for row in records), len(records)
        results.append({"formula": formula, "hits": hits, "count": count,
                        "rate": hits / count, "lower_bound": wilson_lower(hits, count),
                        "max_losses": max_losses, "records": records, "start": start + 1})
    results.sort(key=lambda item: (-item["lower_bound"], item["max_losses"], item["formula"].id))
    return results, unavailable


def make_plan(formula, history, *, scenario=None, horizon=20):
    """A future path is an explicit user assumption, never an observed result."""
    check_history(history)
    if horizon != 20:
        raise ValueError("แผนต้องมี 20 งวด")
    if scenario is not None:
        if len(scenario) != horizon - 1:
            raise ValueError("ต้องมีผลสมมติ 19 ตัวเพื่อคำนวณงวดที่ 2–20")
        check_history(scenario)
    path = scenario if scenario is not None else [history[-1]] * (horizon - 1)
    working, rows = list(history), []
    for step in range(horizon):
        take, omit = selection(formula, working)
        rows.append({"step": step + 1, "round": len(history) + step + 1,
                     "include": take, "exclude": omit, "formula_id": formula.id,
                     "basis": "ประวัติจริง" if step == 0 else "ตามผลสมมติ", "seed": working[-1],
                     "assumed_previous": None if step == 0 else path[step - 1]})
        if step < horizon - 1:
            working.append(path[step])
    return rows


def fingerprint(history, formulas):
    payload = {"history": history, "formulas": [formula.to_dict() for formula in formulas]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def demo_formulas():
    return load_formulas([
        {"id": "demo-offset", "name": "ตัวอย่าง · เว้นเลขล่าสุด +1 +2", "kind": "offsets",
         "params": {"offsets": [0, 1, 2]}, "min_history": 1,
         "source": "ตัวอย่างระบบ ไม่ใช่สูตรจาก Google Sheets"},
        {"id": "demo-low", "name": "ตัวอย่าง · เว้น 3 เลขที่ออกน้อย", "kind": "frequency",
         "params": {"window": 10, "mode": "least"}, "min_history": 10,
         "source": "ตัวอย่างระบบ ไม่ใช่สูตรจาก Google Sheets",
         "description": "นับ 10 งวดล่าสุด หากความถี่เท่ากัน เลือกเลขน้อยก่อน"},
        {"id": "demo-high", "name": "ตัวอย่าง · เว้น 3 เลขที่ออกบ่อย", "kind": "frequency",
         "params": {"window": 10, "mode": "most"}, "min_history": 10,
         "source": "ตัวอย่างระบบ ไม่ใช่สูตรจาก Google Sheets",
         "description": "นับ 10 งวดล่าสุด หากความถี่เท่ากัน เลือกเลขน้อยก่อน"},
    ])
