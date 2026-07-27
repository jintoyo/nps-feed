#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
외국인/기관 순매매 상위 수집기 (한국투자증권 KIS Open API 버전) → flows.json
============================================================================
KRX 익명 접속 차단(2026년 1월경 로그인 필수로 전환)과 네이버/다음 금융의
SPA 전환(비공식 JSON API 필요)으로 무료 스크래핑 경로가 막혀서,
공식 문서화된 한국투자증권 Open API로 교체합니다.

필요한 GitHub Secrets:
  KIS_APP_KEY     — KIS Developers 모의투자 앱키
  KIS_APP_SECRET  — KIS Developers 모의투자 앱시크릿
  (KIS_ACCT_NO 는 이 조회 API에는 필요 없지만, 다른 계좌 조회 기능을
   나중에 추가할 때 쓰려고 워크플로에 남겨둡니다)

  pip install requests
  python flows.py

첫 실행에서 파라미터 명세가 틀릴 가능성에 대비해, 예상과 다른 응답이 오면
"원본 응답을 그대로" 로그에 남깁니다 — 실패해도 다음 수정이 짐작이 아니라
로그 기반으로 정확히 한 번에 끝나도록 하기 위함입니다.
"""

from __future__ import annotations

import os
import json
import sys
import time
from datetime import datetime, timezone, timedelta

import requests

KST = timezone(timedelta(hours=9))
OUT = "flows.json"
TOP_N = 100

APP_KEY = os.environ.get("KIS_APP_KEY", "")
APP_SECRET = os.environ.get("KIS_APP_SECRET", "")

# 모의투자 도메인 (신청하신 앱키가 모의투자용이므로 이 도메인을 씁니다)
BASE = "https://openapivts.koreainvestment.com:29443"
UA = {"User-Agent": "Mozilla/5.0 economic-wire/1.0", "Content-Type": "application/json; charset=utf-8"}

# 국내기관_외국인 매매종목가집계 (국내주식-037)
# https://apiportal.koreainvestment.com/apiservice-apiservice?/uapi/domestic-stock/v1/quotations/foreign-institution-total=
TR_ID = "FHPTJ04400000"
_sample_printed = False
ENDPOINT = "/uapi/domestic-stock/v1/quotations/foreign-institution-total"


def get_token() -> str:
    """접근토큰 발급. 1분당 1회 제한 — 매시간 실행이라 문제 없음."""
    r = requests.post(
        f"{BASE}/oauth2/tokenP",
        headers=UA,
        json={"grant_type": "client_credentials", "appkey": APP_KEY, "appsecret": APP_SECRET},
        timeout=20,
    )
    r.raise_for_status()
    js = r.json()
    tok = js.get("access_token")
    if not tok:
        raise RuntimeError(f"토큰 발급 실패 — 응답 원문: {json.dumps(js, ensure_ascii=False)[:500]}")
    return tok


def call_ranking(token: str, *, mkt_div: str, rank_sort: str, etc_cls: str) -> list[dict]:
    """
    한 조합(시장/정렬기준/투자자구분)의 순위 목록을 가져온다.
    파라미터 후보가 문서와 다를 경우를 대비해, 실패 시 원문 응답을 그대로 노출한다.
    """
    headers = {
        **UA,
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": TR_ID,
        "custtype": "P",
    }
    params = {
        "fid_cond_mrkt_div_code": mkt_div,       # V: 전체(추정) — 실패 시 로그로 원인 확인
        "fid_cond_scr_div_code": "16449",
        "fid_input_iscd": "0000",                 # 0000: 전체 종목
        "fid_div_cls_code": "1",                   # 0:수량 1:금액
        "fid_rank_sort_cls_code": rank_sort,        # 0:순매수상위 1:순매도상위
        "fid_etc_cls_code": etc_cls,                # 0:외국인 1:기관계 (추정)
    }
    r = requests.get(f"{BASE}{ENDPOINT}", headers=headers, params=params, timeout=20)
    try:
        js = r.json()
    except ValueError:
        print(f"  ! JSON 아님 (HTTP {r.status_code}): {r.text[:300]}", file=sys.stderr)
        return []

    rt_cd = js.get("rt_cd")
    if rt_cd != "0":
        print(f"  ! API 오류 rt_cd={rt_cd} msg={js.get('msg1')} "
              f"— 원본: {json.dumps(js, ensure_ascii=False)[:400]}", file=sys.stderr)
        return []

    for key in ("output", "output1", "output2"):
        rows = js.get(key)
        if isinstance(rows, list) and rows:
            global _sample_printed
            if not _sample_printed:
                print(f"  · [진단] 원본 행 샘플(mkt={mkt_div},rank={rank_sort},etc={etc_cls}): "
                      f"{json.dumps(rows[0], ensure_ascii=False)}")
                _sample_printed = True
            return rows
    print(f"  ! 목록 없음 — 응답 키: {list(js.keys())} / 원본 일부: "
          f"{json.dumps(js, ensure_ascii=False)[:400]}", file=sys.stderr)
    return []


def pick_num(row: dict, *names):
    for n in names:
        if n in row:
            try:
                return float(str(row[n]).replace(",", ""))
            except (TypeError, ValueError):
                continue
    return None


import re as _re

def auto_amount_field(rows: list[dict]) -> str | None:
    """
    금액 필드명을 짐작하지 않고, 실제 응답에서 스스로 찾는다.
    후보 조건: 필드명에 금액/순매수 관련 키워드가 있고, 상위 몇 행에서 값이
    크고 0이 아니며, 대체로 내림차순(순위표이므로)인 필드를 고른다.
    """
    if not rows:
        return None
    keys = set()
    for r in rows[:5]:
        keys |= set(r.keys())
    cand = [k for k in keys if _re.search(r"(pbmn|amt|tr_pbmn|ntby)", k, _re.I)]
    best, best_score = None, -1
    for k in cand:
        vals = []
        for r in rows[:10]:
            try:
                vals.append(abs(float(str(r.get(k, "0")).replace(",", ""))))
            except (TypeError, ValueError):
                vals.append(0)
        nonzero = sum(1 for v in vals if v > 0)
        magnitude = sum(vals)
        score = nonzero * 1_000_000_000 + magnitude  # 0이 아닌 개수 우선, 그다음 크기
        if score > best_score:
            best, best_score = k, score
    if best:
        print(f"  · [자동감지] 금액 필드 추정: '{best}' "
              f"(샘플값: {[r.get(best) for r in rows[:3]]})")
    return best


def pick_str(row: dict, *names):
    for n in names:
        if n in row and str(row[n]).strip():
            return str(row[n]).strip()
    return None


def normalize(rows: list[dict]) -> list[dict]:
    """행 하나를 {n(종목명), v(순매매금액, 억원)} 로 정규화."""
    out = []
    amt_key = auto_amount_field(rows)
    for r in rows:
        name = pick_str(r, "hts_kor_isnm", "isnm", "name")
        amt = pick_num(r, amt_key) if amt_key else None
        if amt is None:
            amt = pick_num(r, "ntby_tr_pbmn", "ntby_trad_pbmn", "frgn_ntby_tr_pbmn",
                            "ntby_qty_pbmn", "ntby_prsm_amt")
        if name is None or amt is None:
            continue
        out.append({"n": name, "v": round(amt / 1e8, 1)})  # 원 → 억원 추정
    return out[:TOP_N]


def collect_investor(token: str, etc_cls: str) -> dict:
    buy_rows = call_ranking(token, mkt_div="V", rank_sort="0", etc_cls=etc_cls)
    time.sleep(0.3)
    sell_rows = call_ranking(token, mkt_div="V", rank_sort="1", etc_cls=etc_cls)
    return {"buy": normalize(buy_rows), "sell": normalize(sell_rows)}


def main() -> int:
    if not APP_KEY or not APP_SECRET:
        print("  ! KIS_APP_KEY / KIS_APP_SECRET 시크릿이 없습니다 — 종료합니다.", file=sys.stderr)
        return 0

    print("외국인/기관 순매매 수집 시작 (KIS Open API)")
    try:
        token = get_token()
    except Exception as e:  # noqa: BLE001
        print(f"  ! 토큰 발급 실패: {e}", file=sys.stderr)
        return 0

    out: dict = {}
    for key, etc_cls in (("foreign", "0"), ("inst", "1")):
        try:
            d = collect_investor(token, etc_cls)
        except Exception as e:  # noqa: BLE001
            print(f"  ! {key} 수집 실패: {type(e).__name__}: {e}", file=sys.stderr)
            d = {"buy": [], "sell": []}
        out[key] = d
        print(f"  · {key}: 순매수 {len(d['buy'])} / 순매도 {len(d['sell'])}종목")

    total = sum(len(out[k][s]) for k in out for s in ("buy", "sell"))
    if total == 0:
        print("  ! 수집된 데이터가 없습니다 — 기존 파일을 보존하고 종료합니다.")
        print("  ! 위 로그의 '원본' 부분을 그대로 공유해 주시면 파라미터를 바로잡겠습니다.")
        return 0

    payload = {
        "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
        "date": datetime.now(KST).strftime("%Y%m%d"),
        "source": "kis",
        **out,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    print(f"저장 → {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
