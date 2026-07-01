"""ML 섀도우 예측 (순수 numpy, sklearn 불필요).
buy_model.json(가중치)을 읽고, 한투 API로 최근 일봉을 받아 '오늘 매수 확률'을 계산.
서버(e2-micro)에서 numpy만으로 동작. 실제 주문은 내지 않음(섀도우).
"""
import json
import os
from datetime import datetime, timedelta

import numpy as np
import requests

from config import BASE_URL
from auth import get_headers

_MODEL_FILE = os.path.join(os.path.dirname(__file__), "buy_model.json")


def load_model():
    with open(_MODEL_FILE, "r") as f:
        return json.load(f)


def fetch_recent_daily(code, count=40):
    """최근 일봉을 받아 (closes, vols) 반환 (오래된→최신 순). numpy 배열."""
    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
    end = datetime.now()
    start = end - timedelta(days=count * 2 + 20)   # 영업일 여유있게
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": code,
        "FID_INPUT_DATE_1": start.strftime("%Y%m%d"),
        "FID_INPUT_DATE_2": end.strftime("%Y%m%d"),
        "FID_PERIOD_DIV_CODE": "D",
        "FID_ORG_ADJ_PRC": "0",
    }
    headers = get_headers("FHKST03010100")
    res = requests.get(url, headers=headers, params=params, timeout=10)
    res.raise_for_status()
    data = res.json()
    if data["rt_cd"] != "0":
        raise RuntimeError(data.get("msg1"))
    rows = [d for d in data.get("output2", []) if d.get("stck_bsop_date")]
    rows.sort(key=lambda d: d["stck_bsop_date"])   # 오래된 → 최신
    closes = np.array([float(d["stck_clpr"]) for d in rows])
    vols = np.array([float(d["acml_vol"]) for d in rows])
    return closes, vols


def compute_features(closes, vols):
    """make_features.py와 '동일하게' 6개 피처를 오늘(마지막 행)에 대해 계산.
    주의: vol_5d는 pandas .std()와 맞추려고 ddof=1 (표본표준편차) 사용."""
    c = closes
    return_1d = c[-1] / c[-2] - 1
    return_5d = c[-1] / c[-6] - 1
    ma5_ratio = c[-1] / c[-5:].mean() - 1
    ma20_ratio = c[-1] / c[-20:].mean() - 1
    daily_ret = c[1:] / c[:-1] - 1
    vol_5d = daily_ret[-5:].std(ddof=1)            # pandas와 동일(ddof=1)
    volume_ratio = vols[-1] / vols[-20:].mean()
    return [return_1d, return_5d, ma5_ratio, ma20_ratio, vol_5d, volume_ratio]


def predict_proba(features, model):
    """스케일 → 가중합 → 시그모이드. sklearn 없이 순수 numpy."""
    x = (np.array(features) - np.array(model["mean"])) / np.array(model["std"])
    z = float(np.dot(x, model["coef"]) + model["intercept"])
    return 1.0 / (1.0 + np.exp(-z))


def predict_today(code):
    """오늘 매수 확률 P와 현재가/피처를 반환. 데이터 부족·오류 시 None."""
    model = load_model()
    closes, vols = fetch_recent_daily(code)
    if len(closes) < 21:                           # ma20 등 계산에 최소 21개 필요
        return None
    feats = compute_features(closes, vols)
    p = predict_proba(feats, model)
    return {
        "prob": round(p, 4),
        "signal": "buy" if p >= model["buy_threshold"] else "hold",
        "price": int(closes[-1]),
        "features": {model["features"][i]: round(float(feats[i]), 5) for i in range(len(feats))},
    }
