"""Бизнес-правила BRD (раздел 6). Чистые функции, без обращения к HTTP."""
import json
from datetime import datetime, timedelta, timezone
from flask import current_app

from .util import ApiError, now, tz, parse, fmt, pct, price_for

WEEKDAYS = "0123456"


# ---------- расписание ----------
def validate_schedule(sch) -> dict:
    """{"0": [["08:00","20:00"]], ...}; 0 = понедельник, 24:00 допустимо как конец."""
    if not isinstance(sch, dict):
        raise ApiError("validation", field="schedule")
    out = {}
    for d, wins in sch.items():
        if d not in WEEKDAYS or not isinstance(wins, list):
            raise ApiError("validation", field="schedule")
        clean = []
        for w in wins:
            try:
                a, b = (_hm(x) for x in w)
            except Exception:
                raise ApiError("validation", field="schedule")
            if not 0 <= a < b <= 1440:
                raise ApiError("validation", field="schedule")
            clean.append((a, b))
        clean.sort()
        for i in range(1, len(clean)):
            if clean[i][0] < clean[i - 1][1]:
                raise ApiError("validation", field="schedule")
        out[d] = [[_hm_s(a), _hm_s(b)] for a, b in clean]
    return out


def _hm(s):
    h, m = s.split(":")
    h, m = int(h), int(m)
    if not (0 <= m < 60 and 0 <= h <= 24):
        raise ValueError
    return h * 60 + m


def _hm_s(n):
    return f"{n // 60:02d}:{n % 60:02d}"


def schedule_covers(schedule: dict, tzname: str, start: datetime, end: datetime) -> bool:
    """Весь интервал [start, end) должен лежать в окнах расписания (в местном времени города)."""
    z = tz(tzname)
    s, e = start.astimezone(z), end.astimezone(z)
    day = s.replace(hour=0, minute=0, second=0, microsecond=0)
    while day < e:
        nxt = (day + timedelta(days=1))
        seg_a = max(s, day)
        seg_b = min(e, nxt)
        a = (seg_a - day).total_seconds() // 60
        b = (seg_b - day).total_seconds() // 60
        wins = [(_hm(x), _hm(y)) for x, y in schedule.get(str(day.weekday()), [])]
        if not any(wa <= a and b <= wb for wa, wb in wins):
            return False
        day = nxt
    return True


def schedule_minutes(schedule: dict, tzname: str, start: datetime, end: datetime) -> int:
    """Сколько минут из [start,end) место открыто по расписанию (для загрузки в статистике)."""
    z = tz(tzname)
    s, e = start.astimezone(z), end.astimezone(z)
    day = s.replace(hour=0, minute=0, second=0, microsecond=0)
    total = 0
    while day < e:
        nxt = day + timedelta(days=1)
        a = (max(s, day) - day).total_seconds() // 60
        b = (min(e, nxt) - day).total_seconds() // 60
        for x, y in schedule.get(str(day.weekday()), []):
            lo, hi = max(a, _hm(x)), min(b, _hm(y))
            total += max(0, hi - lo)
        day = nxt
    return int(total)


# ---------- интервал брони ----------
def check_interval(start: datetime, end: datetime):
    c = current_app.config
    n = now()
    if (end - start) <= timedelta(0):
        raise ApiError("bad_interval")
    minutes = int((end - start).total_seconds() // 60)
    step = c["STEP_MIN"]
    if (start.minute % step) or start.second or (end - start).total_seconds() % (step * 60):
        raise ApiError("bad_interval")
    if minutes < c["MIN_DURATION_MIN"] or minutes > c["MAX_DURATION_HOURS"] * 60:
        raise ApiError("bad_interval")
    if start < n:
        raise ApiError("in_past")
    if start > n + timedelta(days=c["MAX_DAYS_AHEAD"]):
        raise ApiError("too_far")
    return minutes


def overlapping(db, place_id, start, end, exclude_booking=None) -> bool:
    """Правило 1: одно место нельзя забронировать двум людям на одно время.
    Неоплаченные брони держат место, пока не истекли 10 минут (правило 3)."""
    q = """SELECT 1 FROM bookings WHERE place_id=? AND start_time < ? AND end_time > ?
           AND (status IN ('confirmed','active') OR (status='pending_payment' AND expires_at > ?))"""
    args = [place_id, fmt(end), fmt(start), fmt(now())]
    if exclude_booking:
        q += " AND id != ?"
        args.append(exclude_booking)
    return db.execute(q + " LIMIT 1", args).fetchone() is not None


def place_bookable(place, city, start, end) -> bool:
    return (place["status"] == "approved" and not place["is_closed"]
            and schedule_covers(json.loads(place["schedule_json"]), city["timezone"], start, end))


# ---------- возвраты (правила 6 и 9) ----------
def refund_percent(booking, by: str, at: datetime = None) -> int:
    if by in ("owner", "service"):
        return 100
    at = at or now()
    start = parse(booking["start_time"])
    c = current_app.config
    if at <= start - timedelta(minutes=c["FULL_REFUND_BEFORE_MIN"]):
        return 100
    if at < start:
        return c["LATE_REFUND_PERCENT"]
    return 0


# ---------- комиссия (правило 10) ----------
def split(paid_kop: int, refunded_kop: int):
    """Комиссия считается с суммы, которую владелец реально заработал (оплата минус возвраты)."""
    kept = max(0, paid_kop - refunded_kop)
    commission = pct(kept, current_app.config["COMMISSION_PERCENT"])
    return commission, kept - commission
