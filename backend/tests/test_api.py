import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from werkzeug.security import generate_password_hash

from app import create_app, jobs, db as dbm
from app.util import fmt, set_clock, new_totp_secret, totp

# Вторник 2026-12-01 09:00 по Москве = 06:00 UTC
T0 = datetime(2026, 12, 1, 6, 0, tzinfo=timezone.utc)
FULL = {str(d): [["00:00", "24:00"]] for d in range(7)}


class Base(unittest.TestCase):
    def setUp(self):
        self.now = T0
        set_clock(lambda: self.now)
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.app = create_app({"DATABASE": self.path, "MEDIA_DIR": tempfile.mkdtemp(), "TESTING": True})
        self.c = self.app.test_client()
        self.con = dbm.connect(self.path)

    def tearDown(self):
        set_clock(None)
        self.con.close()
        os.unlink(self.path)

    # --- помощники ---
    def login(self, ident):
        code = self.c.post("/api/auth/request-code", json={"identifier": ident}).json["dev_code"]
        r = self.c.post("/api/auth/verify", json={"identifier": ident, "code": code}).json
        return {"Authorization": "Bearer " + r["token"]}

    def mk_place(self, owner_h, price=30000, schedule=FULL, approve=True):
        r = self.c.post("/api/owner/places", headers=owner_h, json={
            "address": "Тверская, 1", "lat": 55.76, "lon": 37.61, "type_id": 1,
            "price_per_hour_kop": price, "schedule": schedule})
        self.assertEqual(r.status_code, 201, r.json)
        pid = r.json["id"]
        if approve:
            self.con.execute("UPDATE places SET status='approved' WHERE id=?", (pid,))
        return pid

    def book(self, h, pid, start_h=2, hours=1):
        s = T0 + timedelta(hours=start_h)
        return self.c.post("/api/bookings", headers=h, json={
            "place_id": pid, "start": fmt(s), "end": fmt(s + timedelta(hours=hours)), "consent": True})

    def pay(self, h, booking):
        return self.c.post(f"/api/payments/{booking['payment_id']}/mock-pay", headers=h)

    def job(self, fn, *a):
        with self.app.app_context():
            return fn(self.con, *a)

    def staff(self, role):
        secret = new_totp_secret()
        self.con.execute("INSERT INTO staff(name,email,role,password_hash,totp_secret,created_at) VALUES(?,?,?,?,?,?)",
                         (role, f"{role}@x.ru", role, generate_password_hash("pw"), secret, fmt(T0)))
        r = self.c.post("/api/staff/login", json={"email": f"{role}@x.ru", "password": "pw", "code": totp(secret)})
        self.assertEqual(r.status_code, 200, r.json)
        return {"Authorization": "Bearer " + r.json["token"]}


class AuthTests(Base):
    def test_first_login_creates_account_and_second_reuses(self):
        h1 = self.login("+7 (999) 111-22-33")
        self.now = T0 + timedelta(minutes=5)  # лимит повторной отправки кода: 60 с
        h2 = self.login("89991112233")
        self.assertEqual(self.c.get("/api/me", headers=h1).json["id"], self.c.get("/api/me", headers=h2).json["id"])

    def test_wrong_code_and_resend_limit(self):
        self.c.post("/api/auth/request-code", json={"identifier": "a@b.ru"})
        self.assertEqual(self.c.post("/api/auth/verify", json={"identifier": "a@b.ru", "code": "000000"}).json["error"]["code"], "invalid_code")
        self.assertEqual(self.c.post("/api/auth/request-code", json={"identifier": "a@b.ru"}).status_code, 429)

    def test_guest_can_browse_but_not_book(self):
        self.assertEqual(self.c.get("/api/places").status_code, 200)
        self.assertEqual(self.c.post("/api/bookings", json={}).status_code, 401)

    def test_old_client_must_update(self):
        r = self.c.get("/api/config", headers={"X-Client-Version": "0.9.0"})
        self.assertEqual(r.status_code, 426)


class PlaceTests(Base):
    def test_new_place_hidden_until_approved(self):
        owner = self.login("+79990000001")
        pid = self.mk_place(owner, approve=False)
        self.assertEqual(self.c.get("/api/places").json, [])
        staff = self.staff("manager")
        self.assertEqual(self.c.post(f"/api/staff/places/{pid}/reject", headers=staff, json={}).status_code, 400)  # причина обязательна
        self.assertEqual(self.c.post(f"/api/staff/places/{pid}/approve", headers=staff, json={}).status_code, 200)
        res = self.c.get("/api/places").json
        self.assertEqual([p["id"] for p in res], [pid])
        self.assertEqual(res[0]["status"], "free")

    def test_status_colors_and_filters(self):
        owner, drv = self.login("+79990000001"), self.login("+79990000002")
        pid = self.mk_place(owner, price=30000)
        b = self.book(drv, pid).json
        s, e = fmt(T0 + timedelta(hours=2)), fmt(T0 + timedelta(hours=3))
        self.assertEqual(self.c.get(f"/api/places?from={s}&to={e}").json[0]["status"], "booked")
        self.assertEqual(self.c.get(f"/api/places?from={fmt(T0+timedelta(hours=5))}&to={fmt(T0+timedelta(hours=6))}").json[0]["status"], "free")
        self.assertEqual(self.c.get("/api/places?max_price=200").json, [])
        self.assertEqual(len(self.c.get("/api/places?q=тверск").json), 1)  # регистр и кириллица
        self.assertEqual(self.c.get("/api/places?type_id=2").json, [])

    def test_closed_and_outside_schedule_unavailable(self):
        owner, drv = self.login("+79990000001"), self.login("+79990000002")
        pid = self.mk_place(owner, schedule={"1": [["08:00", "12:00"]]})  # вторник 08-12 МСК
        # 09:00-10:00 МСК (06:00 UTC) внутри окна, 14:00 МСК - вне
        self.assertEqual(self.book(drv, pid, start_h=1).status_code, 201)
        self.assertEqual(self.book(drv, pid, start_h=6).status_code, 409)
        self.c.post(f"/api/owner/places/{pid}/close", headers=owner, json={"reason": "ремонт"})
        self.assertEqual(self.book(drv, pid, start_h=2).status_code, 409)

    def test_photo_content_is_checked(self):
        import io
        owner = self.login("+79990000001")
        pid = self.mk_place(owner)
        bad = self.c.post(f"/api/owner/places/{pid}/photos", headers=owner, data={"photo": (io.BytesIO(b"<script>"), "a.jpg")})
        self.assertEqual(bad.status_code, 400)
        ok = self.c.post(f"/api/owner/places/{pid}/photos", headers=owner, data={"photo": (io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"0" * 20), "a.png")})
        self.assertEqual(ok.status_code, 201)

    def test_address_change_needs_recheck_price_does_not(self):
        owner = self.login("+79990000001")
        pid = self.mk_place(owner)
        self.c.patch(f"/api/owner/places/{pid}", headers=owner, json={"price_per_hour_kop": 40000})
        self.assertEqual(self.c.get(f"/api/owner/places/{pid}", headers=owner).json["moderation_status"], "approved")
        self.c.patch(f"/api/owner/places/{pid}", headers=owner, json={"address": "Другой адрес"})
        self.assertEqual(self.c.get(f"/api/owner/places/{pid}", headers=owner).json["moderation_status"], "pending")


class BookingTests(Base):
    def setUp(self):
        super().setUp()
        self.owner, self.drv = self.login("+79990000001"), self.login("+79990000002")
        self.pid = self.mk_place(self.owner, price=30000)

    def test_price_is_computed_and_booking_needs_payment(self):
        b = self.book(self.drv, self.pid, hours=1).json
        self.assertEqual(b["total_price_kop"], 30000)
        self.assertEqual(b["status"], "pending_payment")
        self.assertEqual(self.pay(self.drv, b).json["status"], "confirmed")
        self.assertEqual(len(self.c.get(f"/api/bookings/{b['id']}/receipts", headers=self.drv).json), 1)

    def test_half_hour_step_rules(self):
        s = T0 + timedelta(hours=2)
        bad = self.c.post("/api/bookings", headers=self.drv, json={"place_id": self.pid, "consent": True,
                          "start": fmt(s), "end": fmt(s + timedelta(minutes=20))})
        self.assertEqual(bad.json["error"]["code"], "bad_interval")
        ok = self.c.post("/api/bookings", headers=self.drv, json={"place_id": self.pid, "consent": True,
                         "start": fmt(s), "end": fmt(s + timedelta(minutes=30))})
        self.assertEqual((ok.status_code, ok.json["total_price_kop"]), (201, 15000))
        far = self.book(self.drv, self.pid, start_h=24 * 31)
        self.assertEqual(far.json["error"]["code"], "too_far")
        self.assertEqual(self.book(self.drv, self.pid, start_h=-3).json["error"]["code"], "in_past")

    def test_no_double_booking_and_unpaid_hold_expires_in_10_min(self):
        other = self.login("+79990000003")
        a = self.book(self.drv, self.pid).json
        self.assertEqual(self.book(other, self.pid).status_code, 409)       # правило 1
        self.now = T0 + timedelta(minutes=11)                                 # правило 3
        self.assertEqual(self.book(other, self.pid).status_code, 201)
        self.job(jobs.expire_unpaid)
        self.assertEqual(self.c.get(f"/api/bookings/{a['id']}", headers=self.drv).json["status"], "expired")

    def test_late_payment_is_refunded(self):
        a = self.book(self.drv, self.pid).json
        self.now = T0 + timedelta(minutes=11)
        self.job(jobs.expire_unpaid)
        self.pay(self.drv, a)
        r = self.c.get(f"/api/bookings/{a['id']}", headers=self.drv).json
        self.assertEqual((r["status"], r["paid_kop"], r["refunded_kop"]), ("expired", 30000, 30000))

    def test_cannot_book_own_place(self):
        self.assertEqual(self.book(self.owner, self.pid).status_code, 409)

    def test_refund_tiers(self):
        # >=1 час до начала -> 100 %
        b = self.book(self.drv, self.pid, start_h=3).json
        self.pay(self.drv, b)
        self.assertEqual(self.c.post(f"/api/bookings/{b['id']}/cancel", headers=self.drv).json["refunded_kop"], 30000)
        # меньше часа -> 50 %
        b = self.book(self.drv, self.pid, start_h=3).json
        self.pay(self.drv, b)
        self.now = T0 + timedelta(hours=2, minutes=30)
        self.assertEqual(self.c.get(f"/api/bookings/{b['id']}", headers=self.drv).json["cancel_preview"]["percent"], 50)
        self.assertEqual(self.c.post(f"/api/bookings/{b['id']}/cancel", headers=self.drv).json["refunded_now_kop"], 15000)
        # после начала -> 0 %
        self.now = T0
        b = self.book(self.drv, self.pid, start_h=5).json
        self.pay(self.drv, b)
        self.now = T0 + timedelta(hours=5, minutes=10)
        self.assertEqual(self.c.post(f"/api/bookings/{b['id']}/cancel", headers=self.drv).json["refunded_now_kop"], 0)

    def test_unpaid_cancel_has_no_refund(self):
        b = self.book(self.drv, self.pid).json
        r = self.c.post(f"/api/bookings/{b['id']}/cancel", headers=self.drv).json
        self.assertEqual((r["status"], r["refunded_now_kop"]), ("cancelled", 0))
        self.assertEqual(self.book(self.login("+79990000003"), self.pid).status_code, 201)  # место освободилось

    def test_owner_cancel_returns_everything_and_needs_reason(self):
        b = self.book(self.drv, self.pid, start_h=2).json
        self.pay(self.drv, b)
        self.now = T0 + timedelta(hours=1, minutes=50)  # поздно для водителя, но владелец возвращает 100 %
        self.assertEqual(self.c.post(f"/api/owner/bookings/{b['id']}/cancel", headers=self.owner, json={}).status_code, 400)
        r = self.c.post(f"/api/owner/bookings/{b['id']}/cancel", headers=self.owner, json={"reason": "авария"}).json
        self.assertEqual((r["status"], r["refunded_kop"]), ("cancelled", 30000))
        self.assertTrue(any("отменил" in n["text"] for n in self.c.get("/api/notifications", headers=self.drv).json))

    def test_owner_closing_keeps_paid_bookings(self):
        b = self.book(self.drv, self.pid).json
        self.pay(self.drv, b)
        self.c.post(f"/api/owner/places/{self.pid}/close", headers=self.owner, json={"reason": "ремонт"})
        self.assertEqual(self.c.get(f"/api/bookings/{b['id']}", headers=self.drv).json["status"], "confirmed")

    def test_shift_booking(self):
        b = self.book(self.drv, self.pid, start_h=4).json
        self.pay(self.drv, b)
        new = fmt(T0 + timedelta(hours=6))
        r = self.c.patch(f"/api/bookings/{b['id']}", headers=self.drv, json={"start": new})
        self.assertEqual((r.status_code, r.json["start"]), (200, new))
        third = self.login("+79990000003")
        other = self.book(third, self.pid, start_h=8).json
        self.pay(third, other)
        clash = self.c.patch(f"/api/bookings/{b['id']}", headers=self.drv, json={"start": fmt(T0 + timedelta(hours=8))})
        self.assertEqual(clash.status_code, 409)

    def test_consent_required_first_time(self):
        s = T0 + timedelta(hours=2)
        r = self.c.post("/api/bookings", headers=self.drv, json={"place_id": self.pid, "start": fmt(s), "end": fmt(s + timedelta(hours=1))})
        self.assertEqual(r.json["error"]["code"], "consent_required")

    def test_lifecycle_complaint_payout_and_commission(self):
        b = self.book(self.drv, self.pid, start_h=1, hours=2).json   # 2 ч * 300 = 600 ₽
        self.pay(self.drv, b)
        self.now = T0 + timedelta(minutes=50)
        self.job(jobs.send_reminders)
        self.assertTrue(any(n["kind"] == "reminder_start" for n in self.c.get("/api/notifications", headers=self.drv).json))
        self.now = T0 + timedelta(hours=1, minutes=10)
        self.job(jobs.advance_statuses)
        self.assertEqual(self.c.get(f"/api/bookings/{b['id']}", headers=self.drv).json["status"], "active")
        self.now = T0 + timedelta(hours=4)
        self.job(jobs.advance_statuses)
        self.assertEqual(self.c.get(f"/api/bookings/{b['id']}", headers=self.drv).json["status"], "completed")
        self.assertEqual(self.c.post(f"/api/bookings/{b['id']}/complaint", headers=self.drv, json={"kind": "occupied", "description": "чужая машина"}).status_code, 201)
        # начисление: 60000 - 15 % = 51000
        e = self.c.get("/api/owner/earnings", headers=self.owner).json
        self.assertEqual((e["accrued_gross_kop"], e["accrued_commission_kop"], e["accrued_net_kop"]), (60000, 9000, 51000))
        self.now = datetime(2026, 12, 6, 12, 0, tzinfo=timezone.utc)  # воскресенье - выплат нет
        self.assertEqual(self.job(jobs.weekly_payouts), 0)
        self.now = datetime(2026, 12, 7, 12, 0, tzinfo=timezone.utc)  # понедельник
        self.assertEqual(self.job(jobs.weekly_payouts), 1)
        self.assertEqual(self.job(jobs.weekly_payouts), 0)  # повторный запуск ничего не дублирует
        e = self.c.get("/api/owner/earnings", headers=self.owner).json
        self.assertEqual((e["accrued_net_kop"], e["payouts"][0]["amount_kop"]), (0, 51000))

    def test_partial_refund_commission_only_on_kept_amount(self):
        b = self.book(self.drv, self.pid, start_h=3).json
        self.pay(self.drv, b)
        self.now = T0 + timedelta(hours=2, minutes=30)
        self.c.post(f"/api/bookings/{b['id']}/cancel", headers=self.drv)      # возврат 50 %: 15000 осталось
        e = self.c.get("/api/owner/earnings", headers=self.owner).json
        self.assertEqual((e["accrued_gross_kop"], e["accrued_commission_kop"], e["accrued_net_kop"]), (15000, 2250, 12750))

    def test_account_deletion_purges_personal_data_after_30_days(self):
        drv = self.login("+79990000009")
        self.assertEqual(self.c.delete("/api/me", headers=drv).status_code, 200)
        self.now = T0 + timedelta(days=31)
        self.assertEqual(self.job(jobs.purge_deleted_accounts), 1)
        u = self.con.execute("SELECT phone,name FROM users WHERE purged_at IS NOT NULL").fetchone()
        self.assertEqual((u["phone"], u["name"]), (None, None))

    def test_cannot_delete_account_with_active_booking(self):
        self.pay(self.drv, self.book(self.drv, self.pid).json)
        self.assertEqual(self.c.delete("/api/me", headers=self.drv).json["error"]["code"], "has_active_bookings")


class StaffTests(Base):
    def test_login_needs_password_and_totp(self):
        secret = new_totp_secret()
        self.con.execute("INSERT INTO staff(name,email,role,password_hash,totp_secret,created_at) VALUES('a','a@x.ru','admin',?,?,?)",
                         (generate_password_hash("pw"), secret, fmt(T0)))
        bad = self.c.post("/api/staff/login", json={"email": "a@x.ru", "password": "pw", "code": "000000"})
        self.assertEqual(bad.status_code, 401)
        ok = self.c.post("/api/staff/login", json={"email": "a@x.ru", "password": "pw", "code": totp(secret)})
        self.assertEqual(ok.status_code, 200)

    def test_permission_matrix(self):
        expect = {  # роль -> (проверка мест, все брони, жалобы, возвраты, статистика, сотрудники)
            "admin":    (200, 200, 200, 403, 200, 200),
            "manager":  (200, 200, 403, 403, 403, 403),
            "support":  (403, 200, 200, 403, 403, 403),
            "finance":  (403, 200, 403, 200, 200, 403),
            "marketer": (403, 403, 403, 403, 200, 403),
        }
        urls = ["/api/staff/places", "/api/staff/bookings", "/api/staff/complaints", "/api/staff/payments", "/api/staff/stats", "/api/staff/employees"]
        for role, codes in expect.items():
            h = self.staff(role)
            self.assertEqual(tuple(self.c.get(u, headers=h).status_code for u in urls), codes, role)
        self.assertEqual(self.c.get("/api/staff/stats").status_code, 401)
        # обычный пользователь не может зайти в админку своим токеном
        self.assertEqual(self.c.get("/api/staff/stats", headers=self.login("+79990000001")).status_code, 401)

    def test_manual_refund_by_finance_is_logged(self):
        owner, drv = self.login("+79990000001"), self.login("+79990000002")
        pid = self.mk_place(owner)
        b = self.book(drv, pid).json
        self.pay(drv, b)
        fin, adm = self.staff("finance"), self.staff("admin")
        self.assertEqual(self.c.post(f"/api/staff/bookings/{b['id']}/refund", headers=fin, json={"amount_kop": 99999, "reason": "x"}).status_code, 409)
        self.assertEqual(self.c.post(f"/api/staff/bookings/{b['id']}/refund", headers=fin, json={"amount_kop": 10000, "reason": "жалоба"}).status_code, 200)
        log = self.c.get("/api/staff/log", headers=adm).json
        self.assertEqual(log[0]["action"], "refund.manual")
        self.assertEqual(self.c.get("/api/staff/stats", headers=fin).json["revenue_gross_kop"], 20000)

    def test_blocking_user_kills_session(self):
        drv = self.login("+79990000002")
        uid = self.c.get("/api/me", headers=drv).json["id"]
        adm = self.staff("admin")
        self.c.post(f"/api/staff/users/{uid}/block", headers=adm, json={"reason": "нарушение"})
        self.assertEqual(self.c.get("/api/me", headers=drv).status_code, 401)
        self.now = T0 + timedelta(minutes=5)  # прошёл лимит повторной отправки кода
        code = self.c.post("/api/auth/request-code", json={"identifier": "+79990000002"}).json["dev_code"]
        r = self.c.post("/api/auth/verify", json={"identifier": "+79990000002", "code": code})
        self.assertEqual((r.status_code, r.json["error"]["code"]), (403, "account_blocked"))

    def test_admin_cannot_demote_self(self):
        adm = self.staff("admin")
        me = self.c.get("/api/staff/me", headers=adm).json["id"]
        self.assertEqual(self.c.patch(f"/api/staff/employees/{me}", headers=adm, json={"is_active": False}).status_code, 403)


if __name__ == "__main__":
    unittest.main()
