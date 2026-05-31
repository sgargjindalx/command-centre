import os

DATABASE_URL = os.environ.get("DATABASE_URL", "")
SECRET_KEY   = os.environ.get("SECRET_KEY", "dev-secret-change-me")
API_KEY      = os.environ.get("API_KEY", "")          # Bearer token for POST /api/log-update
PORT         = int(os.environ.get("PORT", 8000))
IS_PROD      = os.environ.get("RAILWAY_ENVIRONMENT") == "production"
