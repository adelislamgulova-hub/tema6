"""Представление мест для карты: статус, расстояние, фильтры."""
import json
import math
from datetime import timedelta

from .rules import schedule_covers
from .util import now, parse, fmt


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return int(2 * r * math.asin(math.sqrt(a)))


def place_status(db, p, city, start, end):
    """free | booked | occupied | unavailable (BRD, раздел 7)."""
    if p["status"] != "approved" or p["is_closed"]:
        return "unavailable"
    if not schedule_covers(json.loads(p["schedule_json"]), city["timezone"], start, end):
        return "unavailable"
    b = db.execute("""SELECT start_time,status FROM bookings WHERE place_id=? AND start_time < ? AND end_time > ?
        AND (status IN ('confirmed','active') OR (status='pending_payment' AND expires_at > ?)) ORDER BY start_time LIMIT 1""",
                   (p["id"], fmt(end), fmt(start), fmt(now()))).fetchone()
    if not b:
        return "free"
    return "occupied" if (b["status"] == "active" or parse(b["start_time"]) <= now()) else "booked"


def photos(db, place_id):
    return [f"/media/{r['filename']}" for r in db.execute("SELECT filename FROM place_photos WHERE place_id=? ORDER BY id", (place_id,))]


def dto(db, p, city, start=None, end=None, origin=None, with_status=True):
    start = start or now()
    end = end or start + timedelta(minutes=30)
    t = db.execute("SELECT name FROM parking_types WHERE id=?", (p["type_id"],)).fetchone()
    d = {
        "id": p["id"], "address": p["address"], "district": p["district"], "lat": p["lat"], "lon": p["lon"],
        "type_id": p["type_id"], "type": t["name"] if t else None, "description": p["description"],
        "entry_rules": p["entry_rules"], "price_per_hour_kop": p["price_per_hour_kop"],
        "schedule": json.loads(p["schedule_json"]), "photos": photos(db, p["id"]),
        "timezone": city["timezone"],
    }
    if with_status:
        d["status"] = place_status(db, p, city, start, end)
    if origin:
        d["distance_m"] = haversine_m(origin[0], origin[1], p["lat"], p["lon"])
    return d
