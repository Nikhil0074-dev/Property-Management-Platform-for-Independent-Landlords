import os


class Settings:
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./pm.db")
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
    WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "whsec_dev")
    TOKEN_MINUTES = int(os.getenv("TOKEN_MINUTES", "480"))
    UPLOAD_DIR = os.getenv("UPLOAD_DIR", "./uploads")
    MAX_UPLOAD = 5 * 1024 * 1024
    # DEBUG enables mock payment simulation and returns reset/temp tokens in API responses.
    DEBUG = os.getenv("DEBUG", "true").lower() == "true"
    CORS_ORIGINS = [o for o in os.getenv("CORS_ORIGINS", "").split(",") if o]


settings = Settings()
