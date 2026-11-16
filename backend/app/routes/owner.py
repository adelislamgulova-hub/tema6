"""Кабинет владельца места."""
import json
import os
import uuid

from flask import Blueprint, jsonify, g, request, current_app

from .. import services
from ..auth import json_body, user_required
from ..db import get_db, rows
from ..places_view import dto
from ..rules import validate_schedule
from ..util import ApiError, now, fmt
from .bookings import booking_dto

bp = Blueprint("owner", __name__, url_prefix="/api/owner")
MAGIC = {b"\xff\xd8\xff": "jpg", b"\x89PNG\r\n\x1a\n": "png"}


def _owned(db, pid):
    p = db.execute("SELECT * FROM places WHERE id=? AND owner_id=?", (pid, g.user["id"])).fetchone()
    if not p:
        raise ApiError("not_found", 404)
    return p


def _place_out(db, p):
    city = db.execute("SELECT * FROM cities WHERE id=?", (p["city_id"],)).fetchone()
    d = dto(db, p, city, with_status=False)
    d.update(moderation_status=p["status"], reject_reason=p["reject_reason"], is_closed=bool(p["is_closed"]), close_reason=p["close_reason"])
    return d


def _fields(d, partial):
    out = {}
    def need(k):
        if k not in d:
            if not partial:
                raise ApiError("validation", field=k)
            return False
        return True
    if need("address"):
        out["address"] = str(d["address"]).strip()[:300]
        if not out["address"]:
            raise ApiError("validation", field="address")
    for k in ("lat", "lon"):
        if need(k):
            try:
                out[k] = float(d[k])
            except (TypeError, ValueError):
                raise ApiError("validation", field=k)
    if "lat" in out and not -90 <= out["lat"] <= 90 or "lon" in out and not -180 <= out["lon"] <= 180:
        raise ApiError("validation", field="lat")
    if need("type_id"):
        out["type_id"] = int(d["type_id"])
    if need("price_per_hour_kop"):
        try:
            out["price_per_hour_kop"] = int(d["price_per_hour_kop"])
        except (TypeError, ValueError):
            raise ApiError("validation", field="price_per_hour_kop")
        if out["price_per_hour_kop"] <= 0:
            raise ApiError("validation", field="price_per_hour_kop")
    if need("schedule"):
        out["schedule_json"] = json.dumps(validate_schedule(d["schedule"]))
    for k, n in (("district", 100), ("description", 2000), ("entry_rules", 1000)):
        if k in d:
            out[k] = (d[k] or "").strip()[:n] or None
    return out


@bp.get("/places")
@user_required
def my_places():
    db = get_db()
    return jsonify([_place_out(db, p) for p in db.execute("SELECT * FROM places WHERE owner_id=? ORDER BY id DESC", (g.user["id"],))])


@bp.post("/places")
@user_required
def add_place():
    db = get_db()
    f = _fields(json_body(), partial=False)
    if not db.execute("SELECT 1 FROM parking_types WHERE id=?", (f["type_id"],)).fetchone():
        raise ApiError("validation", field="type_id")
    city = db.execute("SELECT id FROM cities WHERE is_active=1 AND id=?", (json_body().get("city_id", 1),)).fetchone()
    if not city:
        raise ApiError("validation", field="city_id")
    f.update(owner_id=g.user["id"], city_id=city["id"], status="pending", created_at=fmt(now()), updated_at=fmt(now()))
    cur = db.execute(f"INSERT INTO places({','.join(f)}) VALUES({','.join('?' * len(f))})", list(f.values()))
    return jsonify(_place_out(db, db.execute("SELECT * FROM places WHERE id=?", (cur.lastrowid,)).fetchone())), 201


@bp.get("/places/<int:pid>")
@user_required
def get_place(pid):
    db = get_db()
    p = _owned(db, pid)
    out = _place_out(db, p)
    out["checks"] = rows(db.execute("SELECT result,comment,created_at FROM checks WHERE place_id=? ORDER BY id DESC", (pid,)))
    return jsonify(out)


@bp.patch("/places/<int:pid>")
@user_required
def edit_place(pid):
    """Цена и расписание правятся без перепроверки. Адрес, точка, тип и описание - с повторной проверкой."""
    db = get_db()
    p = _owned(db, pid)
    f = _fields(json_body(), partial=True)
    f.pop("owner_id", None)
    recheck = any(k in f and f[k] != p[k] for k in ("address", "lat", "lon", "type_id", "description"))
    if recheck and p["status"] in ("approved", "rejected"):
        f.update(status="pending", reject_reason=None)
    elif p["status"] == "blocked":
        raise ApiError("bad_status", 409)
    if f:
        f["updated_at"] = fmt(now())
        db.execute(f"UPDATE places SET {','.join(k + '=?' for k in f)} WHERE id=?", [*f.values(), pid])
    return jsonify(_place_out(db, db.execute("SELECT * FROM places WHERE id=?", (pid,)).fetchone()))


@bp.post("/places/<int:pid>/photos")
@user_required
def upload_photo(pid):
    db = get_db()
    _owned(db, pid)
    f = request.files.get("photo")
    data = f.read(current_app.config["MAX_UPLOAD_BYTES"] + 1) if f else b""
    if not data or len(data) > current_app.config["MAX_UPLOAD_BYTES"]:
        raise ApiError("bad_file", 400)
    ext = next((e for m, e in MAGIC.items() if data.startswith(m)), None) or ("webp" if data[:4] == b"RIFF" and data[8:12] == b"WEBP" else None)
    if not ext:  # проверяем содержимое, а не расширение
        raise ApiError("bad_file", 400)
    name = f"{uuid.uuid4().hex}.{ext}"
    os.makedirs(current_app.config["MEDIA_DIR"], exist_ok=True)
    with open(os.path.join(current_app.config["MEDIA_DIR"], name), "wb") as fh:
        fh.write(data)
    cur = db.execute("INSERT INTO place_photos(place_id,filename) VALUES(?,?)", (pid, name))
    return jsonify({"id": cur.lastrowid, "url": f"/media/{name}"}), 201


@bp.post("/places/<int:pid>/close")
@user_required
def close_place(pid):
    """Правило 8: уже оплаченные брони остаются, новые не принимаются."""
    db = get_db()
    _owned(db, pid)
    db.execute("UPDATE places SET is_closed=1, close_reason=? WHERE id=?", ((json_body().get("reason") or "")[:300] or None, pid))
    return jsonify({"ok": True})


@bp.post("/places/<int:pid>/open")
@user_required
def open_place(pid):
    db = get_db()
    _owned(db, pid)
    db.execute("UPDATE places SET is_closed=0, close_reason=NULL WHERE id=?", (pid,))
    return jsonify({"ok": True})


@bp.get("/bookings")
@user_required
def bookings():
    db = get_db()
    q = """SELECT b.* FROM bookings b JOIN places p ON p.id=b.place_id WHERE p.owner_id=? AND b.status!='expired'"""
    scope = request.args.get("scope")
    if scope == "upcoming":
        q += " AND b.status IN ('pending_payment','confirmed','active')"
    elif scope == "past":
        q += " AND b.status IN ('completed','cancelled')"
    return jsonify([booking_dto(db, b, for_owner=True) for b in db.execute(q + " ORDER BY b.start_time DESC", (g.user["id"],))])


@bp.post("/bookings/<int:bid>/cancel")
@user_required
def cancel(bid):
    """Крайний случай: отмена владельцем с обязательной причиной, водителю возвращается 100 %."""
    db = get_db()
    reason = (json_body().get("reason") or "").strip()
    if not reason:
        raise ApiError("reason_required", 400)
    b = db.execute("SELECT b.* FROM bookings b JOIN places p ON p.id=b.place_id WHERE b.id=? AND p.owner_id=?", (bid, g.user["id"])).fetchone()
    if not b:
        raise ApiError("not_found", 404)
    services.cancel_booking(db, b, "owner", reason[:300])
    return jsonify(booking_dto(db, db.execute("SELECT * FROM bookings WHERE id=?", (bid,)).fetchone(), for_owner=True))


@bp.get("/earnings")
@user_required
def earnings():
    db = get_db()
    acc = services.accrued_for_owner(db, g.user["id"])
    return jsonify({
        "accrued_gross_kop": acc["gross"], "accrued_commission_kop": acc["commission"], "accrued_net_kop": acc["net"],
        "payouts": rows(db.execute("SELECT id,period_start,period_end,amount_kop,commission_kop,status,created_at FROM payouts WHERE owner_id=? ORDER BY id DESC", (g.user["id"],))),
        "payout_details": g.user["payout_details"],
    })
