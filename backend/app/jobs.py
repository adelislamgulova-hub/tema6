"""Фоновые задачи (диаграмма use case «фоновые задачи»). Все идемпотентны.
Запуск: `flask --app app jobs` (разово, из cron) или `flask --app app jobs --loop`."""
from datetime import timedelta

from .db import tx
from .notify import notify
from .services import accrued_for_owner
from .util import now, fmt, parse


def expire_unpaid(db):
    n = 0
    for b in db.execute("SELECT id FROM bookings WHERE status='pending_payment' AND expires_at <= ?", (fmt(now()),)).fetchall():
        with tx(db):
            db.execute("UPDATE bookings SET status='expired', expires_at=NULL WHERE id=? AND status='pending_payment'", (b["id"],))
            db.execute("UPDATE payments SET status='canceled' WHERE booking_id=? AND status='pending'", (b["id"],))
        n += 1
    return n


def advance_statuses(db):
    t = fmt(now())
    a = db.execute("UPDATE bookings SET status='active' WHERE status='confirmed' AND start_time <= ? AND end_time > ?", (t, t)).rowcount
    c = db.execute("UPDATE bookings SET status='completed' WHERE status IN ('confirmed','active') AND end_time <= ?", (t,)).rowcount
    return a + c


def send_reminders(db, minutes=15):
    t, soon = now(), fmt(now() + timedelta(minutes=minutes))
    n = 0
    for b in db.execute("SELECT * FROM bookings WHERE status='confirmed' AND reminded_start=0 AND start_time <= ? AND start_time > ?", (soon, fmt(t))).fetchall():
        notify(db, b["user_id"], "reminder_start", f"Аренда по брони №{b['id']} начнётся в течение {minutes} минут")
        db.execute("UPDATE bookings SET reminded_start=1 WHERE id=?", (b["id"],))
        n += 1
    for b in db.execute("SELECT * FROM bookings WHERE status='active' AND reminded_end=0 AND end_time <= ?", (soon,)).fetchall():
        notify(db, b["user_id"], "reminder_end", f"Аренда по брони №{b['id']} закончится в течение {minutes} минут")
        db.execute("UPDATE bookings SET reminded_end=1 WHERE id=?", (b["id"],))
        n += 1
    return n


def weekly_payouts(db, force=False):
    """Раз в неделю (понедельник) формируем выплату каждому владельцу: правило 10."""
    t = now()
    if not force and t.weekday() != 0:
        return 0
    day = t.strftime("%Y-%m-%d")
    created = 0
    owners = db.execute("SELECT DISTINCT owner_id FROM places").fetchall()
    for o in owners:
        with tx(db):
            if db.execute("SELECT 1 FROM payouts WHERE owner_id=? AND substr(period_end,1,10)=?", (o["owner_id"], day)).fetchone():
                continue
            acc = accrued_for_owner(db, o["owner_id"])
            if acc["net"] <= 0:
                continue
            last = db.execute("SELECT MAX(period_end) FROM payouts WHERE owner_id=?", (o["owner_id"],)).fetchone()[0]
            cur = db.execute("INSERT INTO payouts(owner_id,period_start,period_end,amount_kop,commission_kop,created_at) VALUES(?,?,?,?,?,?)",
                             (o["owner_id"], last, fmt(t), acc["net"], acc["commission"], fmt(t)))
            for bid in acc["booking_ids"]:
                db.execute("UPDATE bookings SET payout_id=?, commission_kop=?, owner_amount_kop=? WHERE id=?",
                           (cur.lastrowid, *_split_one(db, bid), bid))
            notify(db, o["owner_id"], "payout", f"Сформирована выплата {acc['net'] / 100:.2f} ₽")
            created += 1
    return created


def _split_one(db, bid):
    from .services import paid_and_refunded
    from .rules import split
    return split(*paid_and_refunded(db, bid))


def purge_deleted_accounts(db):
    """152-ФЗ: личные данные удаляются в течение 30 дней после удаления аккаунта.
    Брони и платежи остаются (хранение 5 лет), но без связи с личностью."""
    from flask import current_app
    cutoff = fmt(now() - timedelta(days=current_app.config["PERSONAL_DATA_RETENTION_DAYS"]))
    n = 0
    for u in db.execute("SELECT id FROM users WHERE deletion_requested_at IS NOT NULL AND purged_at IS NULL AND deletion_requested_at <= ?", (cutoff,)).fetchall():
        with tx(db):
            db.execute("UPDATE users SET name=NULL, phone=NULL, email=NULL, payout_details=NULL, purged_at=? WHERE id=?", (fmt(now()), u["id"]))
            db.execute("DELETE FROM notifications WHERE user_id=?", (u["id"],))
            db.execute("DELETE FROM sessions WHERE kind='user' AND subject_id=?", (u["id"],))
        n += 1
    return n


def cleanup(db):
    db.execute("DELETE FROM sessions WHERE expires_at <= ?", (fmt(now()),))
    db.execute("DELETE FROM otp_codes WHERE expires_at <= ?", (fmt(now() - timedelta(days=1)),))


def run_all(db):
    res = {
        "expired": expire_unpaid(db),
        "advanced": advance_statuses(db),
        "reminders": send_reminders(db),
        "payouts": weekly_payouts(db),
        "purged": purge_deleted_accounts(db),
    }
    cleanup(db)
    return res
