"""Гость и вход: карта, поиск, карточка места, вход по коду, профиль."""
import hmac
import re
import secrets
from datetime import timedelta

from flask import Blueprint, jsonify, request, g, current_app

from ..auth import create_session, json_body, user_required
from ..db import get_db, rows, tx
from ..notify import send_code
from ..places_view import dto
from ..rules import check_interval, overlapping, place_bookable, refund_percent
from ..services import get_place_city
from ..util import ApiError, now, fmt, parse, sha, price_for

bp = Blueprint("public", __name__, url_prefix="/api")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_identifier(raw):
    raw = (raw or "").strip()
    if "@" in raw:
        if not EMAIL.match(raw):
            raise ApiError("invalid_identifier")
        return raw.lower(), "email"
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits[0] in "78":
        digits = digits[1:]
    if len(digits) != 10:
        raise ApiError("invalid_identifier")
    return "+7" + digits, "phone"


# ---------- справочники ----------
@bp.get("/config")
def config():
    c = current_app.config
    db = get_db()
    return jsonify({
        "cities": rows(db.execute("SELECT id,name,timezone,lat,lon FROM cities WHERE is_active=1")),
        "parking_types": rows(db.execute("SELECT id,name FROM parking_types")),
        "rules": {"min_duration_min": c["MIN_DURATION_MIN"], "step_min": c["STEP_MIN"], "max_days_ahead": c["MAX_DAYS_AHEAD"],
                  "payment_timeout_min": c["PAYMENT_TIMEOUT_MIN"], "commission_percent": c["COMMISSION_PERCENT"]},
        "payment_provider": c["PAYMENT_PROVIDER"],
    })


# ---------- вход по коду ----------
@bp.post("/auth/request-code")
def request_code():
    c = current_app.config
    db = get_db()
    ident, _ = normalize_identifier(json_body().get("identifier"))
    last = db.execute("SELECT created_at FROM otp_codes WHERE identifier=? ORDER BY id DESC LIMIT 1", (ident,)).fetchone()
    if last and parse(last["created_at"]) > now() - timedelta(seconds=c["OTP_RESEND_SEC"]):
        raise ApiError("too_many_requests", 429)
    code = f"{secrets.randbelow(1_000_000):06d}"
    db.execute("INSERT INTO otp_codes(identifier,code_hash,created_at,expires_at) VALUES(?,?,?,?)",
               (ident, sha(ident + code), fmt(now()), fmt(now() + timedelta(seconds=c["OTP_TTL_SEC"]))))
    send_code(ident, code)
    out = {"ok": True, "resend_after_sec": c["OTP_RESEND_SEC"]}
    if c["DEV_EXPOSE_CODE"]:
        out["dev_code"] = code
    return jsonify(out)


@bp.post("/auth/verify")
def verify_code():
    c = current_app.config
    db = get_db()
    d = json_body()
    ident, kind = normalize_identifier(d.get("identifier"))
    code = str(d.get("code", "")).strip()
    o = db.execute("SELECT * FROM otp_codes WHERE identifier=? AND used=0 ORDER BY id DESC LIMIT 1", (ident,)).fetchone()
    if not o or parse(o["expires_at"]) <= now() or o["attempts"] >= c["OTP_MAX_ATTEMPTS"]:
        raise ApiError("code_expired", 400)
    if not hmac.compare_digest(o["code_hash"], sha(ident + code)):
        db.execute("UPDATE otp_codes SET attempts=attempts+1 WHERE id=?", (o["id"],))
        raise ApiError("invalid_code", 400)
    with tx(db):
        if db.execute("UPDATE otp_codes SET used=1 WHERE id=? AND used=0", (o["id"],)).rowcount == 0:
            raise ApiError("code_expired", 400)
        u = db.execute(f"SELECT * FROM users WHERE {kind}=?", (ident,)).fetchone()
        is_new = u is None
        if is_new:  # первый вход по коду создаёт аккаунт
            cur = db.execute(f"INSERT INTO users({kind},created_at) VALUES(?,?)", (ident, fmt(now())))
            u = db.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
        if u["is_blocked"]:
            raise ApiError("account_blocked", 403)
        if u["deletion_requested_at"]:  # вход отменяет удаление аккаунта
            db.execute("UPDATE users SET deletion_requested_at=NULL WHERE id=?", (u["id"],))
        token = create_session(db, "user", u["id"], timedelta(days=c["USER_SESSION_DAYS"]))
    return jsonify({"token": token, "is_new": is_new, "user": user_dto(u)})


@bp.post("/auth/logout")
@user_required
def logout():
    h = request.headers["Authorization"][7:]
    get_db().execute("DELETE FROM sessions WHERE token_hash=?", (sha(h),))
    return jsonify({"ok": True})


def user_dto(u):
    return {"id": u["id"], "name": u["name"], "phone": u["phone"], "email": u["email"],
            "payout_details": u["payout_details"], "consent": bool(u["consent_at"])}


@bp.get("/me")
@user_required
def me():
    db = get_db()
    d = user_dto(g.user)
    d["is_owner"] = db.execute("SELECT 1 FROM places WHERE owner_id=? LIMIT 1", (g.user["id"],)).fetchone() is not None
    return jsonify(d)


@bp.patch("/me")
@user_required
def update_me():
    d, db = json_body(), get_db()
    if "name" in d:
        db.execute("UPDATE users SET name=? WHERE id=?", ((d["name"] or "").strip()[:100] or None, g.user["id"]))
    if "payout_details" in d:
        db.execute("UPDATE users SET payout_details=? WHERE id=?", ((d["payout_details"] or "").strip()[:500] or None, g.user["id"]))
    if d.get("consent"):
        db.execute("UPDATE users SET consent_at=COALESCE(consent_at,?) WHERE id=?", (fmt(now()), g.user["id"]))
    return jsonify(user_dto(db.execute("SELECT * FROM users WHERE id=?", (g.user["id"],)).fetchone()))


@bp.delete("/me")
@user_required
def delete_me():
    db = get_db()
    busy = db.execute("""SELECT 1 FROM bookings b JOIN places p ON p.id=b.place_id WHERE (b.user_id=? OR p.owner_id=?)
                         AND b.status IN ('pending_payment','confirmed','active') LIMIT 1""", (g.user["id"], g.user["id"])).fetchone()
    if busy:
        raise ApiError("has_active_bookings", 409)
    db.execute("UPDATE users SET deletion_requested_at=? WHERE id=?", (fmt(now()), g.user["id"]))
    db.execute("UPDATE places SET is_closed=1, close_reason='Аккаунт удалён' WHERE owner_id=?", (g.user["id"],))
    db.execute("DELETE FROM sessions WHERE kind='user' AND subject_id=?", (g.user["id"],))
    return jsonify({"ok": True})


@bp.get("/notifications")
@user_required
def notifications():
    return jsonify(rows(get_db().execute("SELECT id,kind,text,is_read,created_at FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 50", (g.user["id"],))))


# ---------- карта и поиск (доступны гостю) ----------
def _interval_args():
    a, b = request.args.get("from"), request.args.get("to")
    if a and b:
        s, e = parse(a), parse(b)
        if e <= s:
            raise ApiError("bad_interval")
        return s, e
    return None, None


@bp.get("/places")
def search_places():
    db, args = get_db(), request.args
    city = db.execute("SELECT * FROM cities WHERE id=? AND is_active=1", (args.get("city_id", 1),)).fetchone()
    if not city:
        raise ApiError("not_found", 404)
    s, e = _interval_args()
    origin = None
    try:
        if args.get("lat") and args.get("lon"):
            origin = (float(args["lat"]), float(args["lon"]))
        max_price = int(float(args["max_price"]) * 100) if args.get("max_price") else None
        radius = int(args["radius_m"]) if args.get("radius_m") else None
        type_id = int(args["type_id"]) if args.get("type_id") else None
    except ValueError:
        raise ApiError("validation")
    q = (args.get("q") or "").strip().casefold()
    out = []
    for p in db.execute("SELECT * FROM places WHERE city_id=? AND status='approved'", (city["id"],)).fetchall():
        if q and q not in f"{p['address']} {p['district'] or ''}".casefold():
            continue
        if max_price is not None and p["price_per_hour_kop"] > max_price:
            continue
        if type_id and p["type_id"] != type_id:
            continue
        d = dto(db, p, city, s, e, origin)
        if radius and origin and d["distance_m"] > radius:
            continue
        if args.get("only_free") == "1" and d["status"] != "free":
            continue
        out.append(d)
    if origin:
        out.sort(key=lambda x: x["distance_m"])
    return jsonify(out[:300])


@bp.get("/places/<int:pid>")
def place_card(pid):
    db = get_db()
    p, city = get_place_city(db, pid)
    if p["status"] != "approved":
        raise ApiError("not_found", 404)
    s, e = _interval_args()
    origin = (float(request.args["lat"]), float(request.args["lon"])) if request.args.get("lat") and request.args.get("lon") else None
    return jsonify(dto(db, p, city, s, e, origin))


@bp.get("/places/<int:pid>/quote")
def quote(pid):
    """Итоговая цена и условия отмены до брони (видны гостю)."""
    db = get_db()
    p, city = get_place_city(db, pid)
    s, e = _interval_args()
    if not s:
        raise ApiError("validation")
    minutes = check_interval(s, e)
    ok = place_bookable(p, city, s, e) and not overlapping(db, pid, s, e)
    total = price_for(p["price_per_hour_kop"], minutes)
    fake = {"start_time": fmt(s)}
    return jsonify({
        "available": ok, "minutes": minutes, "total_price_kop": total,
        "price_per_hour_kop": p["price_per_hour_kop"],
        "refund_now_percent": refund_percent(fake, "driver"),
        "payment_timeout_min": current_app.config["PAYMENT_TIMEOUT_MIN"],
    })
