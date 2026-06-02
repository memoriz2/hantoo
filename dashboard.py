"""자동매매 대시보드 (멀티종목)"""
from flask import Flask
import json
import re
import os
import subprocess

app = Flask(__name__)

LOG_FILE = os.path.join(os.path.dirname(__file__), "output.log")
TRADE_LOG_FILE = os.path.join(os.path.dirname(__file__), "trade_log.json")

DASHBOARD_STOCKS = [
    {"code": "069500", "name": "KODEX 200", "color": "#00d2d3", "slope_threshold": -0.05},
    {"code": "482730", "name": "TIGER S&P500커버드콜", "color": "#ffa502", "slope_threshold": -0.01},
]


def parse_monitor_log():
    """output.log에서 모니터링 로그 파싱 (멀티종목 지원)"""
    entries = []
    if not os.path.exists(LOG_FILE):
        return entries
    with open(LOG_FILE, "r") as f:
        for line in f:
            # 매도 모니터링 로그
            m = re.search(
                r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] (?:\[([^\]]+)\] )?\[매도 모니터링\] 보유: (\d+)주 \| 평단가: ([\d,]+)원 \| 현재가: ([\d,]+)원 \| 수익률: ([+\-\d.]+)%',
                line
            )
            if m:
                entries.append({
                    "time": m.group(1),
                    "stock": m.group(2) or "KODEX 200",
                    "qty": int(m.group(3)),
                    "avg_price": int(m.group(4).replace(",", "")),
                    "current_price": int(m.group(5).replace(",", "")),
                    "profit_rate": float(m.group(6)),
                })
                continue
            # 현재가 & 기울기 로그
            p = re.search(
                r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] (?:\[([^\]]+)\] )?현재가: ([\d,]+)원 \| 기울기: ([+\-\d.]+)%/분 \| 매수: (\d+)/(\d+)주',
                line
            )
            if p:
                entries.append({
                    "time": p.group(1),
                    "stock": p.group(2) or "KODEX 200",
                    "current_price": int(p.group(3).replace(",", "")),
                    "slope": float(p.group(4)),
                    "bought": int(p.group(5)),
                    "target": int(p.group(6)),
                })
                continue
            # 데이터 수집중 로그
            c = re.search(
                r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] (?:\[([^\]]+)\] )?현재가: ([\d,]+)원 \| 기울기: 데이터 수집중 \((\d+)/(\d+)\) \| 매수: (\d+)/(\d+)주',
                line
            )
            if c:
                entries.append({
                    "time": c.group(1),
                    "stock": c.group(2) or "KODEX 200",
                    "current_price": int(c.group(3).replace(",", "")),
                    "collecting": f"{c.group(4)}/{c.group(5)}",
                    "bought": int(c.group(6)),
                    "target": int(c.group(7)),
                })
    return entries


def load_trade_log():
    if not os.path.exists(TRADE_LOG_FILE):
        return {"trades": []}
    try:
        with open(TRADE_LOG_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, ValueError):
        return {"trades": []}


def build_history_table(trades):
    rows = []
    for t in reversed(trades[-30:]):
        time_str = t.get("time", "-")[:16]
        stock_name = t.get("stock", "-")
        is_sell = t.get("type") == "sell"
        css_class = "sell" if is_sell else "buy"
        label = "매도" if is_sell else "매수"
        qty = t.get("qty", 0)
        price = t.get("price", 0)
        reason = t.get("reason", "")
        reason_label = {
            "slope_buy": "기울기 매수",
            "dca_buy": "분산 매수",
            "deadline_buy": "데드라인 매수",
            "profit_sell": "수익률 매도",
        }.get(reason, reason)
        profit_rate = t.get("profit_rate", "")
        profit_str = f'{profit_rate:+.2f}%' if isinstance(profit_rate, (int, float)) else ""
        rows.append(
            f'<tr><td>{time_str}</td><td>{stock_name}</td><td class="{css_class}">{label}</td>'
            f'<td>{qty}주</td><td>{price:,}원</td><td>{profit_str}</td><td>{reason_label}</td></tr>'
        )
    return "\n".join(rows)


@app.route("/")
def dashboard():
    try:
        return _render_dashboard()
    except Exception as e:
        return f"<h2>대시보드 오류</h2><pre>{e}</pre>", 500


def is_market_hours():
    """현재 장 운영 시간인지 확인 (09:00~15:20, 평일)"""
    from datetime import datetime
    n = datetime.now()
    if n.weekday() >= 5:
        return False
    market_open = n.replace(hour=9, minute=0, second=0)
    market_close = n.replace(hour=15, minute=20, second=0)
    return market_open <= n <= market_close


def get_bot_status():
    """매수봇 프로세스 상태 확인 (systemd 서비스)"""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "hantoo-bot"],
            capture_output=True, text=True, timeout=5
        )
        process_alive = result.stdout.strip() == "active"
    except Exception:
        process_alive = False

    if not is_market_hours():
        return "closed"
    return "alive" if process_alive else "dead"


def _render_dashboard():
    bot_status = get_bot_status()
    entries = parse_monitor_log()
    trade_log = load_trade_log()
    trades = trade_log.get("trades", [])

    from datetime import datetime, timedelta, date

    # 봇 마지막 활동 시간 체크
    latest_any = entries[-1] if entries else {}
    last_log_time = latest_any.get("time")
    bot_stale = False
    if last_log_time:
        try:
            last_dt = datetime.strptime(last_log_time, "%Y-%m-%d %H:%M:%S")
            bot_stale = datetime.now() - last_dt > timedelta(minutes=10)
        except ValueError:
            pass
    if bot_stale and bot_status == "alive":
        bot_status = "dead"

    # 매수/매도 통계 (전체)
    buys = [t for t in trades if t.get("type") == "buy"]
    sells = [t for t in trades if t.get("type") == "sell"]
    total_buy_amount = sum(t.get("qty", 0) * t.get("price", 0) for t in buys)
    total_sell_amount = sum(t.get("qty", 0) * t.get("price", 0) for t in sells)

    # 종목별 데이터 수집
    stock_sections_html = ""
    chart_js = ""
    for s in DASHBOARD_STOCKS:
        name = s["name"]
        color = s["color"]
        stock_entries = [e for e in entries if e.get("stock") == name]

        latest = stock_entries[-1] if stock_entries else {}
        price_entries = [e for e in stock_entries if "current_price" in e][-288:]
        slope_entries = [e for e in stock_entries if "slope" in e][-288:]
        monitor_entries = [e for e in stock_entries if "profit_rate" in e]

        current_price = latest.get("current_price", 0)
        profit_rate = monitor_entries[-1]["profit_rate"] if monitor_entries else 0
        slope = latest.get("slope", None)
        collecting = latest.get("collecting", None)

        # 오늘 매수 수량
        today_str = date.today().isoformat()
        today_buys = sum(
            t.get("qty", 0) for t in buys
            if t.get("time", "").startswith(today_str) and t.get("stock") == name
        )
        today_target = next(
            (e["target"] for e in reversed(stock_entries) if "target" in e),
            0,
        )

        # 추세
        if collecting:
            trend = f"수집중 ({collecting})"
        elif slope is not None:
            if slope <= -0.1:
                trend = "하락 ↓↓"
            elif slope <= -0.05:
                trend = "하락 ↓"
            elif slope <= 0.05:
                trend = "횡보 →"
            elif slope <= 0.1:
                trend = "상승 ↑"
            else:
                trend = "급등 ↑↑"
        else:
            trend = "대기중"

        slope_str = f'{slope:+.4f}%/분' if slope is not None else "—"
        profit_class = "plus" if profit_rate >= 0 else "minus"

        times = json.dumps([e["time"][5:16] for e in price_entries])
        prices = json.dumps([e["current_price"] for e in price_entries])
        slope_times = json.dumps([e["time"][5:16] for e in slope_entries])
        slopes = json.dumps([e["slope"] for e in slope_entries])

        code = s["code"]
        stock_sections_html += f"""
    <div class="stock-section">
        <h2 class="stock-title">{name} <span class="stock-code">({code})</span></h2>
        <div class="cards">
            <div class="card">
                <div class="label">현재가</div>
                <div class="value neutral">{current_price:,}원</div>
            </div>
            <div class="card">
                <div class="label">수익률</div>
                <div class="value {profit_class}">{profit_rate:+.2f}%</div>
            </div>
            <div class="card">
                <div class="label">기울기 추세</div>
                <div class="value neutral">{trend}</div>
            </div>
            <div class="card">
                <div class="label">오늘 매수</div>
                <div class="value neutral">{today_buys} / {today_target}주</div>
            </div>
        </div>
        <div class="chart-container">
            <h3>가격 추이</h3>
            <canvas id="priceChart_{code}"></canvas>
        </div>
        <div class="chart-container">
            <h3>기울기 추이 (%/분)</h3>
            <canvas id="slopeChart_{code}"></canvas>
        </div>
    </div>"""

        chart_js += f"""
        new Chart(document.getElementById('priceChart_{code}'), {{
            type: 'line',
            data: {{
                labels: {times},
                datasets: [{{
                    label: '현재가',
                    data: {prices},
                    borderColor: '{color}',
                    backgroundColor: '{color}22',
                    fill: true, tension: 0.3, pointRadius: 0, borderWidth: 2,
                }}]
            }},
            options: {{
                responsive: true, maintainAspectRatio: false,
                plugins: {{ legend: {{ display: false }} }},
                scales: {{
                    x: {{ ticks: {{ color: '#888', maxTicksLimit: 12 }}, grid: {{ color: '#1a1a2e' }} }},
                    y: {{ ticks: {{ color: '#888' }}, grid: {{ color: '#222' }} }}
                }}
            }}
        }});
        new Chart(document.getElementById('slopeChart_{code}'), {{
            type: 'line',
            data: {{
                labels: {slope_times},
                datasets: [{{
                    label: '기울기 (%/분)',
                    data: {slopes},
                    borderColor: '{color}',
                    backgroundColor: '{color}22',
                    fill: true, tension: 0.3, pointRadius: 0, borderWidth: 2,
                }}, {{
                    label: '매수 기준선',
                    data: Array({len(slope_entries)}).fill({s["slope_threshold"]}),
                    borderColor: '#e94560', borderDash: [5, 5], borderWidth: 1,
                    pointRadius: 0, fill: false,
                }}]
            }},
            options: {{
                responsive: true, maintainAspectRatio: false,
                plugins: {{ legend: {{ labels: {{ color: '#888' }} }} }},
                scales: {{
                    x: {{ ticks: {{ color: '#888', maxTicksLimit: 12 }}, grid: {{ color: '#1a1a2e' }} }},
                    y: {{ ticks: {{ color: '#888' }}, grid: {{ color: '#222' }} }}
                }}
            }}
        }});"""

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="60">
    <title>자동매매 대시보드</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: #1a1a2e; color: #eee; padding: 20px; }}
        .header {{ text-align: center; margin-bottom: 30px; }}
        .header h1 {{ font-size: 24px; color: #e94560; }}
        .header .sub {{ color: #888; font-size: 13px; margin-top: 5px; }}
        .summary-cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 15px; margin-bottom: 30px; }}
        .stock-section {{ margin-bottom: 30px; padding: 20px; background: #0f3460; border-radius: 16px; }}
        .stock-title {{ font-size: 18px; color: #eee; margin-bottom: 15px; }}
        .stock-code {{ font-size: 13px; color: #888; font-weight: normal; }}
        .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 20px; }}
        .card {{ background: #16213e; border-radius: 12px; padding: 16px; text-align: center; }}
        .card .label {{ font-size: 12px; color: #888; margin-bottom: 6px; }}
        .card .value {{ font-size: 22px; font-weight: bold; }}
        .card .value.plus {{ color: #00d2d3; }}
        .card .value.minus {{ color: #e94560; }}
        .card .value.neutral {{ color: #ffa502; }}
        .summary-card {{ background: #16213e; border-radius: 12px; padding: 16px; text-align: center; }}
        .summary-card .label {{ font-size: 12px; color: #888; margin-bottom: 6px; }}
        .summary-card .value {{ font-size: 20px; font-weight: bold; color: #eee; }}
        .chart-container {{ background: #16213e; border-radius: 12px; padding: 15px; margin-bottom: 15px; height: 220px; }}
        .chart-container h3 {{ font-size: 14px; color: #ccc; margin-bottom: 10px; }}
        @media (max-width: 768px) {{ .chart-container {{ height: 300px; }} }}
        table {{ width: 100%; border-collapse: collapse; background: #16213e; border-radius: 12px; overflow: hidden; }}
        th {{ background: #0f3460; padding: 12px; text-align: left; font-size: 13px; color: #888; }}
        td {{ padding: 10px 12px; border-top: 1px solid #1a1a2e; font-size: 13px; }}
        .buy {{ color: #00d2d3; font-weight: bold; }}
        .sell {{ color: #e94560; font-weight: bold; }}
        .section-title {{ font-size: 16px; color: #ccc; margin: 20px 0 10px; }}
        .mode {{ display: inline-block; background: #e94560; color: white; padding: 3px 10px; border-radius: 20px; font-size: 12px; }}
        .bot-status {{ display: inline-block; padding: 3px 10px; border-radius: 20px; font-size: 12px; font-weight: bold; }}
        .bot-status.alive {{ background: #00b894; color: white; }}
        .bot-status.closed {{ background: #636e72; color: white; }}
        .bot-status.dead {{ background: #e94560; color: white; animation: blink 1s infinite; }}
        @keyframes blink {{ 50% {{ opacity: 0.5; }} }}
    </style>
</head>
<body>
    <div class="header">
        <h1>자동매매 대시보드 <span class="mode">실전</span> <span class="bot-status {bot_status}">{'봇 정상' if bot_status == 'alive' else '장 마감' if bot_status == 'closed' else '봇 중단!'}</span></h1>
        <div class="sub">마지막 업데이트: {latest_any.get("time", "-")} | 60초마다 자동 새로고침</div>
    </div>

    <div class="summary-cards">
        <div class="summary-card">
            <div class="label">총 매수 / 매도</div>
            <div class="value">{len(buys)}회 / {len(sells)}회</div>
        </div>
        <div class="summary-card">
            <div class="label">누적 매수금액</div>
            <div class="value">{total_buy_amount:,}원</div>
        </div>
        <div class="summary-card">
            <div class="label">누적 매도금액</div>
            <div class="value">{total_sell_amount:,}원</div>
        </div>
    </div>

    {stock_sections_html}

    <h3 class="section-title">매매 히스토리 (최근 30건)</h3>
    <table>
        <tr><th>시간</th><th>종목</th><th>구분</th><th>수량</th><th>가격</th><th>수익률</th><th>사유</th></tr>
        {build_history_table(trades)}
    </table>

    <script>
        {chart_js}
    </script>
</body>
</html>"""


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
