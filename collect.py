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
  NPS_DAYS       (선택)  조회 일수, 기본 45 (약식보고가 매월 초에 몰림)
  NPS_THRESHOLD  (선택)  1차 임계치 %p, 기본 1.00 = 자본시장법 §147 변동보고 기준
  NPS_STRONG     (선택)  2차 임계치 %p, 기본 3.00 (적극매수/당장매도)

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
import math
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


_MAJOR_CACHE: dict = {}


def nps_position(key: str, corp_code: str, rcept_no: str):
    """발행회사의 대량보유 상황보고에서 국민연금 보유비율/주식수를 찾는다.
    같은 회사를 여러 번 조회하지 않도록 회사 단위로 캐시한다(역산 시 필수)."""
    if corp_code not in _MAJOR_CACHE:
        try:
            js = api("majorstock.json", key, corp_code=corp_code)
            _MAJOR_CACHE[corp_code] = (js.get("list", [])
                                       if js.get("status") == "000" else [])
            time.sleep(SLEEP)
        except requests.RequestException:
            _MAJOR_CACHE[corp_code] = []
    rows = [r for r in _MAJOR_CACHE[corp_code]
            if "국민연금" in str(r.get("repror", ""))]
    if not rows:
        return None, None
    exact = [r for r in rows if str(r.get("rcept_no", "")) == str(rcept_no)]
    row = exact[0] if exact else sorted(rows, key=lambda r: str(r.get("rcept_dt", "")))[-1]
    return to_num(row.get("stkrt")), to_num(row.get("stkqy"))


# ── 판정 ──────────────────────────────────────────────────────────
def bp(x) -> int:
    """% 값을 0.01%p 단위 정수로. 부동소수점 오차를 제거해 엑셀과 결과를 일치시킨다.
    JS Math.round 와 동일한 half-up 방식(파이썬 기본 round 는 banker's rounding)."""
    v = (float(x) if x is not None else 0.0) * 100
    return math.floor(v + 0.5) if v >= 0 else math.ceil(v - 0.5)


def judge(rate, base, mild: float, strong: float):
    """4단계 판정: 적극매수 / 매수 / 유지 / 매도 / 당장매도 (+ 신규·청산).

    임계치 기준점은 자본시장법 §147 변동보고 의무인 1%p.
    반환값 (판정, 절대증감 %p, 상대증감 %) — 둘 다 원본 보존용으로 함께 돌려준다.
    """
    if rate is None:
        return "확인필요", None, None
    if base is None:
        return ("청산" if rate == 0 else "신규"), None, None

    d_bp = bp(rate) - bp(base)
    b_bp = bp(base)
    r_bp = (math.floor(d_bp * 10000 / b_bp + 0.5) if d_bp >= 0
            else math.ceil(d_bp * 10000 / b_bp - 0.5)) if b_bp else None

    delta = d_bp / 100
    rel = r_bp / 100 if r_bp is not None else None

    if rate == 0:
        return "청산", delta, rel
    m, s_ = bp(mild), bp(strong)
    if d_bp >= s_:
        return "적극매수", delta, rel
    if d_bp >= m:
        return "매수", delta, rel
    if d_bp <= -s_:
        return "당장매도", delta, rel
    if d_bp <= -m:
        return "매도", delta, rel
    return "유지", delta, rel


HISTORY_PATH = "nps-history.json"


def load_history() -> dict:
    """종목별 공시 이력. 없으면 빈 구조 — 이때 첫 실행이 자동으로 1년치를 역산한다."""
    if os.path.exists(HISTORY_PATH):
        with open(HISTORY_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"filings": {}}


def save_history(h: dict) -> None:
    h["updated_at"] = datetime.now(KST).isoformat(timespec="seconds")
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(h, f, ensure_ascii=False, indent=1)


def prev_of(history: dict, corp: str, rcept_no: str, date: str):
    """같은 종목의 직전 공시(현재 건 제외, 날짜가 앞선 것 중 최신)를 찾는다."""
    cand = [v for v in history["filings"].values()
            if norm(v.get("corp", "")) == norm(corp)
            and str(v.get("rcept_no")) != str(rcept_no)
            and str(v.get("date", "")) < str(date)]
    if not cand:
        return None
    return max(cand, key=lambda v: str(v.get("date", "")))


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
    ap.add_argument("--days", type=int, default=int(os.environ.get("NPS_DAYS", 45)))
    ap.add_argument("--threshold", type=float,
                    default=float(os.environ.get("NPS_THRESHOLD", 1.00)),
                    help="1차 임계치 %%p — 매수/매도 경계")
    ap.add_argument("--strong", type=float,
                    default=float(os.environ.get("NPS_STRONG", 3.00)),
                    help="2차 임계치 %%p — 적극매수/당장매도 경계")
    ap.add_argument("--baseline", default="baseline_1q.json")
    ap.add_argument("--out", default="nps-latest.json")
    ap.add_argument("--csv", default="", help="CSV도 함께 저장하려면 경로 지정")
    args = ap.parse_args()

    history = load_history()
    if not history["filings"]:
        print("  · 이력 파일 없음 → 최초 1회 자동 역산: 조회 기간을 365일로 확장")
        args.days = max(args.days, 365)

    end = date.today()
    bgn = end - timedelta(days=max(args.days - 1, 0))
    bgn_s, end_s = f"{bgn:%Y%m%d}", f"{end:%Y%m%d}"
    print(f"조회 {bgn_s} ~ {end_s} · 임계치 ±{args.threshold}%p / ±{args.strong}%p")

    baseline = load_baseline(args.baseline)

    # DART 제약: corp_code 없는 목록 조회는 3개월까지만 허용 (status 100)
    # → 80일 단위로 나눠 조회하면 어떤 달 조합에서도 제한에 걸리지 않는다
    every, w_end = [], end
    while w_end >= bgn:
        w_bgn = max(bgn, w_end - timedelta(days=79))
        chunk = list_filings(key, f"{w_bgn:%Y%m%d}", f"{w_end:%Y%m%d}")
        print(f"  · {w_bgn:%Y%m%d}~{w_end:%Y%m%d}: {len(chunk)}건")
        every.extend(chunk)
        w_end = w_bgn - timedelta(days=1)
        time.sleep(SLEEP)
    print(f"  · 대량보유상황보고서 전체 {len(every)}건")

    # ★ 안전장치 1: 목록 자체를 못 받았으면 아무 파일도 건드리지 않는다
    if not every:
        print("  ! 목록 조회 결과 0건 — DART 오류 또는 호출 한도(status 020) 가능성.")
        print("  ! 기존 nps-latest.json / nps-history.json 을 보존하고 종료합니다.")
        return 0

    mine = [r for r in every if "국민연금" in str(r.get("flr_nm", ""))]
    print(f"  · 제출인 '국민연금' 포함 {len(mine)}건")

    if not mine and every:
        from collections import Counter
        pens = sorted({str(r.get("flr_nm", "")) for r in every
                       if "연금" in str(r.get("flr_nm", ""))})
        print(f"  · [진단] '연금' 포함 제출인: {pens if pens else '없음'}")
        top = Counter(str(r.get("flr_nm", "")) for r in every).most_common(8)
        print("  · [진단] 제출인 상위: " + ", ".join(f"{n}({c})" for n, c in top))
        print("  · [진단] 이 기간에 국민연금 보고가 없었을 수 있습니다. "
              "--days 를 60~90으로 늘려 다시 확인하세요.")

    # ★ 안전장치 2: 국민연금 공시가 0건이면 이전의 정상 피드를 보존한다
    if not mine:
        print("  · 이 기간에 국민연금 공시 없음 — 기존 피드를 보존하고 종료합니다.")
        return 0

    # 접수번호 기준 중복 제거 (분할 조회 경계 등 어떤 경우에도 한 건은 한 번만)
    seen_rcpt = set()
    uniq = []
    for r in mine:
        rc = str(r.get("rcept_no", ""))
        if rc and rc in seen_rcpt:
            continue
        seen_rcpt.add(rc)
        uniq.append(r)
    if len(uniq) != len(mine):
        print(f"  · 중복 제거: {len(mine)} → {len(uniq)}건")
    mine = uniq

    filings = []
    for i, r in enumerate(mine, 1):
        corp = (r.get("corp_name") or "").strip()
        rcept = str(r.get("rcept_no") or "")
        rate, qty = nps_position(key, r.get("corp_code", ""), rcept)

        b = baseline.get(norm(corp))
        base_rate = b["rate"] if b else None
        verdict, delta, rel = judge(rate, base_rate, args.threshold, args.strong)

        dt = r.get("rcept_dt", "")
        # 이력 누적 (접수번호로 중복 방지) — 직전 값은 전체 적재 후 2단계에서 계산
        if rcept:
            history["filings"][rcept] = {
                "corp": corp, "rcept_no": rcept, "date": dt,
                "rate": rate, "qty": qty,
            }

        filings.append({
            "corp": corp,
            "rcept_no": rcept,
            "date": dt,
            "report": (r.get("report_nm") or "").strip(),
            "filer": r.get("flr_nm", ""),
            "rate": rate,
            "qty": qty,
            "base": base_rate,
            "delta": delta,
            "delta_rel": rel,
            "prev": None,
            "prev_date": None,
            "delta_prev": None,
            "verdict": verdict,
            "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept}" if rcept else "",
        })
        print(f"    [{i}/{len(mine)}] {corp:<16} "
              f"{'' if rate is None else f'{rate:6.2f}%'}  {verdict}")
        time.sleep(SLEEP)

    # 2단계: 이력이 전부 채워진 뒤 각 공시의 직전 보고를 계산 — 처리 순서에 무관
    for f in filings:
        pv = prev_of(history, f["corp"], f["rcept_no"], f["date"])
        if pv and pv.get("rate") is not None:
            f["prev"] = pv["rate"]
            f["prev_date"] = pv.get("date")
            if f["rate"] is not None:
                f["delta_prev"] = (bp(f["rate"]) - bp(pv["rate"])) / 100
    with_prev = sum(1 for f in filings if f["prev"] is not None)
    print(f"  · 직전 공시 연결: {with_prev}/{len(filings)}건")

    # ★ 안전장치 3: 빈 이력으로 기존 이력을 덮어쓰지 않는다
    hist_n = len(history["filings"])
    if hist_n:
        save_history(history)
    else:
        print("  ! 이력에 저장할 내용이 없어 파일을 만들지 않습니다.")
    print(f"  · 이력 누적 {hist_n}건 → {HISTORY_PATH}")

    # 역산 실행이어도 대시보드 피드는 최근 45일 공시만 담는다
    cutoff = f"{end - timedelta(days=44):%Y%m%d}"
    recent = [f for f in filings if str(f["date"]) >= cutoff]
    if len(recent) != len(filings):
        print(f"  · 피드에는 최근 45일 {len(recent)}건만 수록 (이력에는 전체 보존)")
    filings = recent

    payload = {
        "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
        "period": {"from": bgn_s, "to": end_s},
        "threshold": args.threshold,
        "threshold_strong": args.strong,
        "baseline_label": "2026 1Q",
        "baseline": {v["name"]: v["rate"] for v in baseline.values()},
        "history_count": hist_n,
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
