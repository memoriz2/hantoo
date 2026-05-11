"""API 연동 테스트 - 모의투자 계좌 잔고 조회"""
import requests
from config import BASE_URL, ACCOUNT_NO
from auth import get_headers


def get_balance():
    """계좌 잔고 조회"""
    url = f"{BASE_URL}/uapi/domestic-stock/v1/trading/inquire-balance"
    headers = get_headers("VTTC8434R")  # 모의투자 잔고조회

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
    data = res.json()

    if data["rt_cd"] == "0":
        print(f"예수금 총액: {data['output2'][0]['dnca_tot_amt']}원")
        print(f"총평가금액: {data['output2'][0]['tot_evlu_amt']}원")

        if data["output1"]:
            print("\n보유 종목:")
            for stock in data["output1"]:
                print(f"  {stock['prdt_name']}: {stock['hldg_qty']}주 (평가손익: {stock['evlu_pfls_amt']}원)")
        else:
            print("\n보유 종목 없음")
    else:
        print(f"오류: {data['msg1']}")


if __name__ == "__main__":
    get_balance()
