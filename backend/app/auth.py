"""Сессии и проверка прав. Пользователи входят по коду, сотрудники - пароль + TOTP."""
from datetime import timedelta
from functools import wraps

from flask import g, request, current_app

from .db import get_db
from .util import ApiError, now, fmt, parse, sha, new_token

# Матрица прав (BRD, раздел 10) для сотрудников
PERMISSIONS = {
    "admin":     {"moderate", "all_bookings", "complaints", "stats", "staff", "users"},
    "manager":   {"moderate", "all_bookings"},
    "support":   {"all_bookings", "complaints"},
    "finance":   {"all_bookings", "refunds", "stats"},
    "marketer":  {"stats"},
}


def create_session(db, kind, subject_id, ttl: timedelta) -> str:
    tok = new_token()
    db.execute("INSERT INTO sessions(token_hash,kind,subject_id,expires_at) VALUES(?,?,?,?)",
               (sha(tok), kind, subject_id, fmt(now() + ttl)))
    return tok


def _session(kind):
    h = request.headers.get("Authorization", "")
    if not h.startswith("Bearer "):
        return None
    s = get_db().execute("SELECT * FROM sessions WHERE token_hash=? AND kind=?", (sha(h[7:]), kind)).fetchone()
    if not s or parse(s["expires_at"]) <= now():
        return None
    return s


def current_user(optional=False):
    s = _session("user")
    if s:
        u = get_db().execute("SELECT * FROM users WHERE id=?", (s["subject_id"],)).fetchone()
        if u and u["is_blocked"]:
            raise ApiError("account_blocked", 403)
        if u and not u["purged_at"]:
            return dict(u)
    if optional:
        return None
    raise ApiError("unauthorized", 401)


def user_required(f):
    @wraps(f)
    def w(*a, **k):
        g.user = current_user()
        return f(*a, **k)
    return w


def staff_required(perm=None):
    def deco(f):
        @wraps(f)
        def w(*a, **k):
            s = _session("staff")
            st = s and get_db().execute("SELECT * FROM staff WHERE id=? AND is_active=1", (s["subject_id"],)).fetchone()
            if not st:
                raise ApiError("unauthorized", 401)
            if perm and perm not in PERMISSIONS.get(st["role"], set()):
                raise ApiError("forbidden", 403)
            g.staff = dict(st)
            return f(*a, **k)
        return w
    return deco


def log_action(action, target=None, details=None):
    """Журнал важных действий сотрудников (требование безопасности)."""
    get_db().execute("INSERT INTO action_log(staff_id,action,target,details,created_at) VALUES(?,?,?,?,?)",
                     (g.staff["id"], action, target, details, fmt(now())))


def json_body():
    d = request.get_json(silent=True)
    if not isinstance(d, dict):
        raise ApiError("validation")
    return d
