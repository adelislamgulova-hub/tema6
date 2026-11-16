"""Админ-панель: сотрудники, модерация, брони, жалобы, возвраты, статистика, журнал."""
import secrets
from datetime import timedelta

from flask import Blueprint, jsonify, g, request, current_app
from werkzeug.security import check_password_hash, generate_password_hash

from .. import services
from ..auth import PERMISSIONS, create_session, json_body, log_action, staff_required
from ..db import get_db, rows
from ..notify import notify
from ..places_view import dto
from ..rules import schedule_minutes
from ..util import ApiError, now, fmt, parse, new_totp_secret, totp_ok
from .bookings import booking_dto

import json

bp = Blueprint("staff", __name__, url_prefix="/api/staff")


@bp.post("/login")
def login():
    d, db = json_body(), get_db()
    st = db.execute("SELECT * FROM staff WHERE email=? AND is_active=1", ((d.get("email") or "").strip().lower(),)).fetchone()
    # одинаковая ошибка на любой неверный шаг, чтобы не подсказывать, что именно не так
    if not st or not check_password_hash(st["password_hash"], d.get("password") or "") or not totp_ok(st["totp_secret"], str(d.get("code") or "")):
        raise ApiError("bad_credentials", 401)
    tok = create_session(db, "staff", st["id"], timedelta(hours=current_app.config["STAFF_SESSION_HOURS"]))
    return jsonify({"token": tok, "staff": {"id": st["id"], "name": st["name"], "role": st["role"], "permissions": sorted(PERMISSIONS[st["role"]])}})


@bp.get("/me")
@staff_required()
def me():
    s = g.staff
    return jsonify({"id": s["id"], "name": s["name"], "role": s["role"], "permissions": sorted(PERMISSIONS[s["role"]])})


# ---------- модерация мест ----------
@bp.get("/places")
@staff_required("moderate")
def places():
    db, st = get_db(), request.args.get("status", "pending")
    out = []
    for p in db.execute("SELECT * FROM places WHERE status=? ORDER BY updated_at", (st,)):
        city = db.execute("SELECT * FROM cities WHERE id=?", (p["city_id"],)).fetchone()
        d = dto(db, p, city, with_status=False)
        d.update(moderation_status=p["status"], is_closed=bool(p["is_closed"]), reject_reason=p["reject_reason"], owner_id=p["owner_id"])
        out.append(d)
    return jsonify(out)


def _place_action(pid, new_status, reason_required):
    db, d = get_db(), json_body()
    reason = (d.get("reason") or "").strip()
    if reason_required and not reason:
        raise ApiError("reason_required", 400)
    p = db.execute("SELECT * FROM places WHERE id=?", (pid,)).fetchone()
    if not p:
        raise ApiError("not_found", 404)
    db.execute("UPDATE places SET status=?, reject_reason=?, updated_at=? WHERE id=?", (new_status, reason or None, fmt(now()), pid))
    db.execute("INSERT INTO checks(place_id,staff_id,result,comment,created_at) VALUES(?,?,?,?,?)", (pid, g.staff["id"], new_status, reason, fmt(now())))
    log_action(f"place.{new_status}", f"place:{pid}", reason)
    text = {"approved": "Место одобрено и появилось на карте", "rejected": f"Место отклонено: {reason}", "blocked": f"Место заблокировано: {reason}"}[new_status]
    notify(db, p["owner_id"], "place_" + new_status, f"{p['address']}: {text}")
    return jsonify({"ok": True})


@bp.post("/places/<int:pid>/approve")
@staff_required("moderate")
def approve(pid):
    return _place_action(pid, "approved", False)


@bp.post("/places/<int:pid>/reject")
@staff_required("moderate")
def reject(pid):
    return _place_action(pid, "rejected", True)


@bp.post("/places/<int:pid>/block")
@staff_required("moderate")
def block_place(pid):
    return _place_action(pid, "blocked", True)


@bp.patch("/places/<int:pid>")
@staff_required("moderate")
def edit_place(pid):
    db, d = get_db(), json_body()
    allowed = {k: d[k] for k in ("address", "district", "description", "entry_rules") if k in d}
    if not allowed:
        raise ApiError("validation")
    db.execute(f"UPDATE places SET {','.join(k + '=?' for k in allowed)}, updated_at=? WHERE id=?", [*allowed.values(), fmt(now()), pid])
    log_action("place.edit", f"place:{pid}", json.dumps(allowed, ensure_ascii=False))
    return jsonify({"ok": True})


# ---------- брони ----------
@bp.get("/bookings")
@staff_required("all_bookings")
def bookings():
    db, q = get_db(), (request.args.get("q") or "").strip()
    sql = """SELECT b.* FROM bookings b JOIN places p ON p.id=b.place_id JOIN users u ON u.id=b.user_id"""
    args = []
    if q:
        sql += " WHERE b.id=? OR u.phone LIKE ? OR u.email LIKE ? OR p.address LIKE ?"
        args = [int(q) if q.isdigit() else -1, f"%{q}%", f"%{q}%", f"%{q}%"]
    return jsonify([booking_dto(db, b, for_owner=True) | {"user_id": b["user_id"]} for b in db.execute(sql + " ORDER BY b.id DESC LIMIT 100", args)])


@bp.post("/bookings/<int:bid>/refund")
@staff_required("refunds")
def refund(bid):
    d = json_body()
    try:
        amount = int(d.get("amount_kop"))
    except (TypeError, ValueError):
        raise ApiError("validation", field="amount_kop")
    reason = (d.get("reason") or "").strip()
    if not reason:
        raise ApiError("reason_required", 400)
    services.manual_refund(get_db(), bid, amount, reason, g.staff["id"])
    log_action("refund.manual", f"booking:{bid}", f"{amount} коп: {reason}")
    return jsonify({"ok": True})


@bp.get("/payments")
@staff_required("refunds")
def payments_list():
    db = get_db()
    return jsonify({
        "payments": rows(db.execute("SELECT id,booking_id,amount_kop,status,provider,method,paid_at FROM payments ORDER BY id DESC LIMIT 200")),
        "refunds": rows(db.execute("SELECT id,booking_id,amount_kop,percent,is_manual,reason,created_at FROM refunds ORDER BY id DESC LIMIT 200")),
        "payouts": rows(db.execute("SELECT * FROM payouts ORDER BY id DESC LIMIT 200")),
    })


# ---------- жалобы ----------
@bp.get("/complaints")
@staff_required("complaints")
def complaints():
    return jsonify(rows(get_db().execute("""SELECT c.*, p.address FROM complaints c JOIN places p ON p.id=c.place_id
        WHERE c.status=? ORDER BY c.id DESC""", (request.args.get("status", "open"),))))


@bp.post("/complaints/<int:cid>/resolve")
@staff_required("complaints")
def resolve(cid):
    d, db = json_body(), get_db()
    status = d.get("status", "resolved")
    if status not in ("resolved", "rejected"):
        raise ApiError("validation", field="status")
    c = db.execute("SELECT * FROM complaints WHERE id=?", (cid,)).fetchone()
    if not c:
        raise ApiError("not_found", 404)
    db.execute("UPDATE complaints SET status=?, resolution=?, handled_by=? WHERE id=?", (status, (d.get("resolution") or "")[:1000], g.staff["id"], cid))
    log_action("complaint." + status, f"complaint:{cid}")
    notify(db, c["user_id"], "complaint", f"Жалоба №{cid} рассмотрена: {d.get('resolution') or status}")
    return jsonify({"ok": True})


# ---------- пользователи ----------
@bp.get("/users")
@staff_required("users")
def users():
    q = f"%{(request.args.get('q') or '').strip()}%"
    return jsonify(rows(get_db().execute("SELECT id,name,phone,email,is_blocked,created_at FROM users WHERE phone LIKE ? OR email LIKE ? OR name LIKE ? ORDER BY id DESC LIMIT 100", (q, q, q))))


@bp.post("/users/<int:uid>/<action>")
@staff_required("users")
def user_block(uid, action):
    if action not in ("block", "unblock"):
        raise ApiError("not_found", 404)
    db = get_db()
    db.execute("UPDATE users SET is_blocked=? WHERE id=?", (1 if action == "block" else 0, uid))
    if action == "block":
        db.execute("DELETE FROM sessions WHERE kind='user' AND subject_id=?", (uid,))
    log_action("user." + action, f"user:{uid}", (request.get_json(silent=True) or {}).get("reason"))
    return jsonify({"ok": True})


# ---------- статистика ----------
@bp.get("/stats")
@staff_required("stats")
def stats():
    db, t = get_db(), now()
    since = t - timedelta(days=30)
    one = lambda q, *a: db.execute(q, a).fetchone()[0]
    paid = one("SELECT COALESCE(SUM(amount_kop),0) FROM payments WHERE status='succeeded'")
    refunded = one("SELECT COALESCE(SUM(amount_kop),0) FROM refunds")
    revenue = paid - refunded
    # загрузка: забронированные минуты / доступные по расписанию минуты за 30 дней
    booked = avail = 0
    for p in db.execute("SELECT * FROM places WHERE status='approved' AND is_closed=0"):
        city = db.execute("SELECT timezone FROM cities WHERE id=?", (p["city_id"],)).fetchone()
        avail += schedule_minutes(json.loads(p["schedule_json"]), city["timezone"], since, t)
        for b in db.execute("SELECT start_time,end_time FROM bookings WHERE place_id=? AND status IN ('confirmed','active','completed') AND end_time > ? AND start_time < ?", (p["id"], fmt(since), fmt(t))):
            booked += max(0, (min(parse(b["end_time"]), t) - max(parse(b["start_time"]), since)).total_seconds() // 60)
    return jsonify({
        "users": one("SELECT COUNT(*) FROM users WHERE purged_at IS NULL"),
        "places_approved": one("SELECT COUNT(*) FROM places WHERE status='approved'"),
        "places_pending": one("SELECT COUNT(*) FROM places WHERE status='pending'"),
        "bookings_total": one("SELECT COUNT(*) FROM bookings WHERE status IN ('confirmed','active','completed','cancelled') AND EXISTS(SELECT 1 FROM payments WHERE booking_id=bookings.id AND status='succeeded')"),
        "bookings_by_status": {r["status"]: r["n"] for r in db.execute("SELECT status, COUNT(*) n FROM bookings GROUP BY status")},
        "revenue_gross_kop": revenue,
        "commission_kop": services_commission(db),
        "occupancy_30d_percent": round(100 * booked / avail, 1) if avail else 0.0,
    })


def services_commission(db):
    from ..rules import split
    total = 0
    for b in db.execute("SELECT id FROM bookings WHERE status IN ('completed','cancelled')"):
        total += split(*services.paid_and_refunded(db, b["id"]))[0]
    return total


# ---------- сотрудники и журнал ----------
@bp.get("/employees")
@staff_required("staff")
def employees():
    return jsonify(rows(get_db().execute("SELECT id,name,email,role,is_active,created_at FROM staff ORDER BY id")))


@bp.post("/employees")
@staff_required("staff")
def add_employee():
    d, db = json_body(), get_db()
    if d.get("role") not in PERMISSIONS or not d.get("email") or not d.get("name"):
        raise ApiError("validation")
    password, secret = secrets.token_urlsafe(10), new_totp_secret()
    try:
        cur = db.execute("INSERT INTO staff(name,email,role,password_hash,totp_secret,created_at) VALUES(?,?,?,?,?,?)",
                         (d["name"], d["email"].strip().lower(), d["role"], generate_password_hash(password), secret, fmt(now())))
    except Exception:
        raise ApiError("validation", field="email")
    log_action("staff.create", f"staff:{cur.lastrowid}", d["role"])
    # пароль и секрет 2FA показываем один раз
    return jsonify({"id": cur.lastrowid, "password": password, "totp_secret": secret}), 201


@bp.patch("/employees/<int:sid>")
@staff_required("staff")
def edit_employee(sid):
    d, db = json_body(), get_db()
    if sid == g.staff["id"] and (d.get("is_active") is False or ("role" in d and d["role"] != g.staff["role"])):
        raise ApiError("forbidden", 403)  # админ не может лишить себя прав
    if "role" in d:
        if d["role"] not in PERMISSIONS:
            raise ApiError("validation", field="role")
        db.execute("UPDATE staff SET role=? WHERE id=?", (d["role"], sid))
    if "is_active" in d:
        db.execute("UPDATE staff SET is_active=? WHERE id=?", (int(bool(d["is_active"])), sid))
        if not d["is_active"]:
            db.execute("DELETE FROM sessions WHERE kind='staff' AND subject_id=?", (sid,))
    log_action("staff.edit", f"staff:{sid}", json.dumps(d))
    return jsonify({"ok": True})


@bp.get("/log")
@staff_required("staff")
def action_log():
    return jsonify(rows(get_db().execute("""SELECT l.id,l.action,l.target,l.details,l.created_at,s.name AS staff_name
        FROM action_log l LEFT JOIN staff s ON s.id=l.staff_id ORDER BY l.id DESC LIMIT 200""")))
