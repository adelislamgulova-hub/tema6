"""Платёжный провайдер. Карточные данные у нас не появляются: пользователь платит
на странице провайдера (для YooKassa - redirect), мы получаем только статус."""
import base64
import json
import urllib.request
import uuid

from flask import current_app

from .util import ApiError


class MockProvider:
    """Демо-режим без денег: «страница оплаты» живёт во фронтенде (#/pay/<id>)."""
    name = "mock"

    def create(self, payment_id, amount_kop, description, return_url):
        pid = f"mock-{uuid.uuid4().hex[:12]}"
        return pid, f"{current_app.config['PUBLIC_URL']}/#/bookings"

    def verify(self, provider_payment_id):
        return "succeeded"  # в mock подтверждение приходит только с кнопки «Оплатить»

    def refund(self, provider_payment_id, amount_kop, key):
        return f"mock-refund-{uuid.uuid4().hex[:8]}"


class YooKassaProvider:
    """Минимальная интеграция с API ЮKassa v3.
    ВНИМАНИЕ: не проверена на реальном магазине - нужен тестовый shopId/secret."""
    name = "yookassa"
    API = "https://api.yookassa.ru/v3"

    def _call(self, method, path, body=None, key=None):
        c = current_app.config
        auth = base64.b64encode(f"{c['YOOKASSA_SHOP_ID']}:{c['YOOKASSA_SECRET']}".encode()).decode()
        headers = {"Authorization": f"Basic {auth}", "Content-Type": "application/json"}
        if key:
            headers["Idempotence-Key"] = key
        req = urllib.request.Request(self.API + path, method=method, headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)

    @staticmethod
    def _amount(kop):
        return {"value": f"{kop / 100:.2f}", "currency": "RUB"}

    def create(self, payment_id, amount_kop, description, return_url):
        res = self._call("POST", "/payments", {
            "amount": self._amount(amount_kop), "capture": True,
            "confirmation": {"type": "redirect", "return_url": return_url},
            "description": description, "metadata": {"payment_id": payment_id}},
            key=f"pay-{payment_id}")
        return res["id"], res["confirmation"]["confirmation_url"]

    def verify(self, provider_payment_id):
        # статус перепроверяем запросом к API, а не верим телу вебхука
        return self._call("GET", f"/payments/{provider_payment_id}")["status"]

    def refund(self, provider_payment_id, amount_kop, key):
        return self._call("POST", "/refunds", {
            "payment_id": provider_payment_id, "amount": self._amount(amount_kop)}, key=key)["id"]


def provider():
    name = current_app.config["PAYMENT_PROVIDER"]
    if name == "yookassa":
        return YooKassaProvider()
    if name == "mock":
        return MockProvider()
    raise ApiError("server_error", 500)
