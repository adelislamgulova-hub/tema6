import sqlite3
from contextlib import contextmanager
from flask import g, current_app

SCHEMA = """
CREATE TABLE IF NOT EXISTS cities(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, timezone TEXT NOT NULL,
  lat REAL NOT NULL, lon REAL NOT NULL, is_active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS parking_types(id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, name TEXT, phone TEXT UNIQUE, email TEXT UNIQUE,
  payout_details TEXT, consent_at TEXT, is_blocked INTEGER NOT NULL DEFAULT 0,
  deletion_requested_at TEXT, purged_at TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS otp_codes(
  id INTEGER PRIMARY KEY, identifier TEXT NOT NULL, code_hash TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0, used INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(
  token_hash TEXT PRIMARY KEY, kind TEXT NOT NULL, subject_id INTEGER NOT NULL,
  expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS staff(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
  role TEXT NOT NULL, password_hash TEXT NOT NULL, totp_secret TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS places(
  id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL REFERENCES users(id),
  city_id INTEGER NOT NULL REFERENCES cities(id),
  address TEXT NOT NULL, district TEXT, lat REAL NOT NULL, lon REAL NOT NULL,
  type_id INTEGER NOT NULL REFERENCES parking_types(id),
  description TEXT, entry_rules TEXT,
  price_per_hour_kop INTEGER NOT NULL, schedule_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',   -- pending | approved | rejected | blocked
  reject_reason TEXT, is_closed INTEGER NOT NULL DEFAULT 0, close_reason TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_places_city_status ON places(city_id, status);
CREATE TABLE IF NOT EXISTS place_photos(
  id INTEGER PRIMARY KEY, place_id INTEGER NOT NULL REFERENCES places(id), filename TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS checks(
  id INTEGER PRIMARY KEY, place_id INTEGER NOT NULL REFERENCES places(id),
  staff_id INTEGER REFERENCES staff(id), result TEXT NOT NULL, comment TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bookings(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
  place_id INTEGER NOT NULL REFERENCES places(id),
  start_time TEXT NOT NULL, end_time TEXT NOT NULL,
  price_per_hour_kop INTEGER NOT NULL, total_price_kop INTEGER NOT NULL,
  status TEXT NOT NULL,  -- pending_payment|confirmed|active|completed|cancelled|expired
  expires_at TEXT, cancelled_by TEXT, cancel_reason TEXT, cancel_date TEXT,
  reminded_start INTEGER NOT NULL DEFAULT 0, reminded_end INTEGER NOT NULL DEFAULT 0,
  commission_kop INTEGER, owner_amount_kop INTEGER, payout_id INTEGER,
  created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_bookings_place ON bookings(place_id, start_time, end_time);
CREATE INDEX IF NOT EXISTS ix_bookings_user ON bookings(user_id);
CREATE TABLE IF NOT EXISTS payments(
  id INTEGER PRIMARY KEY, booking_id INTEGER NOT NULL REFERENCES bookings(id),
  amount_kop INTEGER NOT NULL, status TEXT NOT NULL,  -- pending|succeeded|canceled
  provider TEXT NOT NULL, provider_payment_id TEXT, confirmation_url TEXT,
  method TEXT, created_at TEXT NOT NULL, paid_at TEXT);
CREATE TABLE IF NOT EXISTS refunds(
  id INTEGER PRIMARY KEY, payment_id INTEGER NOT NULL REFERENCES payments(id),
  booking_id INTEGER NOT NULL REFERENCES bookings(id),
  amount_kop INTEGER NOT NULL, percent INTEGER, is_manual INTEGER NOT NULL DEFAULT 0,
  reason TEXT, staff_id INTEGER, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS receipts(
  id INTEGER PRIMARY KEY, booking_id INTEGER NOT NULL REFERENCES bookings(id),
  kind TEXT NOT NULL,  -- payment | refund
  number TEXT NOT NULL, amount_kop INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS complaints(
  id INTEGER PRIMARY KEY, booking_id INTEGER NOT NULL REFERENCES bookings(id),
  user_id INTEGER NOT NULL REFERENCES users(id), place_id INTEGER NOT NULL,
  kind TEXT NOT NULL, description TEXT, status TEXT NOT NULL DEFAULT 'open',
  resolution TEXT, handled_by INTEGER, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS payouts(
  id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL REFERENCES users(id),
  period_start TEXT, period_end TEXT NOT NULL, amount_kop INTEGER NOT NULL,
  commission_kop INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'created', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS action_log(
  id INTEGER PRIMARY KEY, staff_id INTEGER, action TEXT NOT NULL, target TEXT,
  details TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notifications(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, kind TEXT NOT NULL,
  text TEXT NOT NULL, is_read INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
"""


def connect(path):
    # isolation_level=None: транзакциями управляем сами (см. tx)
    con = sqlite3.connect(path, isolation_level=None, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    return con


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(con):
    con.executescript(SCHEMA)


@contextmanager
def tx(db):
    """BEGIN IMMEDIATE даёт защиту от гонок: два запроса не забронируют одно место."""
    db.execute("BEGIN IMMEDIATE")
    try:
        yield db
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise


def rows(cur):
    return [dict(r) for r in cur.fetchall()]
