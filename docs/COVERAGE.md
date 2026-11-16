# Соответствие требованиям

Источники: `docs/analytics/Аналитика_тема_6.pdf` (BRD&SRS) и `Задание2_Тема6.pptx` (use case, классы, компоненты, развёртывание).

## Бизнес-правила (раздел 6 BRD)

| № | Правило | Где реализовано | Тест |
|---|---------|-----------------|------|
| 1 | Одно место - одному на одно время | `rules.overlapping`, транзакция `BEGIN IMMEDIATE` в `services.create_booking` | `test_no_double_booking_...` |
| 2 | Бронь подтверждается после оплаты | `services.mark_paid` | `test_price_is_computed_and_booking_needs_payment` |
| 3 | 10 минут на оплату | `expires_at`, `jobs.expire_unpaid`, возврат при поздней оплате | `test_late_payment_is_refunded` |
| 4 | От 30 мин, шаг 30, до 30 дней вперёд | `rules.check_interval` | `test_half_hour_step_rules` |
| 5 | Цена фиксируется при брони | `bookings.price_per_hour_kop`, `total_price_kop` | там же |
| 6 | Возврат 100 / 50 / 0 % | `rules.refund_percent` | `test_refund_tiers` |
| 7 | Место на карте только после проверки | `places.status`, фильтр `approved` | `test_new_place_hidden_until_approved` |
| 8 | Закрытие места не снимает оплаченные брони | `owner.close_place` | `test_owner_closing_keeps_paid_bookings` |
| 9 | Отмена владельцем/сервисом - 100 % | `refund_percent(by=owner/service)` | `test_owner_cancel_returns_everything_...` |
| 10 | Комиссия 15 %, выплаты раз в неделю | `rules.split`, `jobs.weekly_payouts` (понедельник) | `test_lifecycle_complaint_payout_and_commission` |

## Use case → API

| Диаграмма | Реализация |
|-----------|-----------|
| 1.1 Гость | `GET /api/places`, `/places/<id>`, `/places/<id>/quote`, `POST /auth/request-code`, `/auth/verify` |
| 1.2 Первая бронь | `POST /api/bookings`, `POST /payments/<id>/mock-pay` и `/payments/webhook`, `POST /bookings/<id>/cancel` |
| 1.3 Управление бронями | `GET /bookings`, `PATCH /bookings/<id>`, `GET /bookings/<id>/receipts`, `POST /bookings/<id>/complaint`, `DELETE /me` |
| 1.4 Владелец | `/api/owner/places*`, `/owner/bookings*`, `/owner/earnings`, `PATCH /me` (реквизиты) |
| 1.5 Сотрудники | `/api/staff/*` (матрица прав - `auth.PERMISSIONS`, тест `test_permission_matrix`) |
| 1.6 Фоновые задачи | `jobs.py`: снятие неоплаченных, смена статусов, напоминания, недельные выплаты, чеки, удаление ПДн |

## Нефункциональные требования

| Требование | Статус |
|------------|--------|
| Вход по коду без пароля; сотрудники - пароль + TOTP | Сделано |
| Журнал действий сотрудников | Сделано (`action_log`) |
| Карточные данные не храним, оплата на стороне провайдера | Сделано (redirect на ЮKassa) |
| Новые города без переделки | Сделано (таблица `cities`, часовой пояс города) |
| Тексты отдельно от кода | Сделано (`frontend/i18n/ru.json`, `backend/app/messages_ru.json`) |
| Устаревшая версия клиента просит обновиться | Сделано (заголовок `X-Client-Version`, HTTP 426) |
| Хранение броней и платежей 5 лет, ПДн удаляются за 30 дней | Сделано (`purge_deleted_accounts`) |
| Резервная копия ежедневно | Скрипт `backend/scripts/backup.py` + cron |
| Только HTTPS | Конфиг nginx в `deploy/` |

## Допущения (в BRD не определено)

- Максимальная длительность брони 24 часа (`MAX_DURATION_HOURS`).
- Перенос брони: только с той же длительностью, не позже чем за час до начала (иначе пришлось бы доплачивать или возвращать разницу).
- Правка адреса, точки, типа или описания одобренного места отправляет его на повторную проверку; цена и расписание - нет.
- Владелец не может бронировать собственное место.
- Выплата формируется записью в `payouts` (статус `created`); сам банковский перевод делает финансист вне системы.
