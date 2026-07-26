#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
외국인/기관 순매매 상위 수집기 → flows.json
==========================================
KRX 정보데이터시스템 공식 통계(투자자별 순매수 종목, MDCSTAT02401)에서
당일(장 마감 전이면 직전 거래일) 기준으로
  · 외국인 순매수 상위 100 / 순매도 상위 100
  · 기관합계 순매수 상위 100 / 순매도 상위 100
을 코스피+코스닥 합산으로 만듭니다. 금액 단위는 억원.

  pip install requests
  python flows.py
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, date, timedelta, timezone

import requests

KST = timezone(timedelta(hours=9))
OUT = "flows.json"
URL = "http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) economic-wire/1.0",
    "Referer": "http://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd",
}
INVESTORS = {"foreign": "9000", "inst": "7050"}   # 외국인 / 기관합계
MARKETS = ("STK", "KSQ")                          # 코스피 / 코스닥
TOP_N = 100


def num(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def krx_rows(trd_dd: str, invst: str, mkt: str) -> list:
    """해당 일자·투자자·시장의 종목별 순매수 목록."""
    data = {
        "bld": "dbms/MDC/STAT/standard/MDCSTAT02401",
        "mktId": mkt,
        "invstTpCd": invst,
        "strtDd": trd_dd,
        "endDd": trd_dd,
        "share": "1",
        "money": "1",
        "csvxls_isNo": "false",
    }
    r = requests.post(URL, data=data, headers=HEADERS, timeout=25)
    r.raise_for_status()
    js = r.json()
    for key in ("output", "OutBlock_1", "block1"):
        if isinstance(js.get(key), list):
            return js[key]
    return []


def pick(row: dict, *names):
    for n in names:
        if n in row and str(row[n]).strip() not in ("", "-"):
            return row[n]
    return None


def collect_day(trd_dd: str) -> dict | None:
    """하루치 4개 목록. 데이터가 없는 날(휴장)이면 None."""
    out = {}
    for inv_key, inv_cd in INVESTORS.items():
        merged: dict[str, dict] = {}
        for mkt in MARKETS:
            rows = krx_rows(trd_dd, inv_cd, mkt)
            time.sleep(0.4)
            for row in rows:
                code = str(pick(row, "ISU_SRT_CD", "ISU_CD") or "").strip()
                name = str(pick(row, "ISU_ABBRV", "ISU_NM") or "").strip()
                netv = num(pick(row, "NETBID_TRDVAL", "NETBID_TRD_VAL"))
                if not name or netv is None:
                    continue
                # 같은 종목이 두 시장에 있을 일은 없지만, 방어적으로 합산
                if code in merged:
                    merged[code]["raw"] += netv
                else:
                    merged[code] = {"n": name, "raw": netv}
        if not merged:
            return None
        ranked = sorted(merged.values(), key=lambda x: x["raw"], reverse=True)
        to_eok = lambda x: round(x / 1e8, 1)          # 원 → 억원
        buy = [{"n": r["n"], "v": to_eok(r["raw"])}
               for r in ranked if r["raw"] > 0][:TOP_N]
        sell = [{"n": r["n"], "v": to_eok(r["raw"])}
                for r in reversed(ranked) if r["raw"] < 0][:TOP_N]
        out[inv_key] = {"buy": buy, "sell": sell}
        print(f"  · {inv_key}: 순매수 {len(buy)} / 순매도 {len(sell)}종목 "
              f"(1위 {buy[0]['n']} +{buy[0]['v']}억)" if buy else f"  · {inv_key}: 0건")
    return out


def main() -> int:
    print("외국인/기관 순매매 수집 시작")
    d = datetime.now(KST).date()
    for back in range(8):                      # 오늘부터 최대 8일 거슬러 영업일 탐색
        trd = d - timedelta(days=back)
        if trd.weekday() >= 5:                 # 주말 건너뜀
            continue
        trd_dd = f"{trd:%Y%m%d}"
        print(f"  · {trd_dd} 시도")
        try:
            day = collect_day(trd_dd)
        except Exception as e:                 # noqa: BLE001
            print(f"  ! KRX 요청 실패: {e}", file=sys.stderr)
            day = None
        if day:
            payload = {
                "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
                "date": trd_dd,
                **day,
            }
            with open(OUT, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
            print(f"저장 → {OUT} (기준일 {trd_dd})")
            return 0
    print("  ! 8일 내 거래일 데이터를 찾지 못했습니다 — 기존 파일을 보존하고 종료합니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
