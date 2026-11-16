"""Операции над бронями и деньгами. Вызываются из маршрутов и фоновых задач."""
import json
from datetime import timedelta
from flask import current_app

from . import payments
from .db import tx
from .notify import notify
from .rules import (check_interval, overlapping, place_bookable, refund_percent, split)
from .util import ApiError, now, fmt, parse, price_for


def get_place_city(db, place_id):
    p = db.execute("SELECT * FROM places WHERE id=?", (place_id,)).fetchone()
    if not p:
        raise ApiError("not_found", 404)
    c = db.execute("SELECT * FROM cities WHERE id=?", (p["city_id"],)).fetchone()
    return p, c


def paid_and_refunded(db, booking_id):
    paid = db.execute("SELECT COALESCE(SUM(amount_kop),0) FROM payments WHERE booking_id=? AND status='succeeded'",
                      (booking_id,)).fetchone()[0]
    ref = db.execute("SELECT COALESCE(SUM(amount_kop),0) FROM refunds WHERE booking_id=?", (booking_id,)).fetchone()[0]
    return paid, ref


def create_booking(db, user, place_id, start, end):
    place, city = get_place_city(db, place_id)
    minutes = check_interval(start, end)
    with tx(db):  # проверка и вставка в одной транзакции -> нет двойных броней
        if not place_bookable(place, city, start, end):
            raise ApiError("place_unavailable", 409)
        if place["owner_id"] == user["id"]:
            raise ApiError("place_unavailable", 409)
        if overlapping(db, place_id, start, end):
            raise ApiError("slot_taken", 409)
        total = price_for(place["price_per_hour_kop"], minutes)  # правило 5: цена фиксируется
        expires = now() + timedelta(minutes=current_app.config["PAYMENT_TIMEOUT_MIN"])
        cur = db.execute(
            """INSERT INTO bookings(user_id,place_id,start_time,end_time,price_per_hour_kop,total_price_kop,
               status,expires_at,created_at) VALUES(?,?,?,?,?,?, 'pending_payment',?,?)""",
            (user["id"], place_id, fmt(start), fmt(end), place["price_per_hour_kop"], total, fmt(expires), fmt(now())))
        bid = cur.lastrowid
        pay = db.execute("INSERT INTO payments(booking_id,amount_kop,status,provider,created_at) VALUES(?,?,?,?,?)",
                         (bid, total, "pending", payments.provider().name, fmt(now())))
        pay_id = pay.lastrowid
    prov = payments.provider()
    ppid, url = prov.create(pay_id, total, f"Парковка: {place['address']}", f"{current_app.config['PUBLIC_URL']}/#/booking/{bid}")
    db.execute("UPDATE payments SET provider_payment_id=?, confirmation_url=? WHERE id=?", (ppid, url, pay_id))
    return bid


def _receipt(db, booking_id, kind, amount):
    n = db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] + 1
    db.execute("INSERT INTO receipts(booking_id,kind,number,amount_kop,created_at) VALUES(?,?,?,?,?)",
               (booking_id, kind, f"PC-{n:08d}", amount, fmt(now())))


def mark_paid(db, payment_id, method="card"):
    """Правило 2: бронь подтверждается только после оплаты. Идемпотентно."""
    with tx(db):
        pay = db.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
        if not pay or pay["status"] == "succeeded":
            return
        b = db.execute("SELECT * FROM bookings WHERE id=?", (pay["booking_id"],)).fetchone()
        db.execute("UPDATE payments SET status='succeeded', method=?, paid_at=? WHERE id=?", (method, fmt(now()), payment_id))
        late = b["status"] != "pending_payment" or parse(b["expires_at"]) <= now()
        if not late:
            db.execute("UPDATE bookings SET status='confirmed', expires_at=NULL WHERE id=?", (b["id"],))
            _receipt(db, b["id"], "payment", pay["amount_kop"])
            notify(db, b["user_id"], "booking_confirmed", f"Бронь №{b['id']} подтверждена")
            return
        # оплатил после истечения 10 минут: бронь уже снята, деньги возвращаем
        _receipt(db, b["id"], "payment", pay["amount_kop"])
        _do_refund(db, b, pay, pay["amount_kop"], 100, False, "Оплата после истечения времени", None)
        notify(db, b["user_id"], "payment_late", f"Время на оплату брони №{b['id']} вышло, деньги возвращены")


def _do_refund(db, b, pay, amount, percent, manual, reason, staff_id):
    if amount <= 0:
        return None
    key = f"refund-{pay['id']}-{db.execute('SELECT COUNT(*) FROM refunds WHERE payment_id=?', (pay['id'],)).fetchone()[0]}"
    payments.provider().refund(pay["provider_payment_id"], amount, key)
    db.execute("INSERT INTO refunds(payment_id,booking_id,amount_kop,percent,is_manual,reason,staff_id,created_at) VALUES(?,?,?,?,?,?,?,?)",
               (pay["id"], b["id"], amount, percent, int(manual), reason, staff_id, fmt(now())))
    _receipt(db, b["id"], "refund", amount)
    return amount


def cancel_preview(db, b, by="driver"):
    paid, ref = paid_and_refunded(db, b["id"])
    p = refund_percent(b, by) if paid else 0
    return {"percent": p, "refund_kop": max(0, min(paid * p // 100, paid - ref)), "paid_kop": paid}


def cancel_booking(db, b, by: str, reason: str = None):
    """by: driver | owner | service."""
    if b["status"] not in ("pending_payment", "confirmed", "active"):
        raise ApiError("bad_status", 409)
    with tx(db):
        b = db.execute("SELECT * FROM bookings WHERE id=?", (b["id"],)).fetchone()
        if b["status"] not in ("pending_payment", "confirmed", "active"):
            raise ApiError("bad_status", 409)
        prev = b["status"]
        db.execute("UPDATE bookings SET status='cancelled', cancelled_by=?, cancel_reason=?, cancel_date=?, expires_at=NULL WHERE id=?",
                   (by, reason, fmt(now()), b["id"]))
        refunded = 0
        if prev == "pending_payment":
            db.execute("UPDATE payments SET status='canceled' WHERE booking_id=? AND status='pending'", (b["id"],))
        else:
            pay = db.execute("SELECT * FROM payments WHERE booking_id=? AND status='succeeded'", (b["id"],)).fetchone()
            pv = cancel_preview(db, b, by)
            refunded = _do_refund(db, b, pay, pv["refund_kop"], pv["percent"], False, f"Отмена: {by}", None) or 0
        place = db.execute("SELECT owner_id,address FROM places WHERE id=?", (b["place_id"],)).fetchone()
        if by == "owner":
            notify(db, b["user_id"], "booking_cancelled", f"Владелец отменил бронь №{b['id']}: {reason}. Деньги возвращены полностью")
        elif by == "driver" and prev != "pending_payment":
            notify(db, place["owner_id"], "booking_cancelled", f"Водитель отменил бронь №{b['id']} ({place['address']})")
    return refunded


def shift_booking(db, b, new_start):
    """Перенос брони с той же длительностью (цена не меняется). Только до начала и не позже,
    чем за час (допущение: иначе владельцу не успеть перепланировать)."""
    if b["status"] != "confirmed":
        raise ApiError("bad_status", 409)
    if now() > parse(b["start_time"]) - timedelta(minutes=current_app.config["FULL_REFUND_BEFORE_MIN"]):
        raise ApiError("bad_status", 409)
    dur = parse(b["end_time"]) - parse(b["start_time"])
    new_end = new_start + dur
    check_interval(new_start, new_end)
    place, city = get_place_city(db, b["place_id"])
    with tx(db):
        if not place_bookable(place, city, new_start, new_end):
            raise ApiError("place_unavailable", 409)
        if overlapping(db, b["place_id"], new_start, new_end, exclude_booking=b["id"]):
            raise ApiError("slot_taken", 409)
        db.execute("UPDATE bookings SET start_time=?, end_time=?, reminded_start=0, reminded_end=0 WHERE id=?",
                   (fmt(new_start), fmt(new_end), b["id"]))


def manual_refund(db, booking_id, amount_kop, reason, staff_id):
    with tx(db):
        b = db.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
        if not b:
            raise ApiError("not_found", 404)
        pay = db.execute("SELECT * FROM payments WHERE booking_id=? AND status='succeeded'", (booking_id,)).fetchone()
        if not pay:
            raise ApiError("bad_status", 409)
        paid, ref = paid_and_refunded(db, booking_id)
        if amount_kop <= 0 or amount_kop > paid - ref:
            raise ApiError("refund_too_big", 409)
        _do_refund(db, b, pay, amount_kop, None, True, reason, staff_id)
        notify(db, b["user_id"], "refund", f"По брони №{b['id']} оформлен возврат {amount_kop / 100:.2f} ₽")


def accrued_for_owner(db, owner_id):
    """Начисления владельцу по завершённым (и отменённым с удержанием) броням, ещё не выплаченным."""
    out = {"gross": 0, "commission": 0, "net": 0, "booking_ids": []}
    for b in db.execute("""SELECT b.id FROM bookings b JOIN places p ON p.id=b.place_id
            WHERE p.owner_id=? AND b.payout_id IS NULL AND b.status IN ('completed','cancelled')""", (owner_id,)):
        paid, ref = paid_and_refunded(db, b["id"])
        com, net = split(paid, ref)
        if paid - ref <= 0:
            continue
        out["gross"] += paid - ref
        out["commission"] += com
        out["net"] += net
        out["booking_ids"].append(b["id"])
    return out
