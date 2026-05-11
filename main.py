"""KODEX 200 자동매매 프로그램 - 기울기 분할매수 + 수익률 매도"""
import json
import os
import time
import numpy as np
import requests
import schedule
from datetime import datetime, date
from config import BASE_URL, ACCOUNT_NO, MODE
from auth import get_headers

# === 설정 ===
STOCK_CODE = "069500"          # KODEX 200 종목코드
STOCK_NAME = "KODEX 200"
DAILY_TARGET_QTY = 5           # 기본 하루 목표 매수 수량
DAILY_TARGET_QTY_HIGH = 7      # 예수금 많을 때 매수 수량
CASH_THRESHOLD = 5000000       # 이 이상이면 매수 수량 증가
SELL_PROFIT_RATE = 15.0        # 매도 기준 수익률 (%)
PRICE_CHECK_INTERVAL = 5       # 가격 체크 간격 (분)
SLOPE_WINDOW = 6               # 기울기 계산용 데이터 수 (6개 = 30분)
SLOPE_THRESHOLD = -0.05        # 이 이하면 하락 추세로 판단 (%/분)
DEADLINE_HOUR = 14             # 이 시각까지 목표 미달 시 나머지 일괄 매수
LOG_FILE = os.path.join(os.path.dirname(__file__), "trade_log.json")

# 가격 히스토리 (기울기 계산용)
price_history = []
today_bought = 0  # 오늘 매수한 수량


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def is_market_open():
    """장 운영 시간인지 확인 (09:00~15:20)"""
    n = datetime.now()
    if n.weekday() >= 5:  # 토, 일
        return False
    market_open = n.replace(hour=9, minute=0, second=0)
    market_close = n.replace(hour=15, minute=20, second=0)
    return market_open <= n <= market_close


def load_log():
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r") as f:
            return json.load(f)
    return {"trades": []}


def save_log(log):
    with open(LOG_FILE, "w") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)


def get_current_price():
    """KODEX 200 현재가 조회"""
    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
    headers = get_headers("FHKST01010100")
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": STOCK_CODE,
    }
    res = requests.get(url, headers=headers, params=params)
    res.raise_for_status()
    data = res.json()
    if data["rt_cd"] == "0":
        return int(data["output"]["stck_prpr"])
    print(f"[{now()}] 현재가 조회 실패: {data.get('msg1')}")
    return None


def get_balance():
    """계좌 잔고 조회 (예수금, 보유종목)"""
    url = f"{BASE_URL}/uapi/domestic-stock/v1/trading/inquire-balance"
    tr_id = "TTTC8434R" if MODE == "real" else "VTTC8434R"
    headers = get_headers(tr_id)
    params = {
        "CANO": ACCOUNT_NO,
        "ACNT_PRDT_CD": "01",
        "AFHR_FLPR_YN": "N",
        "OFL_YN": "",
        "INQR_DVSN": "02",
        "UNPR_DVSN": "01",
        "FUND_STTL_ICLD_YN": "N",
        "FNCG_AMT_AUTO_RDPT_YN": "N",
        "PRCS_DVSN": "01",
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": "",
    }
    res = requests.get(url, headers=headers, params=params)
    res.raise_for_status()
    return res.json()


def get_cash_balance():
    """예수금 조회"""
    data = get_balance()
    if data["rt_cd"] == "0":
        return int(data["output2"][0]["dnca_tot_amt"])
    return 0


def get_stock_holding():
    """KODEX 200 보유 정보 (수량, 평단가, 수익률)"""
    data = get_balance()
    if data["rt_cd"] != "0":
        return 0, 0, 0

    for stock in data["output1"]:
        if stock["pdno"] == STOCK_CODE:
            qty = int(stock["hldg_qty"])
            avg_price = float(stock["pchs_avg_pric"])
            profit_rate = float(stock["evlu_pfls_rt"])
            return qty, avg_price, profit_rate
    return 0, 0, 0


def buy_stock(qty):
    """KODEX 200 시장가 매수"""
    url = f"{BASE_URL}/uapi/domestic-stock/v1/trading/order-cash"
    tr_id = "TTTC0802U" if MODE == "real" else "VTTC0802U"
    headers = get_headers(tr_id)
    body = {
        "CANO": ACCOUNT_NO,
        "ACNT_PRDT_CD": "01",
        "PDNO": STOCK_CODE,
        "ORD_DVSN": "01",  # 시장가
        "ORD_QTY": str(qty),
        "ORD_UNPR": "0",
    }
    res = requests.post(url, headers=headers, json=body)
    res.raise_for_status()
    data = res.json()
    if data["rt_cd"] == "0":
        print(f"[{now()}] 매수 성공: {STOCK_NAME} {qty}주")
    else:
        print(f"[{now()}] 매수 실패: {data.get('msg1')}")
    return data


def sell_stock(qty):
    """KODEX 200 시장가 매도"""
    url = f"{BASE_URL}/uapi/domestic-stock/v1/trading/order-cash"
    tr_id = "TTTC0801U" if MODE == "real" else "VTTC0801U"
    headers = get_headers(tr_id)
    body = {
        "CANO": ACCOUNT_NO,
        "ACNT_PRDT_CD": "01",
        "PDNO": STOCK_CODE,
        "ORD_DVSN": "01",  # 시장가
        "ORD_QTY": str(qty),
        "ORD_UNPR": "0",
    }
    res = requests.post(url, headers=headers, json=body)
    res.raise_for_status()
    data = res.json()
    if data["rt_cd"] == "0":
        print(f"[{now()}] 매도 성공: {STOCK_NAME} {qty}주")
    else:
        print(f"[{now()}] 매도 실패: {data.get('msg1')}")
    return data


def get_today_target():
    """오늘 목표 매수 수량 (예수금에 따라 조절)"""
    cash = get_cash_balance()
    target = DAILY_TARGET_QTY_HIGH if cash >= CASH_THRESHOLD else DAILY_TARGET_QTY
    print(f"[{now()}] 예수금: {cash:,}원 → 오늘 목표: {target}주")
    return target


def calc_slope():
    """가격 기울기 계산 (%/분). None이면 데이터 부족."""
    if len(price_history) < SLOPE_WINDOW:
        return None
    recent = price_history[-SLOPE_WINDOW:]
    base_price = recent[0]
    if base_price == 0:
        return None
    # 수익률로 변환 후 기울기
    rates = [(p - base_price) / base_price * 100 for p in recent]
    x = np.arange(len(rates)) * PRICE_CHECK_INTERVAL
    slope = np.polyfit(x, rates, 1)[0]
    return slope


def bought_today_count():
    """오늘 매수한 총 수량"""
    log = load_log()
    today_str = date.today().isoformat()
    count = 0
    for t in log["trades"]:
        if t.get("type") == "buy" and t.get("time", "").startswith(today_str):
            count += t.get("qty", 0)
    return count


def sold_today():
    """오늘 매도한 기록이 있는지"""
    log = load_log()
    today_str = date.today().isoformat()
    return any(
        t.get("type") == "sell" and t.get("time", "").startswith(today_str)
        for t in log["trades"]
    )


def try_slope_buy():
    """기울기 확인 후 1주 분할 매수 시도"""
    global today_bought

    if not is_market_open():
        return

    # 현재가 수집
    price = get_current_price()
    if price is None:
        return
    price_history.append(price)
    if len(price_history) > 100:
        del price_history[:len(price_history) - 100]

    # 오늘 매수 현황
    today_bought = bought_today_count()
    target = get_today_target()
    remaining = target - today_bought

    if remaining <= 0:
        print(f"[{now()}] 오늘 목표 달성 ({today_bought}/{target}주)")
        return

    # 기울기 계산
    slope = calc_slope()
    n = datetime.now()

    if slope is not None:
        print(f"[{now()}] 현재가: {price:,}원 | 기울기: {slope:+.4f}%/분 | 매수: {today_bought}/{target}주")

        if slope <= SLOPE_THRESHOLD:
            # 하락 감지 → 1주 매수
            print(f"[{now()}] 하락 감지! 1주 분할 매수")
            result = buy_stock(1)
            if result["rt_cd"] == "0":
                today_bought += 1
                log = load_log()
                log["trades"].append({
                    "time": now(),
                    "type": "buy",
                    "stock": STOCK_NAME,
                    "qty": 1,
                    "price": price,
                    "slope": round(slope, 6),
                    "reason": "slope_buy",
                    "today_total": today_bought,
                })
                save_log(log)
    else:
        print(f"[{now()}] 현재가: {price:,}원 | 기울기: 데이터 수집중 ({len(price_history)}/{SLOPE_WINDOW}) | 매수: {today_bought}/{target}주")

    # 데드라인 체크: 14시 넘었는데 목표 미달이면 나머지 일괄 매수
    if n.hour >= DEADLINE_HOUR and remaining > 0:
        print(f"[{now()}] 데드라인 도달! 나머지 {remaining}주 일괄 매수")
        result = buy_stock(remaining)
        if result["rt_cd"] == "0":
            today_bought += remaining
            log = load_log()
            log["trades"].append({
                "time": now(),
                "type": "buy",
                "stock": STOCK_NAME,
                "qty": remaining,
                "price": price,
                "slope": round(slope, 6) if slope else None,
                "reason": "deadline_buy",
                "today_total": today_bought,
            })
            save_log(log)


def check_sell():
    """수익률 체크 후 매도"""
    if not is_market_open():
        return
    if sold_today():
        return

    qty, avg_price, profit_rate = get_stock_holding()
    if qty == 0:
        return

    price = get_current_price()
    if price is None:
        return

    print(f"[{now()}] [매도 모니터링] 보유: {qty}주 | 평단가: {avg_price:,.0f}원 | 현재가: {price:,}원 | 수익률: {profit_rate:+.2f}%")

    if profit_rate >= SELL_PROFIT_RATE:
        sell_qty = qty // 2
        if sell_qty == 0:
            return
        print(f"[{now()}] 수익률 {profit_rate:.2f}% >= {SELL_PROFIT_RATE}%! {sell_qty}주 매도")
        result = sell_stock(sell_qty)
        if result["rt_cd"] == "0":
            log = load_log()
            log["trades"].append({
                "time": now(),
                "type": "sell",
                "stock": STOCK_NAME,
                "qty": sell_qty,
                "price": price,
                "profit_rate": round(profit_rate, 2),
                "avg_price": avg_price,
                "reason": "profit_sell",
            })
            save_log(log)


def reset_daily():
    """매일 장 시작 전 초기화"""
    global today_bought
    price_history.clear()
    today_bought = 0
    print(f"\n[{now()}] === 새로운 거래일 시작 ===")


if __name__ == "__main__":
    print("=" * 50)
    print(f"  {STOCK_NAME} 자동매매 프로그램")
    print("=" * 50)
    print(f"  모드: {'실전투자' if MODE == 'real' else '모의투자'}")
    print(f"  종목: {STOCK_NAME} ({STOCK_CODE})")
    print(f"  매수: 기울기 하락 시 1주씩 분할매수")
    print(f"  하루 목표: {DAILY_TARGET_QTY}주 (예수금 {CASH_THRESHOLD/10000:.0f}만원 이상: {DAILY_TARGET_QTY_HIGH}주)")
    print(f"  매도: 수익률 {SELL_PROFIT_RATE}% 이상 시 절반 매도")
    print(f"  데드라인: {DEADLINE_HOUR}시까지 미달 시 나머지 일괄 매수")
    print(f"  체크 간격: {PRICE_CHECK_INTERVAL}분")
    print("=" * 50)

    # 매일 08:55에 초기화
    schedule.every().day.at("08:55").do(reset_daily)

    # 5분마다 기울기 매수 체크
    schedule.every(PRICE_CHECK_INTERVAL).minutes.do(try_slope_buy)

    # 5분마다 매도 체크
    schedule.every(PRICE_CHECK_INTERVAL).minutes.do(check_sell)

    print(f"\n[{now()}] 스케줄러 시작 ({'실전' if MODE == 'real' else '모의'})")
    print(f"[{now()}] 종료하려면 Ctrl+C를 누르세요.\n")

    # 시작 시 바로 한번 실행
    if is_market_open():
        reset_daily()
        try_slope_buy()
        check_sell()

    while True:
        schedule.run_pending()
        time.sleep(30)
