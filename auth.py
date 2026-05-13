import json
import os
import time
import requests
from config import APP_KEY, APP_SECRET, BASE_URL


TOKEN_FILE = os.path.join(os.path.dirname(__file__), "token.json")


def _load_cached_token():
    """저장된 토큰이 유효하면 반환"""
    if not os.path.exists(TOKEN_FILE):
        return None
    with open(TOKEN_FILE, "r") as f:
        data = json.load(f)
    if time.time() < data.get("expires_at", 0):
        return data["access_token"]
    return None


def _save_token(access_token, expires_in):
    """토큰을 파일에 저장 (만료 1시간 전까지 유효하게)"""
    data = {
        "access_token": access_token,
        "expires_at": time.time() + expires_in - 3600,
    }
    with open(TOKEN_FILE, "w") as f:
        json.dump(data, f)


def get_access_token():
    """접근토큰 발급 (캐시 우선, 실패 시 재시도)"""
    cached = _load_cached_token()
    if cached:
        return cached

    url = f"{BASE_URL}/oauth2/tokenP"
    body = {
        "grant_type": "client_credentials",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
    }
    for attempt in range(3):
        try:
            res = requests.post(url, json=body, timeout=10)
            res.raise_for_status()
            data = res.json()
            access_token = data["access_token"]
            expires_in = data.get("expires_in", 86400)
            _save_token(access_token, expires_in)
            print("새 접근토큰 발급 완료")
            return access_token
        except Exception as e:
            print(f"토큰 발급 실패 (시도 {attempt + 1}/3): {e}")
            if attempt < 2:
                time.sleep(5)
    raise RuntimeError("토큰 발급 3회 실패")


def get_headers(tr_id):
    """API 호출용 공통 헤더 생성"""
    return {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {get_access_token()}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": tr_id,
    }
