"""KODEX 200 자동매매 프로그램 - 기울기 분할매수 + 수익률 매도"""
import json
import os
import time
import numpy as np
import requests
import schedule
from datetime import datetime, date, timedelta
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
DCA_START_MINUTE = 30          # 장 시작 후 30분(09:30)부터 분산매수 시작
SLOPE_BUY_COOLDOWN = 20        # 기울기 매수 후 최소 대기 시간 (분)
LOG_FILE = os.path.join(os.path.dirname(__file__), "trade_log.json")
PRICE_HISTORY_FILE = os.path.join(os.path.dirname(__file__), "price_history.json")

# 가격 히스토리 (기울기 계산용)
price_history = []
today_bought = 0  # 오늘 매수한 수량
last_dca_time = None  # 마지막 분산매수 시각
last_slope_buy_time = None  # 마지막 기울기 매수 시각


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_price_history():
    """저장된 가격 히스토리 로드 (3일치만 유지)"""
    global price_history
    if not os.path.exists(PRICE_HISTORY_FILE):
        return
    try:
        with open(PRICE_HISTORY_FILE, "r") as f:
            data = json.load(f)
        # 3일 이내 데이터만 유지
        cutoff = (datetime.now() - timedelta(days=3)).isoformat()
        data = [d for d in data if d["time"] >= cutoff]
        price_history = [d["price"] for d in data]
        print(f"[{now()}] 가격 히스토리 복원: {len(price_history)}개")
    except (json.JSONDecodeError, ValueError, KeyError):
        price_history = []


def save_price_history():
    """가격 히스토리를 JSON으로 저장"""
    try:
        # 기존 데이터 로드
        existing = []
        if os.path.exists(PRICE_HISTORY_FILE):
            with open(PRICE_HISTORY_FILE, "r") as f:
                existing = json.load(f)
        # 3일 이내만 유지
        cutoff = (datetime.now() - timedelta(days=3)).isoformat()
        existing = [d for d in existing if d["time"] >= cutoff]
        # 새 데이터 추가
        existing.append({"time": now(), "price": price_history[-1]})
        with open(PRICE_HISTORY_FILE, "w") as f:
            json.dump(existing, f)
    except Exception as e:
        print(f"[{now()}] 가격 히스토리 저장 에러: {e}")


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
    try:
        url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
        headers = get_headers("FHKST01010100")
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": STOCK_CODE,
        }
        res = requests.get(url, headers=headers, params=params, timeout=10)
        res.raise_for_status()
        data = res.json()
        if data["rt_cd"] == "0":
            return int(data["output"]["stck_prpr"])
        print(f"[{now()}] 현재가 조회 실패: {data.get('msg1')}")
    except Exception as e:
        print(f"[{now()}] 현재가 조회 에러: {e}")
    return None


def get_balance():
    """계좌 잔고 조회 (예수금, 보유종목)"""
    try:
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
        res = requests.get(url, headers=headers, params=params, timeout=10)
        res.raise_for_status()
        return res.json()
    except Exception as e:
        print(f"[{now()}] 잔고 조회 에러: {e}")
        return {"rt_cd": "-1", "msg1": str(e), "output1": [], "output2": [{"dnca_tot_amt": "0"}]}


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
    try:
        res = requests.post(url, headers=headers, json=body, timeout=10)
        res.raise_for_status()
        data = res.json()
        if data["rt_cd"] == "0":
            print(f"[{now()}] 매수 성공: {STOCK_NAME} {qty}주")
        else:
            print(f"[{now()}] 매수 실패: {data.get('msg1')}")
        return data
    except Exception as e:
        print(f"[{now()}] 매수 주문 에러: {e}")
        return {"rt_cd": "-1", "msg1": str(e)}


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
    try:
        res = requests.post(url, headers=headers, json=body, timeout=10)
        res.raise_for_status()
        data = res.json()
        if data["rt_cd"] == "0":
            print(f"[{now()}] 매도 성공: {STOCK_NAME} {qty}주")
        else:
            print(f"[{now()}] 매도 실패: {data.get('msg1')}")
    except Exception as e:
        print(f"[{now()}] 매도 주문 에러: {e}")
        data = {"rt_cd": "-1", "msg1": str(e)}
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


def restore_last_buy_times():
    """trade_log.json에서 오늘 마지막 매수 시간을 복원"""
    global last_dca_time, last_slope_buy_time
    log = load_log()
    today_str = date.today().isoformat()
    for t in reversed(log["trades"]):
        if t.get("type") != "buy" or not t.get("time", "").startswith(today_str):
            continue
        buy_time = datetime.strptime(t["time"], "%Y-%m-%d %H:%M:%S")
        if t.get("reason") == "slope_buy" and last_slope_buy_time is None:
            last_slope_buy_time = buy_time
        if last_dca_time is None:
            last_dca_time = buy_time
        if last_dca_time is not None and last_slope_buy_time is not None:
            break
    if last_dca_time or last_slope_buy_time:
        print(f"[{now()}] 매수 시간 복원 — DCA: {last_dca_time}, 기울기: {last_slope_buy_time}")


def get_dca_interval(target):
    """목표 수량에 따른 분산매수 간격(분) 계산 (09:30~14:00)"""
    available_minutes = (DEADLINE_HOUR * 60) - (9 * 60 + DCA_START_MINUTE)
    return available_minutes / target


def do_buy(qty, price, slope, reason):
    """매수 실행 + 로그 기록"""
    global today_bought
    result = buy_stock(qty)
    if result["rt_cd"] == "0":
        today_bought += qty
        log = load_log()
        log["trades"].append({
            "time": now(),
            "type": "buy",
            "stock": STOCK_NAME,
            "qty": qty,
            "price": price,
            "slope": round(slope, 6) if slope else None,
            "reason": reason,
            "today_total": today_bought,
        })
        save_log(log)
        return True
    return False


def try_buy():
    """시간 분산 매수(DCA) + 기울기 추가 매수 + 데드라인 안전망"""
    global today_bought, last_dca_time, last_slope_buy_time

    if not is_market_open():
        return

    # 현재가 수집
    price = get_current_price()
    if price is None:
        return
    price_history.append(price)
    if len(price_history) > 100:
        del price_history[:len(price_history) - 100]
    save_price_history()

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
    minutes_since_open = n.hour * 60 + n.minute - (9 * 60)
    dca_active = minutes_since_open >= DCA_START_MINUTE

    if slope is not None:
        print(f"[{now()}] 현재가: {price:,}원 | 기울기: {slope:+.4f}%/분 | 매수: {today_bought}/{target}주")
    else:
        print(f"[{now()}] 현재가: {price:,}원 | 기울기: 데이터 수집중 ({len(price_history)}/{SLOPE_WINDOW}) | 매수: {today_bought}/{target}주")

    bought_this_cycle = False

    # 1. 기울기 매수: 하락 감지 시 추가 1주 매수 (쿨다운 20분)
    slope_cooldown_ok = (last_slope_buy_time is None) or \
                        (n - last_slope_buy_time).total_seconds() / 60 >= SLOPE_BUY_COOLDOWN
    if slope is not None and slope <= SLOPE_THRESHOLD and remaining > 0 and slope_cooldown_ok:
        print(f"[{now()}] 하락 감지! 기울기 매수 1주")
        if do_buy(1, price, slope, "slope_buy"):
            remaining -= 1
            bought_this_cycle = True
            last_dca_time = n
            last_slope_buy_time = n

    # 2. 시간 분산 매수(DCA): 일정 간격마다 1주씩
    if not bought_this_cycle and dca_active and remaining > 0:
        interval = get_dca_interval(target)
        should_buy = (last_dca_time is None) or \
                     (n - last_dca_time).total_seconds() / 60 >= interval
        if should_buy:
            print(f"[{now()}] 시간 분산 매수 1주 (간격: {interval:.0f}분)")
            if do_buy(1, price, slope, "dca_buy"):
                remaining -= 1
                bought_this_cycle = True
                last_dca_time = n

    # 3. 데드라인: 14시 넘었는데 목표 미달이면 나머지 일괄 매수
    if n.hour >= DEADLINE_HOUR and remaining > 0:
        print(f"[{now()}] 데드라인 도달! 나머지 {remaining}주 일괄 매수")
        do_buy(remaining, price, slope, "deadline_buy")


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
    """매일 장 시작 전 초기화 (가격 히스토리는 유지)"""
    global today_bought, last_dca_time, last_slope_buy_time
    today_bought = 0
    last_dca_time = None
    last_slope_buy_time = None
    print(f"\n[{now()}] === 새로운 거래일 시작 ===")


if __name__ == "__main__":
    print("=" * 50)
    print(f"  {STOCK_NAME} 자동매매 프로그램")
    print("=" * 50)
    print(f"  모드: {'실전투자' if MODE == 'real' else '모의투자'}")
    print(f"  종목: {STOCK_NAME} ({STOCK_CODE})")
    print(f"  매수: 시간분산(DCA) + 기울기 하락 시 추가매수")
    print(f"  하루 목표: {DAILY_TARGET_QTY}주 (예수금 {CASH_THRESHOLD/10000:.0f}만원 이상: {DAILY_TARGET_QTY_HIGH}주)")
    print(f"  매도: 수익률 {SELL_PROFIT_RATE}% 이상 시 절반 매도")
    print(f"  DCA 시작: 09:{DCA_START_MINUTE:02d} | 데드라인: {DEADLINE_HOUR}시")
    print(f"  체크 간격: {PRICE_CHECK_INTERVAL}분")
    print("=" * 50)

    # 저장된 상태 복원
    load_price_history()
    restore_last_buy_times()

    # 매일 08:55에 초기화
    schedule.every().day.at("08:55").do(reset_daily)

    # 5분마다 매수 체크 (DCA + 기울기)
    schedule.every(PRICE_CHECK_INTERVAL).minutes.do(try_buy)

    # 5분마다 매도 체크
    schedule.every(PRICE_CHECK_INTERVAL).minutes.do(check_sell)

    print(f"\n[{now()}] 스케줄러 시작 ({'실전' if MODE == 'real' else '모의'})")
    print(f"[{now()}] 종료하려면 Ctrl+C를 누르세요.\n")

    # 시작 시 바로 한번 실행
    if is_market_open():
        try:
            reset_daily()
            try_buy()
            check_sell()
        except Exception as e:
            print(f"[{now()}] 시작 시 에러 (무시하고 스케줄러 진입): {e}")

    while True:
        try:
            schedule.run_pending()
        except Exception as e:
            print(f"[{now()}] 스케줄러 에러 (계속 실행): {e}")
        time.sleep(30)
