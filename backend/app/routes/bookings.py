"""Водитель: бронь, оплата, отмена, перенос, чеки, жалобы."""
from flask import Blueprint, jsonify, g, request

from .. import payments, services
from ..auth import json_body, user_required
from ..db import get_db, rows
from ..places_view import photos
from ..util import ApiError, now, fmt, parse

bp = Blueprint("bookings", __name__, url_prefix="/api")


def booking_dto(db, b, for_owner=False):
    p = db.execute("SELECT * FROM places WHERE id=?", (b["place_id"],)).fetchone()
    pay = db.execute("SELECT * FROM payments WHERE booking_id=? ORDER BY id DESC LIMIT 1", (b["id"],)).fetchone()
    paid, ref = services.paid_and_refunded(db, b["id"])
    d = {
        "id": b["id"], "place_id": b["place_id"], "address": p["address"], "lat": p["lat"], "lon": p["lon"],
        "entry_rules": p["entry_rules"], "photo": (photos(db, p["id"]) or [None])[0],
        "start": b["start_time"], "end": b["end_time"], "status": b["status"],
        "total_price_kop": b["total_price_kop"], "paid_kop": paid, "refunded_kop": ref,
        "expires_at": b["expires_at"], "cancelled_by": b["cancelled_by"], "cancel_reason": b["cancel_reason"],
        "payment_id": pay["id"] if pay else None,
        "payment_url": pay["confirmation_url"] if pay and pay["status"] == "pending" else None,
    }
    if b["status"] in ("pending_payment", "confirmed", "active"):
        d["cancel_preview"] = services.cancel_preview(db, b, "owner" if for_owner else "driver")
    if for_owner:
        u = db.execute("SELECT name,phone FROM users WHERE id=?", (b["user_id"],)).fetchone()
        d["driver"] = {"name": u["name"], "phone": u["phone"]} if u else None
    return d


def my_booking(db, bid):
    b = db.execute("SELECT * FROM bookings WHERE id=? AND user_id=?", (bid, g.user["id"])).fetchone()
    if not b:
        raise ApiError("not_found", 404)
    return b


@bp.post("/bookings")
@user_required
def create():
    db, d = get_db(), json_body()
    if d.get("consent"):  # условия и согласие на обработку ПДн (152-ФЗ) - при первой брони
        db.execute("UPDATE users SET consent_at=COALESCE(consent_at,?) WHERE id=?", (fmt(now()), g.user["id"]))
    elif not db.execute("SELECT consent_at FROM users WHERE id=?", (g.user["id"],)).fetchone()[0]:
        raise ApiError("consent_required", 400)
    try:
        pid = int(d.get("place_id"))
    except (TypeError, ValueError):
        raise ApiError("validation", field="place_id")
    bid = services.create_booking(db, g.user, pid, parse(d.get("start")), parse(d.get("end")))
    return jsonify(booking_dto(db, db.execute("SELECT * FROM bookings WHERE id=?", (bid,)).fetchone())), 201


@bp.get("/bookings")
@user_required
def mine():
    db = get_db()
    scope = request.args.get("scope")
    q = "SELECT * FROM bookings WHERE user_id=?"
    if scope == "upcoming":
        q += " AND status IN ('pending_payment','confirmed','active')"
    elif scope == "past":
        q += " AND status IN ('completed','cancelled','expired')"
    return jsonify([booking_dto(db, b) for b in db.execute(q + " ORDER BY start_time DESC", (g.user["id"],))])


@bp.get("/bookings/<int:bid>")
@user_required
def one(bid):
    db = get_db()
    return jsonify(booking_dto(db, my_booking(db, bid)))


@bp.post("/bookings/<int:bid>/cancel")
@user_required
def cancel(bid):
    db = get_db()
    b = my_booking(db, bid)
    refunded = services.cancel_booking(db, b, "driver", (request.get_json(silent=True) or {}).get("reason"))
    out = booking_dto(db, db.execute("SELECT * FROM bookings WHERE id=?", (bid,)).fetchone())
    out["refunded_now_kop"] = refunded
    return jsonify(out)


@bp.patch("/bookings/<int:bid>")
@user_required
def shift(bid):
    db = get_db()
    b = my_booking(db, bid)
    services.shift_booking(db, b, parse(json_body().get("start")))
    return jsonify(booking_dto(db, db.execute("SELECT * FROM bookings WHERE id=?", (bid,)).fetchone()))


@bp.get("/bookings/<int:bid>/receipts")
@user_required
def receipts(bid):
    db = get_db()
    b = my_booking(db, bid)
    p = db.execute("SELECT address FROM places WHERE id=?", (b["place_id"],)).fetchone()
    return jsonify([{**dict(r), "address": p["address"], "seller": "ООО «ГородПаркинг»"} for r in
                    db.execute("SELECT * FROM receipts WHERE booking_id=? ORDER BY id", (bid,))])


@bp.post("/bookings/<int:bid>/complaint")
@user_required
def complaint(bid):
    """Жаловаться можно только по брони, которая уже была (подтверждена оплатой)."""
    db, d = get_db(), json_body()
    b = my_booking(db, bid)
    if b["status"] not in ("confirmed", "active", "completed", "cancelled") or not services.paid_and_refunded(db, bid)[0]:
        raise ApiError("bad_status", 409)
    kind = d.get("kind")
    if kind not in ("occupied", "mismatch", "other"):
        raise ApiError("validation", field="kind")
    cur = db.execute("INSERT INTO complaints(booking_id,user_id,place_id,kind,description,created_at) VALUES(?,?,?,?,?,?)",
                     (bid, g.user["id"], b["place_id"], kind, (d.get("description") or "")[:1000], fmt(now())))
    return jsonify({"id": cur.lastrowid}), 201


# ---------- платежи ----------
@bp.post("/payments/<int:pid>/mock-pay")
@user_required
def mock_pay(pid):
    """Демо-оплата: имитирует возврат с платёжной страницы. Работает только с провайдером mock."""
    db = get_db()
    if payments.provider().name != "mock":
        raise ApiError("forbidden", 403)
    pay = db.execute("SELECT p.* FROM payments p JOIN bookings b ON b.id=p.booking_id WHERE p.id=? AND b.user_id=?",
                     (pid, g.user["id"])).fetchone()
    if not pay:
        raise ApiError("not_found", 404)
    services.mark_paid(db, pid, "mock")
    return jsonify(booking_dto(db, db.execute("SELECT * FROM bookings WHERE id=?", (pay["booking_id"],)).fetchone()))


@bp.post("/payments/webhook")
def webhook():
    """Уведомление ЮKassa. Статус перепроверяем запросом к API провайдера."""
    obj = (request.get_json(silent=True) or {}).get("object") or {}
    db = get_db()
    pay = db.execute("SELECT * FROM payments WHERE provider_payment_id=?", (obj.get("id"),)).fetchone()
    if pay and payments.provider().verify(pay["provider_payment_id"]) == "succeeded":
        services.mark_paid(db, pay["id"], (obj.get("payment_method") or {}).get("type", "card"))
    return jsonify({"ok": True})
