import json
import logging
import os
import time

import click
from flask import Flask, jsonify, request, send_from_directory
from werkzeug.security import generate_password_hash

from . import db as dbm, jobs
from .config import Config
from .util import ApiError, fmt, now, new_totp_secret

FRONTEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "frontend"))


def _ver(s):
    try:
        return tuple(int(x) for x in s.split("."))
    except ValueError:
        return (0,)


def create_app(overrides=None):
    app = Flask(__name__, static_folder=None)
    app.config.from_object(Config)
    app.config.update(overrides or {})
    logging.basicConfig(level=logging.INFO)
    app.teardown_appcontext(dbm.close_db)

    with dbm.connect(app.config["DATABASE"]) as con:
        dbm.init_db(con)
        _seed_reference(con)

    from .routes import public, bookings, owner, staff
    for m in (public, bookings, owner, staff):
        app.register_blueprint(m.bp)

    @app.before_request
    def check_client_version():
        """Если мобильное приложение слишком старое, просим обновиться (HTTP 426)."""
        v = request.headers.get("X-Client-Version")
        if v and request.path.startswith("/api/") and _ver(v) < _ver(app.config["MIN_CLIENT_VERSION"]):
            raise ApiError("update_required", 426)

    @app.errorhandler(ApiError)
    def api_error(e):
        return jsonify(e.payload()), e.status

    @app.errorhandler(404)
    def nf(_):
        return jsonify(ApiError("not_found").payload()), 404

    @app.errorhandler(405)
    def na(_):
        return jsonify(ApiError("not_found").payload()), 405

    @app.errorhandler(Exception)
    def boom(e):
        app.logger.exception(e)
        return jsonify(ApiError("server_error").payload()), 500

    @app.after_request
    def headers(r):
        r.headers.setdefault("X-Content-Type-Options", "nosniff")
        r.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return r

    # статика: в dev фронтенд раздаёт Flask, на проде - nginx
    @app.get("/media/<path:name>")
    def media(name):
        return send_from_directory(os.path.abspath(app.config["MEDIA_DIR"]), name)

    @app.get("/")
    def index():
        return send_from_directory(FRONTEND, "index.html")

    @app.get("/<path:path>")
    def static_files(path):
        if path.startswith("api/"):
            raise ApiError("not_found", 404)
        return send_from_directory(FRONTEND, path)

    register_cli(app)
    return app


def _seed_reference(con):
    if not con.execute("SELECT 1 FROM cities").fetchone():
        # один город на старте; новые добавляются строкой в таблицу (без переделки)
        con.execute("INSERT INTO cities(name,timezone,lat,lon) VALUES('Москва','Europe/Moscow',55.7558,37.6173)")
    if not con.execute("SELECT 1 FROM parking_types").fetchone():
        con.executemany("INSERT INTO parking_types(name) VALUES(?)",
                        [("Открытая площадка",), ("Крытая",), ("Подземная",), ("Во дворе",), ("Частная территория",)])


def register_cli(app):
    @app.cli.command("create-admin")
    @click.option("--email", required=True)
    @click.option("--password", required=True)
    @click.option("--name", default="Администратор")
    def create_admin(email, password, name):
        """Создать первого администратора. Секрет 2FA печатается один раз."""
        secret = new_totp_secret()
        con = dbm.connect(app.config["DATABASE"])
        con.execute("INSERT INTO staff(name,email,role,password_hash,totp_secret,created_at) VALUES(?,?,?,?,?,?)",
                    (name, email.lower(), "admin", generate_password_hash(password), secret, fmt(now())))
        click.echo(f"Администратор создан. Секрет TOTP (добавьте в приложение-аутентификатор): {secret}")

    @app.cli.command("seed-demo")
    def seed_demo():
        """Демо-данные: владелец и несколько одобренных мест в центре Москвы."""
        con = dbm.connect(app.config["DATABASE"])
        if con.execute("SELECT 1 FROM places").fetchone():
            click.echo("Места уже есть, пропускаю")
            return
        cur = con.execute("INSERT INTO users(name,phone,created_at,consent_at) VALUES('Демо-владелец','+79990000000',?,?)", (fmt(now()), fmt(now())))
        owner = cur.lastrowid
        full = {str(d): [["00:00", "24:00"]] for d in range(7)}
        work = {str(d): [["08:00", "20:00"]] for d in range(5)}
        demo = [
            ("Тверская ул., 7", "Тверской", 55.7570, 37.6137, 1, 25000, full, "Заезд со двора, шлагбаум открывает охрана"),
            ("Петровка ул., 20", "Тверской", 55.7616, 37.6173, 3, 30000, work, "Подземный паркинг, въезд с Петровки"),
            ("Арбат ул., 24", "Арбат", 55.7495, 37.5927, 4, 20000, full, "Во дворе, второй подъезд"),
            ("Лубянский пр., 5", "Мещанский", 55.7589, 37.6293, 2, 35000, work, "Крытая парковка бизнес-центра"),
            ("Новый Арбат ул., 15", "Арбат", 55.7521, 37.5866, 5, 28000, full, "Частная территория, звоните на въезде"),
        ]
        for addr, dist, lat, lon, t, price, sch, rules in demo:
            con.execute("""INSERT INTO places(owner_id,city_id,address,district,lat,lon,type_id,description,entry_rules,
                price_per_hour_kop,schedule_json,status,created_at,updated_at) VALUES(?,1,?,?,?,?,?,?,?,?,?,'approved',?,?)""",
                        (owner, addr, dist, lat, lon, t, "Демо-место", rules, price, json.dumps(sch), fmt(now()), fmt(now())))
        click.echo("Демо-данные добавлены")

    @app.cli.command("jobs")
    @click.option("--loop", is_flag=True, help="Работать постоянно (раз в 30 секунд)")
    def run_jobs(loop):
        """Фоновые задачи: снятие неоплаченных броней, статусы, напоминания, выплаты."""
        while True:
            con = dbm.connect(app.config["DATABASE"])
            click.echo(json.dumps(jobs.run_all(con)))
            con.close()
            if not loop:
                break
            time.sleep(30)
