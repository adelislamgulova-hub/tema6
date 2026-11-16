"""Время, деньги, ошибки, TOTP и прочие мелочи."""
import base64
import hashlib
import hmac
import json
import os
import struct
import time as _time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

ISO = "%Y-%m-%dT%H:%M:%SZ"
_MESSAGES = json.load(open(os.path.join(os.path.dirname(__file__), "messages_ru.json"), encoding="utf-8"))

_clock_override = None


def now() -> datetime:
    return _clock_override() if _clock_override else datetime.now(timezone.utc).replace(microsecond=0)


def set_clock(fn):
    """Для тестов: подмена текущего времени."""
    global _clock_override
    _clock_override = fn


def fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(ISO)


def parse(s: str) -> datetime:
    """Принимает ISO-строку с Z или смещением. Без смещения считаем UTC."""
    if not isinstance(s, str):
        raise ApiError("validation", field="datetime")
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        raise ApiError("validation", field="datetime")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def tz(name: str) -> ZoneInfo:
    return ZoneInfo(name)


class ApiError(Exception):
    def __init__(self, code, status=400, **extra):
        self.code, self.status, self.extra = code, status, extra

    def payload(self):
        return {"error": {"code": self.code, "message": _MESSAGES.get(self.code, self.code), **self.extra}}


def pct(amount_kop: int, percent: int) -> int:
    """Процент от суммы в копейках с округлением half-up."""
    return int((Decimal(amount_kop) * percent / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def price_for(price_per_hour_kop: int, minutes: int) -> int:
    return int((Decimal(price_per_hour_kop) * minutes / 60).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def new_token() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")


# --- TOTP (RFC 6238) для второго фактора сотрудников ---
def new_totp_secret() -> str:
    return base64.b32encode(os.urandom(10)).decode()


def totp(secret: str, at: float = None, step=30) -> str:
    key = base64.b32decode(secret)
    counter = int((at if at is not None else _time.time()) // step)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[o:o + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"


def totp_ok(secret: str, code: str, at: float = None) -> bool:
    t = at if at is not None else _time.time()
    return any(hmac.compare_digest(totp(secret, t + d * 30), str(code)) for d in (-1, 0, 1))


def week_start_utc(d: datetime) -> datetime:
    return (d - timedelta(days=d.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
