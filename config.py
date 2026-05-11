import os
from dotenv import load_dotenv

load_dotenv()

MODE = os.getenv("MODE", "mock")

if MODE == "real":
    APP_KEY = os.getenv("APP_KEY")
    APP_SECRET = os.getenv("APP_SECRET")
    ACCOUNT_NO = os.getenv("ACCOUNT_NO")
    BASE_URL = "https://openapi.koreainvestment.com:9443"
else:
    APP_KEY = os.getenv("MOCK_APP_KEY")
    APP_SECRET = os.getenv("MOCK_APP_SECRET")
    ACCOUNT_NO = os.getenv("MOCK_ACCOUNT_NO")
    BASE_URL = "https://openapivts.koreainvestment.com:29443"
