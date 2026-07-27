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
URLS = (
    "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd",
    "http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd",
)
WARMUP = "https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201020304"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Referer": "https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201020304",
    "Origin": "https://data.krx.co.kr",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "ko-KR,ko;q=0.9",
    "X-Requested-With": "XMLHttpRequest",
}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)
_warmed = False


def warm_up():
    """첫 호출 전에 실제 화면을 한 번 방문해 세션 쿠키를 받는다."""
    global _warmed
    if _warmed:
        return
    try:
        r = SESSION.get(WARMUP, timeout=20)
        print(f"  · 워밍업: HTTP {r.status_code}, 쿠키 {len(SESSION.cookies)}개")
    except Exception as e:  # noqa: BLE001
        print(f"  · 워밍업 실패(계속 진행): {type(e).__name__}: {e}", file=sys.stderr)
    _warmed = True


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
    warm_up()
    last_err = None
    for url in URLS:
        try:
            r = SESSION.post(url, data=data, timeout=25)
            r.raise_for_status()
            ct = r.headers.get("Content-Type", "")
            if "json" not in ct and "javascript" not in ct:
                print(f"    - {mkt}/{invst} @ {url.split('/')[2]}: JSON 아님 "
                      f"(Content-Type={ct}, 응답 앞부분: {r.text[:80]!r})", file=sys.stderr)
                last_err = RuntimeError(f"비정상 응답({ct})")
                continue
            js = r.json()
            for key in ("output", "OutBlock_1", "block1"):
                if isinstance(js.get(key), list):
                    print(f"    - {mkt}/{invst}: {len(js[key])}행 수신")
                    return js[key]
            print(f"    - {mkt}/{invst}: 응답에 목록 없음 · 키={list(js.keys())[:6]}")
            return []
        except Exception as e:  # noqa: BLE001
            last_err = e
            print(f"    - {mkt}/{invst} @ {url.split('/')[2]}: "
                  f"{type(e).__name__}: {e}", file=sys.stderr)
    raise RuntimeError(f"KRX 접속 불가 — 마지막 오류: {type(last_err).__name__}: {last_err}")


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
                # 같은 종목이 중복 수신되면 첫 값만 사용 (이중 합산 방지)
                if code not in merged:
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




# ── 폴백: 네이버 금융 (KRX 가 해외 IP 를 막을 때) ──────────────
NAVER_BASE = "https://finance.naver.com"
NAVER_HEADERS = {"User-Agent": HEADERS["User-Agent"],
                 "Referer": "https://finance.naver.com/sise/",
                 "Accept-Language": "ko-KR,ko;q=0.9"}


def naver_get(path: str) -> str:
    r = requests.get(NAVER_BASE + path, headers=NAVER_HEADERS, timeout=20)
    r.raise_for_status()
    r.encoding = "euc-kr"
    return r.text


def naver_discover() -> dict:
    """시세 메인에서 외국인/기관 매매 상위 페이지 링크를 찾아낸다."""
    import re as _re
    html = naver_get("/sise/")
    links = _re.findall(r'href="(/sise/[^"]+)"[^>]*>([^<]{2,20})<', html)
    found = {}
    for href, text in links:
        t = text.strip()
        if "외국인" in t and ("매매" in t or "순매" in t):
            found.setdefault("foreign", href.replace("&amp;", "&"))
        if "기관" in t and ("매매" in t or "순매" in t):
            found.setdefault("inst", href.replace("&amp;", "&"))
    print(f"  · [네이버] 발견한 링크: {found or '없음'}")
    return found


def naver_parse_tables(html: str) -> list:
    """페이지의 모든 표를 (헤더, 행들)로 뽑는다. 구조를 로그로 남긴다."""
    import re as _re
    out = []
    for tb in _re.findall(r"<table[^>]*>([\s\S]*?)</table>", html):
        rows = []
        for tr in _re.findall(r"<tr[^>]*>([\s\S]*?)</tr>", tb):
            cells = [_re.sub(r"<[^>]+>", "", c).replace("&nbsp;", " ").strip()
                     for c in _re.findall(r"<t[hd][^>]*>([\s\S]*?)</t[hd]>", tr)]
            if any(cells):
                rows.append(cells)
        if len(rows) >= 2:
            out.append(rows)
    return out


def naver_extract(href: str, label: str) -> tuple[list, list]:
    """한 투자자 페이지에서 (순매수, 순매도) 목록을 뽑는다."""
    html = naver_get(href)
    tables = naver_parse_tables(html)
    buys, sells = [], []
    seen_amount_tables = 0
    for rows in tables:
        header = rows[0]
        htxt = " ".join(header)
        # 금액 열 찾기 (단위: 보통 백만원)
        amt_i = next((i for i, h in enumerate(header) if "금액" in h), None)
        name_i = next((i for i, h in enumerate(header) if "종목" in h), None)
        if amt_i is None or name_i is None:
            continue
        unit_div = 100.0 if "백만" in htxt else (1e8 if "원" in htxt else 100.0)
        seen_amount_tables += 1
        if "매도" in htxt:
            side = "sell"
        elif "매수" in htxt:
            side = "buy"
        else:
            # 헤더에 방향이 없으면 등장 순서로: 첫 금액표=순매수, 둘째=순매도
            side = "buy" if seen_amount_tables == 1 else "sell"
        recs = []
        for r in rows[1:]:
            if len(r) <= max(amt_i, name_i):
                continue
            name = r[name_i].strip()
            try:
                amt = float(r[amt_i].replace(",", "").replace("+", ""))
            except ValueError:
                continue
            if name and abs(amt) > 0:
                recs.append({"n": name, "v": round(abs(amt) / unit_div, 1)})
        if not recs:
            continue
        print(f"  · [네이버:{label}] 표 인식: 헤더={header[:5]} → {len(recs)}행, side={side}")
        if side == "sell":
            sells.extend({"n": x["n"], "v": -x["v"]} for x in recs)
        else:
            buys.extend(recs)
    # 진단: 아무것도 못 뽑았으면 표 헤더들을 전부 보여준다
    if not buys and not sells:
        for rows in tables[:6]:
            print(f"  · [네이버:{label}] 미인식 표 헤더: {rows[0][:6]}", file=sys.stderr)
    return buys[:TOP_N], sells[:TOP_N]


def collect_naver() -> dict | None:
    try:
        found = naver_discover()
    except Exception as e:  # noqa: BLE001
        print(f"  ! 네이버 접속 실패: {type(e).__name__}: {e}", file=sys.stderr)
        return None
    if not found:
        return None
    out = {}
    for key in ("foreign", "inst"):
        if key not in found:
            continue
        try:
            buy, sell = naver_extract(found[key], key)
            if buy or sell:
                out[key] = {"buy": buy, "sell": sell}
        except Exception as e:  # noqa: BLE001
            print(f"  ! 네이버 {key} 파싱 실패: {type(e).__name__}: {e}", file=sys.stderr)
    return out or None


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
        except RuntimeError as e:              # 접속 자체가 안 되는 경우 — 폴백으로
            print(f"  ! {e}", file=sys.stderr)
            print("  · KRX 차단으로 판단 — 네이버 금융 폴백을 시도합니다.")
            day = collect_naver()
            if day:
                payload = {"generated_at": datetime.now(KST).isoformat(timespec="seconds"),
                           "date": trd_dd, "source": "naver", **day}
                with open(OUT, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
                print(f"저장 → {OUT} (네이버 폴백, 기준일 표기는 페이지 갱신 시점 기준)")
                return 0
            print("  ! 폴백도 실패 — 위 로그(미인식 표 헤더 포함)를 공유해 주세요.")
            return 0
        except Exception as e:                 # noqa: BLE001
            print(f"  ! 수집 오류: {e}", file=sys.stderr)
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
