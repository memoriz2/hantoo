"""KODEX 200 자동매매 대시보드"""
from flask import Flask
import json
import re
import os

app = Flask(__name__)

LOG_FILE = os.path.join(os.path.dirname(__file__), "output.log")
TRADE_LOG_FILE = os.path.join(os.path.dirname(__file__), "trade_log.json")


def parse_monitor_log():
    """output.log에서 모니터링 로그 파싱"""
    entries = []
    if not os.path.exists(LOG_FILE):
        return entries
    with open(LOG_FILE, "r") as f:
        for line in f:
            # 매도 모니터링 로그
            m = re.search(
                r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] \[매도 모니터링\] 보유: (\d+)주 \| 평단가: ([\d,]+)원 \| 현재가: ([\d,]+)원 \| 수익률: ([+\-\d.]+)%',
                line
            )
            if m:
                entries.append({
                    "time": m.group(1),
                    "qty": int(m.group(2)),
                    "avg_price": int(m.group(3).replace(",", "")),
                    "current_price": int(m.group(4).replace(",", "")),
                    "profit_rate": float(m.group(5)),
                })
            # 현재가 & 기울기 로그
            p = re.search(
                r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] 현재가: ([\d,]+)원 \| 기울기: ([+\-\d.]+)%/분 \| 매수: (\d+)/(\d+)주',
                line
            )
            if p:
                entries.append({
                    "time": p.group(1),
                    "current_price": int(p.group(2).replace(",", "")),
                    "slope": float(p.group(3)),
                    "bought": int(p.group(4)),
                    "target": int(p.group(5)),
                })
    return entries


def load_trade_log():
    if not os.path.exists(TRADE_LOG_FILE):
        return {"trades": []}
    with open(TRADE_LOG_FILE, "r") as f:
        return json.load(f)


def build_history_table(trades):
    rows = []
    for t in reversed(trades[-30:]):
        time_str = t.get("time", "-")[:16]
        is_sell = t.get("type") == "sell"
        css_class = "sell" if is_sell else "buy"
        label = "매도" if is_sell else "매수"
        qty = t.get("qty", 0)
        price = t.get("price", 0)
        reason = t.get("reason", "")
        reason_label = {
            "slope_buy": "기울기 매수",
            "deadline_buy": "데드라인 매수",
            "profit_sell": "수익률 매도",
        }.get(reason, reason)
        profit_rate = t.get("profit_rate", "")
        profit_str = f'{profit_rate:+.2f}%' if isinstance(profit_rate, (int, float)) else ""
        rows.append(
            f'<tr><td>{time_str}</td><td class="{css_class}">{label}</td>'
            f'<td>{qty}주</td><td>{price:,}원</td><td>{profit_str}</td><td>{reason_label}</td></tr>'
        )
    return "\n".join(rows)


@app.route("/")
def dashboard():
    entries = parse_monitor_log()
    trade_log = load_trade_log()
    trades = trade_log.get("trades", [])

    # 최근 데이터
    latest = entries[-1] if entries else {}

    # 매수/매도 통계
    buys = [t for t in trades if t.get("type") == "buy"]
    sells = [t for t in trades if t.get("type") == "sell"]
    total_buy_amount = sum(t.get("qty", 0) * t.get("price", 0) for t in buys)
    total_sell_amount = sum(t.get("qty", 0) * t.get("price", 0) for t in sells)

    # 오늘 매수 수량
    from datetime import date
    today_str = date.today().isoformat()
    today_buys = sum(t.get("qty", 0) for t in buys if t.get("time", "").startswith(today_str))
    today_target = latest.get("target", 5)

    # 차트 데이터 (가격)
    price_entries = [e for e in entries if "current_price" in e][-288:]
    times = [e["time"][5:16] for e in price_entries]
    prices = [e["current_price"] for e in price_entries]

    # 기울기 데이터
    slope_entries = [e for e in entries if "slope" in e][-288:]
    slope_times = [e["time"][5:16] for e in slope_entries]
    slopes = [e["slope"] for e in slope_entries]

    current_price = latest.get("current_price", 0)
    profit_rate = latest.get("profit_rate", 0)
    slope = latest.get("slope", None)
    slope_str = f'{slope:+.4f}%/분' if slope is not None else "수집중"

    if slope is not None:
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
        trend = "수집중"

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="60">
    <title>KODEX 200 자동매매 대시보드</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: #1a1a2e; color: #eee; padding: 20px; }}
        .header {{ text-align: center; margin-bottom: 30px; }}
        .header h1 {{ font-size: 24px; color: #e94560; }}
        .header .sub {{ color: #888; font-size: 13px; margin-top: 5px; }}
        .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 15px; margin-bottom: 30px; }}
        .card {{ background: #16213e; border-radius: 12px; padding: 20px; text-align: center; }}
        .card .label {{ font-size: 13px; color: #888; margin-bottom: 8px; }}
        .card .value {{ font-size: 26px; font-weight: bold; }}
        .card .value.plus {{ color: #00d2d3; }}
        .card .value.minus {{ color: #e94560; }}
        .card .value.neutral {{ color: #ffa502; }}
        .chart-container {{ background: #16213e; border-radius: 12px; padding: 20px; margin-bottom: 20px; height: 240px; }}
        .chart-container h2 {{ font-size: 16px; color: #ccc; margin-bottom: 15px; }}
        @media (max-width: 768px) {{ .chart-container {{ height: 340px; }} }}
        table {{ width: 100%; border-collapse: collapse; background: #16213e; border-radius: 12px; overflow: hidden; }}
        th {{ background: #0f3460; padding: 12px; text-align: left; font-size: 13px; color: #888; }}
        td {{ padding: 10px 12px; border-top: 1px solid #1a1a2e; font-size: 13px; }}
        .buy {{ color: #00d2d3; font-weight: bold; }}
        .sell {{ color: #e94560; font-weight: bold; }}
        .section-title {{ font-size: 16px; color: #ccc; margin: 20px 0 10px; }}
        .mode {{ display: inline-block; background: #e94560; color: white; padding: 3px 10px; border-radius: 20px; font-size: 12px; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>KODEX 200 자동매매 <span class="mode">실전</span></h1>
        <div class="sub">마지막 업데이트: {latest.get("time", "-")} | 60초마다 자동 새로고침</div>
    </div>

    <div class="cards">
        <div class="card">
            <div class="label">현재가</div>
            <div class="value neutral">{current_price:,}원</div>
        </div>
        <div class="card">
            <div class="label">수익률</div>
            <div class="value {'plus' if profit_rate >= 0 else 'minus'}">{profit_rate:+.2f}%</div>
        </div>
        <div class="card">
            <div class="label">기울기 추세</div>
            <div class="value neutral">{trend}</div>
        </div>
        <div class="card">
            <div class="label">오늘 매수</div>
            <div class="value neutral">{today_buys} / {today_target}주</div>
        </div>
        <div class="card">
            <div class="label">총 매수 / 매도</div>
            <div class="value neutral">{len(buys)}회 / {len(sells)}회</div>
        </div>
        <div class="card">
            <div class="label">누적 매수금액</div>
            <div class="value neutral">{total_buy_amount:,}원</div>
        </div>
    </div>

    <div class="chart-container">
        <h2>KODEX 200 가격 추이</h2>
        <canvas id="priceChart"></canvas>
    </div>

    <div class="chart-container">
        <h2>기울기 추이 (%/분)</h2>
        <canvas id="slopeChart"></canvas>
    </div>

    <h3 class="section-title">매매 히스토리 (최근 30건)</h3>
    <table>
        <tr><th>시간</th><th>구분</th><th>수량</th><th>가격</th><th>수익률</th><th>사유</th></tr>
        {build_history_table(trades)}
    </table>

    <script>
        const times = {json.dumps(times)};
        const prices = {json.dumps(prices)};
        const slopeTimes = {json.dumps(slope_times)};
        const slopes = {json.dumps(slopes)};

        new Chart(document.getElementById('priceChart'), {{
            type: 'line',
            data: {{
                labels: times,
                datasets: [{{
                    label: '현재가',
                    data: prices,
                    borderColor: '#00d2d3',
                    backgroundColor: 'rgba(0, 210, 211, 0.1)',
                    fill: true,
                    tension: 0.3,
                    pointRadius: 0,
                    borderWidth: 2,
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{ legend: {{ display: false }} }},
                scales: {{
                    x: {{ ticks: {{ color: '#888', maxTicksLimit: 12 }}, grid: {{ color: '#1a1a2e' }} }},
                    y: {{ ticks: {{ color: '#888' }}, grid: {{ color: '#222' }} }}
                }}
            }}
        }});

        new Chart(document.getElementById('slopeChart'), {{
            type: 'line',
            data: {{
                labels: slopeTimes,
                datasets: [{{
                    label: '기울기 (%/분)',
                    data: slopes,
                    borderColor: '#ffa502',
                    backgroundColor: 'rgba(255, 165, 2, 0.1)',
                    fill: true,
                    tension: 0.3,
                    pointRadius: 0,
                    borderWidth: 2,
                }}, {{
                    label: '매수 기준선',
                    data: Array(slopeTimes.length).fill(-0.05),
                    borderColor: '#e94560',
                    borderDash: [5, 5],
                    borderWidth: 1,
                    pointRadius: 0,
                    fill: false,
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{ legend: {{ labels: {{ color: '#888' }} }} }},
                scales: {{
                    x: {{ ticks: {{ color: '#888', maxTicksLimit: 12 }}, grid: {{ color: '#1a1a2e' }} }},
                    y: {{ ticks: {{ color: '#888' }}, grid: {{ color: '#222' }} }}
                }}
            }}
        }});
    </script>
</body>
</html>"""


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
