#!/usr/bin/env python3
"""
한국 주식 모니터링 프로그램
조건: 거래대금 2000억 이상 + 장대 양봉 발생 종목
데이터 소스: pykrx (기본) / FinanceDataReader (대체)
"""

import sys
import argparse
from datetime import datetime, timedelta
import pandas as pd

try:
    from tabulate import tabulate
    HAS_TABULATE = True
except ImportError:
    HAS_TABULATE = False

try:
    from colorama import init, Fore, Style
    init(autoreset=True)
    HAS_COLOR = True
except ImportError:
    HAS_COLOR = False

# ─── 색상 헬퍼 ───────────────────────────────────────────────
def color(text, c=""):
    if not HAS_COLOR:
        return text
    return f"{c}{text}{Style.RESET_ALL}"

def green(t):  return color(t, Fore.GREEN)
def red(t):    return color(t, Fore.RED)
def cyan(t):   return color(t, Fore.CYAN)
def yellow(t): return color(t, Fore.YELLOW)

# ─── 기준값 (argparse로 덮어씀) ─────────────────────────────
MIN_TRADING_VALUE = 200_000_000_000   # 2000억 원
MIN_BODY_RATIO    = 0.5               # 몸통이 전체 범위의 50% 이상
MIN_CHANGE_RATE   = 3.0               # 시가 대비 종가 상승률 3% 이상


# ─── 날짜 유틸 ───────────────────────────────────────────────
def get_recent_trading_day(date_str=None):
    """영업일 기준 날짜 반환 (주말 → 직전 금요일)"""
    dt = datetime.strptime(date_str, "%Y%m%d") if date_str else datetime.today()
    while dt.weekday() >= 5:
        dt -= timedelta(days=1)
    return dt.strftime("%Y%m%d")


# ─── 장대 양봉 판별 ──────────────────────────────────────────
def is_jangdae_yangbong(row):
    """
    장대 양봉 조건:
      1) 종가 > 시가  (양봉)
      2) (종가 - 시가) / 시가 × 100 >= MIN_CHANGE_RATE
      3) (종가 - 시가) / (고가 - 저가) >= MIN_BODY_RATIO
    """
    o, h, l, c = row.get("시가", 0), row.get("고가", 0), row.get("저가", 0), row.get("종가", 0)
    if o <= 0 or h <= l:
        return False
    body = c - o
    if body <= 0:
        return False
    return (body / o * 100 >= MIN_CHANGE_RATE) and (body / (h - l) >= MIN_BODY_RATIO)


# ─── pykrx 데이터 소스 ───────────────────────────────────────
def fetch_via_pykrx(date, market):
    from pykrx import stock as krx
    df = krx.get_market_ohlcv(date, market=market)
    if df is None or df.empty:
        return pd.DataFrame()
    df.index.name = "티커"
    df = df.reset_index()
    df["종목명"] = df["티커"].apply(lambda t: krx.get_market_ticker_name(t))
    return df


# ─── FinanceDataReader 데이터 소스 ───────────────────────────
def fetch_via_fdr(date, market):
    import FinanceDataReader as fdr
    listing = fdr.StockListing(market)         # 종목 목록
    results = []
    for _, row in listing.iterrows():
        ticker = row["Code"]
        suffix = ".KS" if market == "KOSPI" else ".KQ"
        try:
            df = fdr.DataReader(ticker, date, date)
            if df.empty:
                continue
            r = df.iloc[0]
            results.append({
                "티커": ticker,
                "종목명": row.get("Name", ticker),
                "시가": r.get("Open", 0),
                "고가": r.get("High", 0),
                "저가": r.get("Low", 0),
                "종가": r.get("Close", 0),
                "거래량": r.get("Volume", 0),
                "거래대금": r.get("Close", 0) * r.get("Volume", 0),
            })
        except Exception:
            continue
    return pd.DataFrame(results)


# ─── 공통 분석 ───────────────────────────────────────────────
def analyze(df, market):
    if df.empty:
        return pd.DataFrame()

    # 거래대금 컬럼 찾기
    tv_col = next((c for c in ["거래대금", "TradingValue"] if c in df.columns), None)
    if tv_col is None:
        print(red(f"  [경고] 거래대금 컬럼 없음. 컬럼 목록: {df.columns.tolist()}"))
        return pd.DataFrame()

    df_f = df[df[tv_col] >= MIN_TRADING_VALUE].copy()
    if df_f.empty:
        return pd.DataFrame()

    df_f["장대양봉"] = df_f.apply(is_jangdae_yangbong, axis=1)
    df_r = df_f[df_f["장대양봉"]].copy()
    if df_r.empty:
        return pd.DataFrame()

    df_r["시장"] = market
    df_r["등락률(%)"] = ((df_r["종가"] - df_r["시가"]) / df_r["시가"] * 100).round(2)
    df_r["몸통비율(%)"] = (
        (df_r["종가"] - df_r["시가"])
        / (df_r["고가"] - df_r["저가"]).replace(0, float("nan"))
        * 100
    ).round(1)
    df_r["거래대금(억)"] = (df_r[tv_col] / 1e8).round(0).astype(int)
    return df_r


# ─── 데모 데이터 ─────────────────────────────────────────────
def build_demo_data():
    """네트워크 없이 실행 구조를 확인하는 샘플 데이터"""
    rows = [
        {"티커": "005930", "종목명": "삼성전자",  "시장": "KOSPI",
         "시가": 78000,  "고가": 84000,  "저가": 77500,  "종가": 83000, "거래대금": 3_200_000_000_000},
        {"티커": "000660", "종목명": "SK하이닉스", "시장": "KOSPI",
         "시가": 190000, "고가": 205000, "저가": 188000, "종가": 203000, "거래대금": 2_800_000_000_000},
        {"티커": "035420", "종목명": "NAVER",      "시장": "KOSPI",
         "시가": 180000, "고가": 192000, "저가": 179000, "종가": 190000, "거래대금": 2_100_000_000_000},
        {"티커": "247540", "종목명": "에코프로비엠","시장": "KOSDAQ",
         "시가": 120000, "고가": 132000, "저가": 119000, "종가": 130000, "거래대금": 4_500_000_000_000},
        {"티커": "086520", "종목명": "에코프로",   "시장": "KOSDAQ",
         "시가": 90000,  "고가": 97000,  "저가": 89500,  "종가": 95000, "거래대금": 3_100_000_000_000},
        # 조건 미달 케이스
        {"티커": "051910", "종목명": "LG화학",     "시장": "KOSPI",
         "시가": 400000, "고가": 402000, "저가": 398000, "종가": 401000, "거래대금": 500_000_000_000},  # 등락률 낮음
        {"티커": "068270", "종목명": "셀트리온",   "시장": "KOSPI",
         "시가": 180000, "고가": 193000, "저가": 179000, "종가": 192000, "거래대금": 80_000_000_000},   # 거래대금 부족
    ]
    return pd.DataFrame(rows)


# ─── 메인 실행 ───────────────────────────────────────────────
def run_monitor(date, markets, output_csv=None, demo=False, source="pykrx"):
    print(cyan("=" * 62))
    print(cyan(f"  한국 주식 모니터링 | 날짜: {date}{'  [데모모드]' if demo else ''}"))
    print(cyan(f"  조건: 거래대금 {MIN_TRADING_VALUE/1e8:.0f}억 이상 + 장대 양봉"))
    print(cyan(f"        (등락률 ≥{MIN_CHANGE_RATE}%,  몸통비율 ≥{MIN_BODY_RATIO*100:.0f}%)"))
    print(cyan("=" * 62))
    print()

    all_results = []

    if demo:
        df_demo = build_demo_data()
        for market in markets:
            df_m = df_demo[df_demo["시장"] == market].copy()
            df_r = analyze(df_m, market)
            if not df_r.empty:
                all_results.append(df_r)
                print(f"  {market}: {green(f'{len(df_r)}개 종목 발견')} (데모)")
            else:
                print(f"  {market}: 조건에 맞는 종목 없음 (데모)")
    else:
        for market in markets:
            print(yellow(f"[{market}] 데이터 조회 중... (소스: {source})"))
            try:
                if source == "fdr":
                    df = fetch_via_fdr(date, market)
                else:
                    df = fetch_via_pykrx(date, market)

                if df.empty:
                    print(f"  데이터 없음 (휴장일이거나 API 오류)\n")
                    continue

                df_r = analyze(df, market)
                if df_r.empty:
                    print(f"  조건에 맞는 종목 없음\n")
                else:
                    all_results.append(df_r)
                    print(f"  {green(f'{len(df_r)}개 종목 발견')}\n")

            except ImportError as e:
                print(red(f"  라이브러리 오류: {e}"))
                print(red(f"  pip install pykrx  또는  pip install finance-datareader"))
            except Exception as e:
                print(red(f"  오류 발생: {e}"))

    if not all_results:
        print(red("\n조건에 맞는 종목이 없습니다."))
        return

    combined = pd.concat(all_results, ignore_index=True)
    combined = combined.sort_values("거래대금(억)", ascending=False).reset_index(drop=True)
    combined.index += 1

    display_cols = ["시장", "티커", "종목명", "시가", "고가", "저가",
                    "종가", "등락률(%)", "몸통비율(%)", "거래대금(억)"]
    display_df = combined[[c for c in display_cols if c in combined.columns]]

    print(cyan("=" * 62))
    print(cyan(f"  조건 충족 종목 ({len(display_df)}개)  —  거래대금 순 정렬"))
    print(cyan("=" * 62))

    if HAS_TABULATE:
        print(tabulate(display_df, headers="keys", tablefmt="pretty",
                       showindex=True, numalign="right"))
    else:
        print(display_df.to_string())

    if output_csv:
        combined.to_csv(output_csv, index=False, encoding="utf-8-sig")
        print(green(f"\nCSV 저장 완료: {output_csv}"))

    print(cyan(f"\n총 {len(display_df)}개 종목이 조건을 충족합니다.\n"))


# ─── CLI ────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="한국 주식 거래대금 2000억↑ + 장대 양봉 모니터링",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예시:
  python stock_monitor.py                          # 오늘(최근 영업일) KOSPI+KOSDAQ
  python stock_monitor.py -d 20250530              # 특정 날짜 조회
  python stock_monitor.py -m KOSPI                 # KOSPI만
  python stock_monitor.py -m KOSDAQ                # KOSDAQ만
  python stock_monitor.py -o result.csv            # CSV 파일 저장
  python stock_monitor.py --min-value 1000         # 거래대금 기준 1000억
  python stock_monitor.py --min-change 5           # 등락률 기준 5%
  python stock_monitor.py --source fdr             # FinanceDataReader 사용
  python stock_monitor.py --demo                   # 샘플 데이터로 데모 실행
""",
    )
    parser.add_argument("-d", "--date",       type=str,   default=None,
                        help="조회 날짜 YYYYMMDD (기본: 오늘)")
    parser.add_argument("-m", "--market",     type=str,   default="ALL",
                        choices=["ALL", "KOSPI", "KOSDAQ"],
                        help="시장 선택 (기본: ALL)")
    parser.add_argument("-o", "--output",     type=str,   default=None,
                        help="결과 CSV 저장 경로")
    parser.add_argument("--min-value",        type=float, default=2000,
                        help="최소 거래대금 억 원 (기본: 2000)")
    parser.add_argument("--min-change",       type=float, default=3.0,
                        help="최소 등락률 %% (기본: 3.0)")
    parser.add_argument("--body-ratio",       type=float, default=0.5,
                        help="최소 몸통비율 0~1 (기본: 0.5)")
    parser.add_argument("--source",           type=str,   default="pykrx",
                        choices=["pykrx", "fdr"],
                        help="데이터 소스: pykrx(기본) / fdr(FinanceDataReader)")
    parser.add_argument("--demo",             action="store_true",
                        help="샘플 데이터로 프로그램 구조 확인")

    args = parser.parse_args()

    global MIN_TRADING_VALUE, MIN_CHANGE_RATE, MIN_BODY_RATIO
    MIN_TRADING_VALUE = args.min_value * 1e8
    MIN_CHANGE_RATE   = args.min_change
    MIN_BODY_RATIO    = args.body_ratio

    date    = get_recent_trading_day(args.date)
    markets = ["KOSPI", "KOSDAQ"] if args.market == "ALL" else [args.market]

    run_monitor(date, markets, args.output, demo=args.demo, source=args.source)


if __name__ == "__main__":
    main()
