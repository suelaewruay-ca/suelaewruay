"""Cash-flow calculator for seven equal single-digit stakes."""

from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

INITIAL_CAPITAL = 1000.0
GROSS_RETURN = 9.9
CENT = Decimal("0.01")


def amount(value):
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("จำนวนเงินต้องเป็นตัวเลขที่มีค่าจำกัด")
    return number


def cash(value):
    return float(amount(value).quantize(CENT, rounding=ROUND_HALF_UP))


def settlement(stake_per_digit, gross_return, hit):
    """gross_return includes the winning stake; the other six stakes are lost."""
    stake, payout = amount(stake_per_digit), amount(gross_return)
    if stake < 0 or payout <= 0 or type(hit) is not bool:
        raise ValueError("เงินต่อเลขและอัตราจ่ายไม่ถูกต้อง")
    total = (stake * 7).quantize(CENT)
    returned = (stake * payout if hit else Decimal(0)).quantize(CENT, rounding=ROUND_DOWN)
    return {"total_stake": float(total), "returned": float(returned), "net": float(returned - total)}


def starting_capital(value):
    capital = amount(value)
    if capital < 0 or capital != capital.quantize(CENT):
        raise ValueError("ทุนเริ่มต้นต้องไม่น้อยกว่า 0 บาท และมีทศนิยมไม่เกิน 2 ตำแหน่ง")
    return capital


def calculate_bankroll(stake_per_digit, gross_return, *, balance=None, initial_capital=INITIAL_CAPITAL):
    """Use the entered stake unchanged, regardless of simulated balance or history."""
    capital = starting_capital(initial_capital)
    if balance is None:
        balance = capital
    stake, payout, current = amount(stake_per_digit), amount(gross_return), amount(balance)
    if payout <= 0 or stake < CENT or stake != stake.quantize(CENT):
        raise ValueError("อัตราจ่ายต้องมากกว่า 0 และเงินต่อเลขต้องไม่น้อยกว่า 0.01 บาท (ทศนิยมไม่เกิน 2 ตำแหน่ง)")
    winning = settlement(stake, payout, True)
    return {"initial_capital": float(capital), "balance": float(current),
            "gross_return": float(payout), "stake_per_digit": float(stake),
            "total_stake": winning["total_stake"], "returned_if_hit": winning["returned"],
            "net_if_hit": winning["net"], "net_if_miss": -winning["total_stake"]}


def ledger_balance(rows, initial_capital=INITIAL_CAPITAL):
    return cash(starting_capital(initial_capital) + sum((amount(row.get("กำไร/ขาดทุนจำลอง", 0)) for row in rows), Decimal(0)))


def rebase_ledger(rows, initial_capital):
    """Rebuild running balances while preserving each round's saved stakes and P/L."""
    balance = starting_capital(initial_capital)
    rebased = []
    for row in rows:
        saved = dict(row)
        balance += amount(saved.get("กำไร/ขาดทุนจำลอง", 0))
        if "กำไร/ขาดทุนจำลอง" in saved:
            saved["ยอดคงเหลือจำลอง"] = cash(balance)
        rebased.append(saved)
    return rebased
