#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
핵심 지표 수집기 → indicators.json
==================================
야후 파이낸스 공개 시세 API + CNN 공포&탐욕 + WGB(한국 CDS)에서
10개 지표를 가져와 대시보드가 읽을 JSON 을 만듭니다.
GitHub Actions 에서 매시간 실행되며, 브라우저 CORS 제약을 받지 않습니다.

  pip install requests
  python indicators.py
"""

from __future__ import annotations

import re
import json
import math
import sys
import time
from datetime import datetime, timezone, timedelta

import requests

KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) economic-wire/1.0"}
OUT = "indicators.json"


def rnd(x, d=2):
    if x is None:
        return None
    try:
        v = float(x)
        if math.isnan(v):
            return None
        return round(v, d)
    except (TypeError, ValueError):
        return None


def yahoo(sym: str):
    """야후 차트 API에서 (현재가, 전일종가)를 가져온다. query1 실패 시 query2 재시도."""
    for host in ("query1", "query2"):
        try:
            r = requests.get(
                f"https://{host}.finance.yahoo.com/v8/finance/chart/{sym}",
                params={"range": "5d", "interval": "1d"},
                headers=UA, timeout=15)
            r.raise_for_status()
            res = r.json()["chart"]["result"][0]
            meta = res["meta"]
            closes = [c for c in res["indicators"]["quote"][0].get("close", [])
                      if c is not None]
            cur = meta.get("regularMarketPrice")
            if cur is None and closes:
                cur = closes[-1]
            # 직전 거래일 종가: 종가 배열의 끝에서 두 번째
            # (chartPreviousClose 는 조회구간 시작 전 종가라 전일비 계산에 부적합)
            prev = closes[-2] if len(closes) >= 2 else meta.get("chartPreviousClose")
            if cur is not None:
                return float(cur), (float(prev) if prev is not None else None)
        except Exception as e:  # noqa: BLE001 — 지표 하나 실패해도 나머지는 계속
            print(f"  ! yahoo {sym} ({host}): {e}", file=sys.stderr)
            time.sleep(1)
    return None, None


def pct(cur, prev):
    if cur is None or prev in (None, 0):
        return None
    return (cur / prev - 1) * 100


def fear_greed():
    try:
        r = requests.get(
            "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
            headers=UA, timeout=15)
        r.raise_for_status()
        return rnd(r.json()["fear_and_greed"]["score"], 0)
    except Exception as e:  # noqa: BLE001
        print(f"  ! CNN F&G: {e}", file=sys.stderr)
        return None


def korea_cds():
    """WGB 페이지에서 한국 5년물 CDS(bp)를 추출. 구조가 바뀌면 None."""
    try:
        r = requests.get(
            "https://www.worldgovernmentbonds.com/cds-historical-data/south-korea/5-years/",
            headers=UA, timeout=20)
        r.raise_for_status()
        # "South Korea 5 Years CDS value is XX.XX" 류의 첫 수치를 찾는다
        m = re.search(r"CDS[^0-9]{0,40}(\d{1,3}(?:\.\d{1,2})?)\s*(?:bp|basis|&nbsp)?",
                      r.text, re.I)
        return rnd(m.group(1)) if m else None
    except Exception as e:  # noqa: BLE001
        print(f"  ! Korea CDS: {e}", file=sys.stderr)
        return None


def main() -> int:
    print("지표 수집 시작")
    out: dict = {}

    # ── 야후 시세 계열 ────────────────────────────────
    tnx_c, tnx_p = yahoo("^TNX")            # 10년물 금리 ×10
    out["us10y"] = {"v": rnd(tnx_c / 10 if tnx_c else None),
                    "chg": rnd((tnx_c - tnx_p) / 10 if tnx_c and tnx_p else None)}

    dxy_c, dxy_p = yahoo("DX-Y.NYB")
    out["dxy"] = {"v": rnd(dxy_c),
                  "chg": rnd(dxy_c - dxy_p if dxy_c and dxy_p else None)}

    krw_c, krw_p = yahoo("KRW=X")
    out["usdkrw"] = {"v": rnd(krw_c),
                     "chg": rnd(krw_c - krw_p if krw_c and krw_p else None)}

    vix_c, _ = yahoo("^VIX")
    out["vix"] = {"v": rnd(vix_c)}

    btc_c, btc_p = yahoo("BTC-USD")
    out["btc"] = {"v": rnd(btc_c, 0), "chg": rnd(pct(btc_c, btc_p))}

    sp_c, sp_p = yahoo("ES=F")
    nq_c, nq_p = yahoo("NQ=F")
    out["futures"] = {"sp": rnd(pct(sp_c, sp_p)), "nq": rnd(pct(nq_c, nq_p))}

    wti_c, wti_p = yahoo("CL=F")
    out["wti"] = {"v": rnd(wti_c), "chg": rnd(pct(wti_c, wti_p))}

    leaders = []
    for t in ("NVDA", "TSLA"):
        c, p = yahoo(t)
        leaders.append({"t": t, "chg": rnd(pct(c, p))})
    out["leaders"] = {"list": leaders}

    # ── 야후 밖 소스 ─────────────────────────────────
    out["fng"] = {"v": fear_greed()}
    out["cds"] = {"v": korea_cds()}

    out["generated_at"] = datetime.now(KST).isoformat(timespec="seconds")

    ok = sum(1 for k, v in out.items()
             if k not in ("generated_at",) and any(
                 x is not None for x in (v.get("v"), v.get("chg"),
                                         v.get("sp"), v.get("nq")))
             or (k == "leaders" and any(i["chg"] is not None for i in v["list"])))
    print(f"  · 값이 채워진 지표: {ok}/10")
    for k, v in out.items():
        if k != "generated_at":
            print(f"    {k}: {json.dumps(v, ensure_ascii=False)}")

    if ok == 0:
        print("  ! 모든 지표 수집 실패 — 기존 파일을 보존하고 종료합니다.")
        return 0

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"저장 → {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
