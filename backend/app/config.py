import os


class Config:
    """Все настройки берутся из переменных окружения (см. .env.example)."""
    DATABASE = os.environ.get("PARKCITY_DB", os.path.join(os.path.dirname(__file__), "..", "parkcity.sqlite3"))
    MEDIA_DIR = os.environ.get("PARKCITY_MEDIA", os.path.join(os.path.dirname(__file__), "..", "media"))
    MAX_UPLOAD_BYTES = 5 * 1024 * 1024

    # Бизнес-правила (BRD, раздел 6)
    COMMISSION_PERCENT = 15
    PAYMENT_TIMEOUT_MIN = 10
    MIN_DURATION_MIN = 30
    STEP_MIN = 30
    MAX_DAYS_AHEAD = 30
    MAX_DURATION_HOURS = 24          # допущение: в требованиях верхней границы нет
    FULL_REFUND_BEFORE_MIN = 60      # отмена за час и раньше -> 100 %
    LATE_REFUND_PERCENT = 50         # позже, но до начала -> 50 %
    REMINDER_BEFORE_MIN = 15
    PERSONAL_DATA_RETENTION_DAYS = 30

    # Вход по коду
    OTP_TTL_SEC = 300
    OTP_RESEND_SEC = 60
    OTP_MAX_ATTEMPTS = 5
    USER_SESSION_DAYS = 30
    STAFF_SESSION_HOURS = 12
    # В dev код возвращается в ответе. В проде обязательно 0.
    DEV_EXPOSE_CODE = os.environ.get("PARKCITY_DEV_EXPOSE_CODE", "1") == "1"

    # Платежи: mock | yookassa
    PAYMENT_PROVIDER = os.environ.get("PARKCITY_PAYMENT_PROVIDER", "mock")
    YOOKASSA_SHOP_ID = os.environ.get("YOOKASSA_SHOP_ID", "")
    YOOKASSA_SECRET = os.environ.get("YOOKASSA_SECRET", "")
    PUBLIC_URL = os.environ.get("PARKCITY_PUBLIC_URL", "http://localhost:5000")

    # Минимальная версия мобильного клиента (заголовок X-Client-Version)
    MIN_CLIENT_VERSION = os.environ.get("PARKCITY_MIN_CLIENT_VERSION", "1.0.0")
    SECRET_KEY = os.environ.get("PARKCITY_SECRET", "dev-only-change-me")
    TESTING = False
