#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
국민연금공단 대량보유상황보고서 수집기
=====================================
DART OpenAPI에서 '주식등의 대량보유상황보고서(D001)' 중 제출인이 국민연금공단인
공시만 골라, 보유비율과 1분기 기준값 대비 매수/매도 판정까지 붙여
nps-latest.json 을 만듭니다. 대시보드는 이 파일 주소만 보고 있으면 됩니다.

환경변수
  DART_API_KEY   (필수)  https://opendart.fss.or.kr 무료 발급
  NPS_DAYS       (선택)  조회 일수, 기본 7
  NPS_THRESHOLD  (선택)  판정 임계치 %p, 기본 0.10

입력  baseline_1q.json   {"삼성전자": 7.28, "SK하이닉스": 8.11}
출력  nps-latest.json
"""

from __future__ import annotations

import os
import re
import csv
import sys
import json
import time
import argparse
from datetime import date, datetime, timedelta, timezone

import requests

BASE_URL = "https://opendart.fss.or.kr/api"
FILER = "국민연금공단"
DETAIL_TY = "D001"           # 주식등의 대량보유상황보고서
TIMEOUT = 20
SLEEP = 0.12                 # DART 호출 제한 여유
KST = timezone(timedelta(hours=9))


# ── 유틸 ──────────────────────────────────────────────────────────
def norm(name: str) -> str:
    return re.sub(r"\(주\)|주식회사|㈜|\s+", "", str(name or "")).strip()


def to_num(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def api(path: str, key: str, **params) -> dict:
    params["crtfc_key"] = key
    r = requests.get(f"{BASE_URL}/{path}", params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


# ── DART 조회 ─────────────────────────────────────────────────────
def list_filings(key: str, bgn: str, end: str) -> list[dict]:
    """기간 내 대량보유상황보고서를 페이지 순회로 모두 가져온다."""
    out, page = [], 1
    while True:
        try:
            js = api("list.json", key, bgn_de=bgn, end_de=end,
                     pblntf_detail_ty=DETAIL_TY, page_no=page, page_count=100)
        except requests.RequestException as e:
            print(f"  ! list.json 실패: {e}", file=sys.stderr)
            break

        status = js.get("status")
        if status == "013":                       # 결과 없음
            break
        if status != "000":
            print(f"  ! DART status {status}: {js.get('message')}", file=sys.stderr)
            break

        out.extend(js.get("list", []))
        if page >= int(js.get("total_page", 1) or 1):
            break
        page += 1
        time.sleep(SLEEP)
    return out


def nps_position(key: str, corp_code: str, rcept_no: str):
    """발행회사의 대량보유 상황보고에서 국민연금 보유비율/주식수를 찾는다."""
    try:
        js = api("majorstock.json", key, corp_code=corp_code)
    except requests.RequestException:
        return None, None
    if js.get("status") != "000":
        return None, None

    rows = [r for r in js.get("list", []) if "국민연금" in str(r.get("repror", ""))]
    if not rows:
        return None, None

    exact = [r for r in rows if str(r.get("rcept_no", "")) == str(rcept_no)]
    row = exact[0] if exact else sorted(rows, key=lambda r: str(r.get("rcept_dt", "")))[-1]
    return to_num(row.get("stkrt")), to_num(row.get("stkqy"))


# ── 판정 ──────────────────────────────────────────────────────────
def judge(rate, base, thr: float) -> tuple[str, float | None]:
    if rate is None:
        return "확인필요", None
    if base is None:
        return ("청산", None) if rate == 0 else ("신규", None)
    delta = round(rate - base, 4)
    if rate == 0:
        return "청산", delta
    if delta >= thr:
        return "매수", delta
    if delta <= -thr:
        return "매도", delta
    return "유지", delta


def load_baseline(path: str) -> dict:
    if not os.path.exists(path):
        print(f"  · 기준값 파일 없음({path}) — 전부 '신규'로 표시됩니다.")
        return {}
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    out = {}
    for k, v in raw.items():
        rate = to_num(v.get("rate") if isinstance(v, dict) else v)
        if rate is not None:
            out[norm(k)] = {"name": k, "rate": rate}
    print(f"  · 기준값 {len(out)}종목 로드")
    return out


# ── 메인 ──────────────────────────────────────────────────────────
def main() -> int:
    key = os.environ.get("DART_API_KEY", "").strip()
    if not key:
        print("DART_API_KEY 가 없습니다. https://opendart.fss.or.kr 에서 발급하세요.",
              file=sys.stderr)
        return 1

    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=int(os.environ.get("NPS_DAYS", 7)))
    ap.add_argument("--threshold", type=float,
                    default=float(os.environ.get("NPS_THRESHOLD", 0.10)))
    ap.add_argument("--baseline", default="baseline_1q.json")
    ap.add_argument("--out", default="nps-latest.json")
    ap.add_argument("--csv", default="", help="CSV도 함께 저장하려면 경로 지정")
    args = ap.parse_args()

    end = date.today()
    bgn = end - timedelta(days=max(args.days - 1, 0))
    bgn_s, end_s = f"{bgn:%Y%m%d}", f"{end:%Y%m%d}"
    print(f"조회 {bgn_s} ~ {end_s} · 임계치 ±{args.threshold}%p")

    baseline = load_baseline(args.baseline)

    every = list_filings(key, bgn_s, end_s)
    print(f"  · 대량보유상황보고서 전체 {len(every)}건")

    mine = [r for r in every if FILER in str(r.get("flr_nm", ""))]
    print(f"  · 제출인 '{FILER}' {len(mine)}건")

    filings = []
    for i, r in enumerate(mine, 1):
        corp = (r.get("corp_name") or "").strip()
        rcept = str(r.get("rcept_no") or "")
        rate, qty = nps_position(key, r.get("corp_code", ""), rcept)

        b = baseline.get(norm(corp))
        base_rate = b["rate"] if b else None
        verdict, delta = judge(rate, base_rate, args.threshold)

        filings.append({
            "corp": corp,
            "rcept_no": rcept,
            "date": r.get("rcept_dt", ""),
            "report": (r.get("report_nm") or "").strip(),
            "filer": r.get("flr_nm", ""),
            "rate": rate,
            "qty": qty,
            "base": base_rate,
            "delta": delta,
            "verdict": verdict,
            "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept}" if rcept else "",
        })
        print(f"    [{i}/{len(mine)}] {corp:<16} "
              f"{'' if rate is None else f'{rate:6.2f}%'}  {verdict}")
        time.sleep(SLEEP)

    payload = {
        "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
        "period": {"from": bgn_s, "to": end_s},
        "threshold": args.threshold,
        "baseline_label": "2026 1Q",
        "baseline": {v["name"]: v["rate"] for v in baseline.values()},
        "count": len(filings),
        "filings": filings,
    }

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"\n저장 → {args.out} ({len(filings)}건)")

    if args.csv and filings:
        with open(args.csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(filings[0].keys()))
            w.writeheader()
            w.writerows(filings)
        print(f"저장 → {args.csv}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
