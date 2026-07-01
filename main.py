"""자동매매 프로그램 - 기울기 분할매수 + 수익률 매도 (멀티종목)"""
import json
import os
import time
import numpy as np
import requests
import schedule
from datetime import datetime, date, timedelta
from config import BASE_URL, ACCOUNT_NO, MODE
from auth import get_headers

# === 종목 설정 ===
# buy_steps: (기울기 임계, 매수 수량) — 큰 하락(가장 음수)부터 차례로 매칭
STOCKS = [
    {
        "code": "069500", "name": "KODEX 200",
        "daily_target": 30, "daily_target_high": 50,
        "sell_profit_rate": 15.0,
        # 스마트 매도(트레일링+변동성밴드+예수금연동 래칫+기울기 급락). 없으면 즉시 절반매도.
        "smart_sell": {
            "margin_base": 1.0,        # 래칫 마진 기본(%p)
            "margin_cash_slope": 4.0,  # 현금비율(0~1)당 추가 마진(%p) → 마진 = base + 현금비율×slope
            "band_mult": 3.0,          # 변동성 밴드 배수
            "band_min": 0.4,           # 밴드 하한(%p)
            "band_max": 1.5,           # 밴드 상한(%p)
            "slice_frac": 0.20,        # 한 슬라이스 = 남은 수량의 20%
            "slice_min": 2,            # 최소 슬라이스 수량
            "slope_panic": -0.05,      # 기울기 이 이하면 밴드 무시하고 급락 매도
        },
        "buy_steps": [(-0.30, 5), (-0.15, 3), (-0.05, 1)],
    },
    {
        "code": "482730", "name": "TIGER S&P500커버드콜",
        "daily_target": 50, "daily_target_high": 80,
        "sell_profit_rate": 15.0,
        "buy_steps": [(-0.10, 10), (-0.05, 5), (-0.01, 2)],
    },
]

# === 공통 설정 ===
CASH_THRESHOLD = 5000000       # 이 이상이면 매수 수량 증가
PRICE_CHECK_INTERVAL = 5       # 가격 체크 간격 (분)
SLOPE_WINDOW = 6               # 기울기 계산용 데이터 수 (6개 = 30분)
SLOPE_BUY_COOLDOWN = 5         # 기울기 매수 후 최소 대기 시간 (분)
LOG_FILE = os.path.join(os.path.dirname(__file__), "trade_log.json")
ML_SHADOW_FILE = os.path.join(os.path.dirname(__file__), "ml_shadow.jsonl")

# Per-stock state: {code: {"price_history": [], "today_bought": 0, "last_slope_buy_time": None}}
stock_state = {}


def _price_history_file(code):
    return os.path.join(os.path.dirname(__file__), f"price_history_{code}.json")


def _price_archive_file(code):
    return os.path.join(os.path.dirname(__file__), f"price_archive_{code}.jsonl")


def append_price_archive(code, price):
    """안 지우는 영구 아카이브에 가격 한 줄 추가 (데이터 수집용). 실패해도 봇은 계속."""
    try:
        with open(_price_archive_file(code), "a") as f:
            f.write(json.dumps({"time": now(), "price": price}) + "\n")
    except Exception as e:
        print(f"[{now()}] 아카이브 저장 에러(무시): {e}")


def _get_stock(code):
    for s in STOCKS:
        if s["code"] == code:
            return s
    return None


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_price_history(code):
    """저장된 가격 히스토리 로드 (3일치만 유지)"""
    st = stock_state[code]
    path = _price_history_file(code)
    if not os.path.exists(path):
        return
    try:
        with open(path, "r") as f:
            data = json.load(f)
        cutoff = (datetime.now() - timedelta(days=3)).isoformat()
        data = [d for d in data if d["time"] >= cutoff]
        st["price_history"] = [d["price"] for d in data]
        name = _get_stock(code)["name"]
        print(f"[{now()}] [{name}] 가격 히스토리 복원: {len(st['price_history'])}개")
    except (json.JSONDecodeError, ValueError, KeyError):
        st["price_history"] = []


def save_price_history(code):
    """가격 히스토리를 JSON으로 저장"""
    st = stock_state[code]
    path = _price_history_file(code)
    try:
        existing = []
        if os.path.exists(path):
            with open(path, "r") as f:
                existing = json.load(f)
        cutoff = (datetime.now() - timedelta(days=3)).isoformat()
        existing = [d for d in existing if d["time"] >= cutoff]
        existing.append({"time": now(), "price": st["price_history"][-1]})
        with open(path, "w") as f:
            json.dump(existing, f)
    except Exception as e:
        print(f"[{now()}] 가격 히스토리 저장 에러: {e}")


def is_market_open():
    """장 운영 시간인지 확인 (09:00~15:20)"""
    n = datetime.now()
    if n.weekday() >= 5:
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


def get_current_price(code):
    """현재가 조회"""
    try:
        url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
        headers = get_headers("FHKST01010100")
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}
        res = requests.get(url, headers=headers, params=params, timeout=10)
        res.raise_for_status()
        data = res.json()
        if data["rt_cd"] == "0":
            return int(data["output"]["stck_prpr"])
        name = _get_stock(code)["name"]
        print(f"[{now()}] [{name}] 현재가 조회 실패: {data.get('msg1')}")
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
            "CANO": ACCOUNT_NO, "ACNT_PRDT_CD": "01", "AFHR_FLPR_YN": "N",
            "OFL_YN": "", "INQR_DVSN": "02", "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N", "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01", "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
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


def get_stock_holding(code):
    """보유 정보 (수량, 평단가, 수익률)"""
    data = get_balance()
    if data["rt_cd"] != "0":
        return 0, 0, 0
    for stock in data["output1"]:
        if stock["pdno"] == code:
            qty = int(stock["hldg_qty"])
            avg_price = float(stock["pchs_avg_pric"])
            profit_rate = float(stock["evlu_pfls_rt"])
            return qty, avg_price, profit_rate
    return 0, 0, 0


def buy_stock(qty, code):
    """시장가 매수"""
    stock = _get_stock(code)
    url = f"{BASE_URL}/uapi/domestic-stock/v1/trading/order-cash"
    tr_id = "TTTC0802U" if MODE == "real" else "VTTC0802U"
    headers = get_headers(tr_id)
    body = {
        "CANO": ACCOUNT_NO, "ACNT_PRDT_CD": "01", "PDNO": code,
        "ORD_DVSN": "01", "ORD_QTY": str(qty), "ORD_UNPR": "0",
    }
    try:
        res = requests.post(url, headers=headers, json=body, timeout=10)
        res.raise_for_status()
        data = res.json()
        if data["rt_cd"] == "0":
            print(f"[{now()}] [{stock['name']}] 매수 성공: {qty}주")
        else:
            print(f"[{now()}] [{stock['name']}] 매수 실패: {data.get('msg1')}")
        return data
    except Exception as e:
        print(f"[{now()}] [{stock['name']}] 매수 주문 에러: {e}")
        return {"rt_cd": "-1", "msg1": str(e)}


def sell_stock(qty, code):
    """시장가 매도"""
    stock = _get_stock(code)
    url = f"{BASE_URL}/uapi/domestic-stock/v1/trading/order-cash"
    tr_id = "TTTC0801U" if MODE == "real" else "VTTC0801U"
    headers = get_headers(tr_id)
    body = {
        "CANO": ACCOUNT_NO, "ACNT_PRDT_CD": "01", "PDNO": code,
        "ORD_DVSN": "01", "ORD_QTY": str(qty), "ORD_UNPR": "0",
    }
    try:
        res = requests.post(url, headers=headers, json=body, timeout=10)
        res.raise_for_status()
        data = res.json()
        if data["rt_cd"] == "0":
            print(f"[{now()}] [{stock['name']}] 매도 성공: {qty}주")
        else:
            print(f"[{now()}] [{stock['name']}] 매도 실패: {data.get('msg1')}")
    except Exception as e:
        print(f"[{now()}] [{stock['name']}] 매도 주문 에러: {e}")
        data = {"rt_cd": "-1", "msg1": str(e)}
    return data


def get_today_target(code):
    """오늘 목표 매수 수량 (예수금에 따라 조절)"""
    stock = _get_stock(code)
    cash = get_cash_balance()
    target = stock["daily_target_high"] if cash >= CASH_THRESHOLD else stock["daily_target"]
    print(f"[{now()}] [{stock['name']}] 예수금: {cash:,}원 → 오늘 목표: {target}주")
    return target


def get_buy_qty(stock, slope):
    """기울기 강도에 따른 매수 수량 결정. 매칭되는 가장 큰 단계 적용."""
    if slope is None:
        return 0
    for threshold, qty in stock["buy_steps"]:
        if slope <= threshold:
            return qty
    return 0


def calc_slope(code):
    """가격 기울기 계산 (%/분). None이면 데이터 부족."""
    ph = stock_state[code]["price_history"]
    if len(ph) < SLOPE_WINDOW:
        return None
    recent = ph[-SLOPE_WINDOW:]
    base_price = recent[0]
    if base_price == 0:
        return None
    rates = [(p - base_price) / base_price * 100 for p in recent]
    x = np.arange(len(rates)) * PRICE_CHECK_INTERVAL
    slope = np.polyfit(x, rates, 1)[0]
    return slope


def calc_volatility(code):
    """최근 가격 변동성 (인터벌 수익률의 표준편차, %). None이면 데이터 부족."""
    ph = stock_state[code]["price_history"]
    if len(ph) < SLOPE_WINDOW:
        return None
    recent = ph[-SLOPE_WINDOW:]
    rets = []
    for i in range(1, len(recent)):
        if recent[i - 1] == 0:
            continue
        rets.append((recent[i] - recent[i - 1]) / recent[i - 1] * 100)
    if len(rets) < 2:
        return None
    return float(np.std(rets))


def last_sell_profit(code):
    """trade_log에서 가장 최근 매도의 수익률(%). 없으면 None."""
    log = load_log()
    stock = _get_stock(code)
    for t in reversed(log["trades"]):
        if t.get("type") == "sell" and (t.get("code") == code or t.get("stock") == stock["name"]):
            return t.get("profit_rate")
    return None


def bought_today_count(code):
    """오늘 매수한 총 수량 (종목별)"""
    log = load_log()
    today_str = date.today().isoformat()
    stock = _get_stock(code)
    count = 0
    for t in log["trades"]:
        if t.get("type") != "buy" or not t.get("time", "").startswith(today_str):
            continue
        if t.get("code") == code or t.get("stock") == stock["name"]:
            count += t.get("qty", 0)
    return count


def sold_today(code):
    """오늘 매도한 기록이 있는지 (종목별)"""
    log = load_log()
    today_str = date.today().isoformat()
    stock = _get_stock(code)
    return any(
        t.get("type") == "sell" and t.get("time", "").startswith(today_str)
        and (t.get("code") == code or t.get("stock") == stock["name"])
        for t in log["trades"]
    )


def restore_last_buy_times():
    """trade_log.json에서 오늘 마지막 기울기 매수 시간을 복원"""
    log = load_log()
    today_str = date.today().isoformat()
    for s in STOCKS:
        code = s["code"]
        st = stock_state[code]
        for t in reversed(log["trades"]):
            if t.get("type") != "buy" or not t.get("time", "").startswith(today_str):
                continue
            if t.get("reason") == "slope_buy" and (t.get("code") == code or t.get("stock") == s["name"]):
                if st["last_slope_buy_time"] is None:
                    st["last_slope_buy_time"] = datetime.strptime(t["time"], "%Y-%m-%d %H:%M:%S")
                    print(f"[{now()}] [{s['name']}] 매수 시간 복원 — 기울기: {st['last_slope_buy_time']}")
                    break


def do_buy(qty, price, slope, reason, code):
    """매수 실행 + 로그 기록"""
    stock = _get_stock(code)
    st = stock_state[code]
    result = buy_stock(qty, code)
    if result["rt_cd"] == "0":
        st["today_bought"] += qty
        log = load_log()
        log["trades"].append({
            "time": now(),
            "type": "buy",
            "stock": stock["name"],
            "code": code,
            "qty": qty,
            "price": price,
            "slope": round(slope, 6) if slope else None,
            "reason": reason,
            "today_total": st["today_bought"],
        })
        save_log(log)
        return True
    return False


def try_buy_stock(code):
    """종목별 기울기 매수"""
    stock = _get_stock(code)
    st = stock_state[code]

    if not is_market_open():
        return

    price = get_current_price(code)
    if price is None:
        return
    st["price_history"].append(price)
    if len(st["price_history"]) > 100:
        del st["price_history"][:len(st["price_history"]) - 100]
    save_price_history(code)
    append_price_archive(code, price)   # 영구 아카이브(데이터 수집, 3일 제한 없음)

    st["today_bought"] = bought_today_count(code)
    target = get_today_target(code)
    remaining = target - st["today_bought"]

    if remaining <= 0:
        print(f"[{now()}] [{stock['name']}] 오늘 목표 달성 ({st['today_bought']}/{target}주)")
        return

    slope = calc_slope(code)
    n = datetime.now()

    if slope is not None:
        print(f"[{now()}] [{stock['name']}] 현재가: {price:,}원 | 기울기: {slope:+.4f}%/분 | 매수: {st['today_bought']}/{target}주")
    else:
        print(f"[{now()}] [{stock['name']}] 현재가: {price:,}원 | 기울기: 데이터 수집중 ({len(st['price_history'])}/{SLOPE_WINDOW}) | 매수: {st['today_bought']}/{target}주")

    slope_cooldown_ok = (st["last_slope_buy_time"] is None) or \
                        (n - st["last_slope_buy_time"]).total_seconds() / 60 >= SLOPE_BUY_COOLDOWN
    buy_qty = get_buy_qty(stock, slope)
    if buy_qty > 0 and remaining > 0 and slope_cooldown_ok:
        actual_qty = min(buy_qty, remaining)
        print(f"[{now()}] [{stock['name']}] 하락 감지! 기울기 매수 {actual_qty}주 (slope {slope:+.4f})")
        if do_buy(actual_qty, price, slope, "slope_buy", code):
            st["last_slope_buy_time"] = n


def _record_sell(code, stock, sell_qty, price, profit_rate, avg_price):
    """시장가 매도 실행 + 로그 기록. 성공 시 True."""
    result = sell_stock(sell_qty, code)
    if result["rt_cd"] != "0":
        return False
    log = load_log()
    log["trades"].append({
        "time": now(),
        "type": "sell",
        "stock": stock["name"],
        "code": code,
        "qty": sell_qty,
        "price": price,
        "profit_rate": round(profit_rate, 2),
        "avg_price": avg_price,
        "reason": "profit_sell",
    })
    save_log(log)
    return True


def check_sell_smart(code, stock, qty, avg_price, profit_rate, price, cfg):
    """스마트 매도: 트레일링 + 변동성밴드 + 예수금연동 래칫 + 기울기 급락."""
    st = stock_state[code]

    # 장중 최고 수익률 갱신
    if st.get("peak_profit") is None or profit_rate > st["peak_profit"]:
        st["peak_profit"] = profit_rate
    peak = st["peak_profit"]

    base_floor = stock["sell_profit_rate"]

    # 예수금 연동 마진 → 유효 바닥선(래칫)
    cash = get_cash_balance()
    pos_value = qty * price
    cash_ratio = cash / (cash + pos_value) if (cash + pos_value) > 0 else 0.0
    margin = cfg["margin_base"] + cash_ratio * cfg["margin_cash_slope"]
    last_sell = last_sell_profit(code)
    floor = base_floor if last_sell is None else max(base_floor, last_sell + margin)

    # 변동성 밴드
    vol = calc_volatility(code)
    if vol is None:
        band = cfg["band_max"]
    else:
        band = min(cfg["band_max"], max(cfg["band_min"], cfg["band_mult"] * vol))

    sell_level = max(floor, peak - band)
    slope = calc_slope(code)
    panic = slope is not None and slope <= cfg["slope_panic"]
    slope_str = "수집중" if slope is None else f"{slope:+.4f}"

    print(f"[{now()}] [{stock['name']}] [스마트매도] 수익률 {profit_rate:+.2f}% | 고점 {peak:+.2f}% | 바닥(래칫) {floor:.2f}% | 밴드 {band:.2f}%p | 발동선 {sell_level:.2f}% | 기울기 {slope_str} | 현금비율 {cash_ratio*100:.0f}% (마진 {margin:.2f}%p)")

    # 바닥선 위로 충분히 올라온 적이 있어야(peak>=floor) 매도 후보
    if peak < floor or profit_rate < base_floor:
        return

    triggered = profit_rate <= sell_level or (panic and profit_rate >= floor)
    if not triggered:
        return

    sell_qty = max(cfg["slice_min"], int(qty * cfg["slice_frac"]))
    sell_qty = min(sell_qty, qty)
    reason = "급락(기울기)" if panic and profit_rate > sell_level else "트레일링"
    print(f"[{now()}] [{stock['name']}] 스마트 매도 발동({reason})! {sell_qty}주 (수익률 {profit_rate:.2f}%, 발동선 {sell_level:.2f}%)")
    _record_sell(code, stock, sell_qty, price, profit_rate, avg_price)


def check_sell_stock(code):
    """종목별 수익률 체크 후 매도"""
    stock = _get_stock(code)
    if not is_market_open():
        return

    qty, avg_price, profit_rate = get_stock_holding(code)
    if qty == 0:
        return

    price = get_current_price(code)
    if price is None:
        return

    # 대시보드가 파싱하는 모니터링 라인 (형식 유지 필수)
    print(f"[{now()}] [{stock['name']}] [매도 모니터링] 보유: {qty}주 | 평단가: {avg_price:,.0f}원 | 현재가: {price:,}원 | 수익률: {profit_rate:+.2f}%")

    cfg = stock.get("smart_sell")
    if cfg:
        check_sell_smart(code, stock, qty, avg_price, profit_rate, price, cfg)
        return

    # 기존 즉시 절반 매도 (하루 1회)
    if sold_today(code):
        return
    if profit_rate >= stock["sell_profit_rate"]:
        sell_qty = qty // 2
        if sell_qty == 0:
            return
        print(f"[{now()}] [{stock['name']}] 수익률 {profit_rate:.2f}% >= {stock['sell_profit_rate']}%! {sell_qty}주 매도")
        _record_sell(code, stock, sell_qty, price, profit_rate, avg_price)


def run_ml_shadow():
    """[섀도우] ML 매수 예측을 기록만 함 (실제 주문 X). KODEX 200(069500)만.
    실매매 로직과 완전히 분리 — 여기서 order 함수는 절대 호출하지 않음."""
    if not is_market_open():
        return
    code = "069500"
    try:
        import ml_signal
        result = ml_signal.predict_today(code)
        if result is None:
            print(f"[{now()}] [ML섀도우] 데이터 부족 — 건너뜀")
            return
        cash = get_cash_balance()
        qty, _avg, _pr = get_stock_holding(code)
        price = result["price"]
        pos_value = qty * price
        exposure = pos_value / (cash + pos_value) if (cash + pos_value) > 0 else 0.0
        record = {
            "time": now(), "code": code,
            "prob": result["prob"], "signal": result["signal"],
            "price": price, "cash": cash,
            "holding_qty": qty, "exposure": round(exposure, 4),
        }
        with open(ML_SHADOW_FILE, "a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(f"[{now()}] [ML섀도우] P={result['prob']} 신호={result['signal']} (기록만, 매매X)")
    except Exception as e:
        print(f"[{now()}] [ML섀도우] 에러(무시): {e}")


def try_buy():
    """전 종목 매수 체크"""
    for s in STOCKS:
        try:
            try_buy_stock(s["code"])
        except Exception as e:
            print(f"[{now()}] [{s['name']}] 매수 체크 에러: {e}")


def check_sell():
    """전 종목 매도 체크"""
    for s in STOCKS:
        try:
            check_sell_stock(s["code"])
        except Exception as e:
            print(f"[{now()}] [{s['name']}] 매도 체크 에러: {e}")


def reset_daily():
    """매일 장 시작 전 초기화 (가격 히스토리는 유지)"""
    for s in STOCKS:
        st = stock_state[s["code"]]
        st["today_bought"] = 0
        st["last_slope_buy_time"] = None
        st["peak_profit"] = None
    print(f"\n[{now()}] === 새로운 거래일 시작 ===")


if __name__ == "__main__":
    # 종목별 상태 초기화
    for s in STOCKS:
        stock_state[s["code"]] = {
            "price_history": [],
            "today_bought": 0,
            "last_slope_buy_time": None,
            "peak_profit": None,
        }

    print("=" * 50)
    print(f"  자동매매 프로그램 (멀티종목)")
    print("=" * 50)
    print(f"  모드: {'실전투자' if MODE == 'real' else '모의투자'}")
    for s in STOCKS:
        mode = "스마트(트레일링+래칫)" if s.get("smart_sell") else "즉시 절반"
        print(f"  종목: {s['name']} ({s['code']}) — 목표 {s['daily_target']}주/일 · 매도 {mode}")
    print(f"  매수: 기울기 하락 감지 시 매수 (쿨다운 {SLOPE_BUY_COOLDOWN}분)")
    print(f"  체크 간격: {PRICE_CHECK_INTERVAL}분")
    print("=" * 50)

    # 기존 price_history.json → price_history_069500.json 마이그레이션
    old_ph = os.path.join(os.path.dirname(__file__), "price_history.json")
    new_ph = _price_history_file("069500")
    if os.path.exists(old_ph) and not os.path.exists(new_ph):
        os.rename(old_ph, new_ph)
        print(f"[{now()}] price_history.json → price_history_069500.json 마이그레이션 완료")

    # 저장된 상태 복원
    for s in STOCKS:
        load_price_history(s["code"])
    restore_last_buy_times()

    # 매일 08:55에 초기화
    schedule.every().day.at("08:55").do(reset_daily)

    # 5분마다 매수/매도 체크
    schedule.every(PRICE_CHECK_INTERVAL).minutes.do(try_buy)
    schedule.every(PRICE_CHECK_INTERVAL).minutes.do(check_sell)

    # [섀도우] 하루 1회 ML 예측 기록 (실제 매매 아님, 검증용)
    schedule.every().day.at("09:10").do(run_ml_shadow)

    print(f"\n[{now()}] 스케줄러 시작 ({'실전' if MODE == 'real' else '모의'})")
    print(f"[{now()}] 종료하려면 Ctrl+C를 누르세요.\n")

    # 시작 시 바로 한번 실행
    if is_market_open():
        try:
            reset_daily()
            try_buy()
            check_sell()
            run_ml_shadow()
        except Exception as e:
            print(f"[{now()}] 시작 시 에러 (무시하고 스케줄러 진입): {e}")

    while True:
        try:
            schedule.run_pending()
        except Exception as e:
            print(f"[{now()}] 스케줄러 에러 (계속 실행): {e}")
        time.sleep(30)
