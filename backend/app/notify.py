"""Уведомления: пишем в БД (лента в приложении) и отправляем во внешние каналы.
Сейчас каналы - заглушки с логом; на проде здесь подключаются SMS-шлюз, SMTP и push."""
import logging
from .util import now, fmt

log = logging.getLogger("parkcity.notify")


def send_code(identifier: str, code: str):
    channel = "email" if "@" in identifier else "sms"
    log.info("[%s] код входа для %s: %s", channel, identifier, code)


def notify(db, user_id: int, kind: str, text: str):
    db.execute("INSERT INTO notifications(user_id,kind,text,created_at) VALUES(?,?,?,?)",
               (user_id, kind, text, fmt(now())))
    log.info("[push] user=%s %s: %s", user_id, kind, text)
