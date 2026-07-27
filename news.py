#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
5개국 경제 헤드라인 수집기 → news.json
=====================================
구글 뉴스 RSS(한국어)에서 국가별 경제 뉴스를 모아, 공신력 있는 매체만 남기고
대시보드 02 탭 형식으로 저장합니다. AI 없이 서버에서 돌므로 어디서 열어도 갱신됩니다.

  pip install requests
  python news.py
"""

from __future__ import annotations

import re
import json
import sys
import html
import time
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

import requests

KST = timezone(timedelta(hours=9))
OUT = "news.json"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) economic-wire/1.0"}

QUERIES = [
    ("KR", "한국은행 OR 기준금리 OR 코스피 OR 한국 경제"),
    ("US", "연준 OR 미국 경제 OR 뉴욕증시"),
    ("CN", "중국 경제 OR 인민은행"),
    ("JP", "일본은행 OR 엔화 OR 일본 경제"),
    ("TW", "대만 경제 OR TSMC"),
]

# 공신력 매체 화이트리스트 (구글 RSS 의 <source> 표기 기준, 부분일치)
TRUSTED = ("연합뉴스", "한국경제", "매일경제", "조선비즈", "머니투데이", "서울경제",
           "아시아경제", "이데일리", "뉴시스", "뉴스1", "파이낸셜뉴스", "헤럴드경제",
           "KBS", "MBC", "SBS", "YTN", "JTBC", "한겨레", "경향", "동아일보",
           "중앙일보", "조선일보", "Reuters", "로이터", "블룸버그", "Bloomberg")

HOT = re.compile(r"기준금리|금리\s?(인상|인하|동결)|GDP|성장률|물가|CPI|수출|고용|실업|"
                 r"환율|무역|관세|FOMC|연준|중앙은행|국채")
WARM = re.compile(r"증시|주가|코스피|나스닥|실적|투자|유가|반도체")


def weight(title: str) -> int:
    if HOT.search(title):
        return 3
    if WARM.search(title):
        return 2
    return 1


def clean_title(t: str) -> str:
    t = html.unescape(t).strip()
    # 구글 RSS 제목 꼬리의 " - 매체명" 제거
    return re.sub(r"\s+-\s+[^-]{2,25}$", "", t).strip()


def fetch_country(code: str, query: str) -> list[dict]:
    url = ("https://news.google.com/rss/search?q=" + requests.utils.quote(query)
           + "&hl=ko&gl=KR&ceid=KR:ko")
    r = requests.get(url, headers=UA, timeout=20)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    items = []
    for it in root.iter("item"):
        title = clean_title(it.findtext("title") or "")
        link = (it.findtext("link") or "").strip()
        src = (it.findtext("source") or "").strip()
        pub = it.findtext("pubDate") or ""
        if not title or not link:
            continue
        try:
            dt = parsedate_to_datetime(pub).astimezone(KST)
        except Exception:  # noqa: BLE001
            dt = datetime.now(KST)
        items.append({"title": title, "link": link, "src": src, "dt": dt})

    trusted = [x for x in items if any(t in x["src"] for t in TRUSTED)]
    others = [x for x in items if x not in trusted]
    trusted.sort(key=lambda x: x["dt"], reverse=True)
    others.sort(key=lambda x: x["dt"], reverse=True)
    pool = trusted + others          # 신뢰매체 우선, 부족분만 그 외로 보충

    out, seen = [], set()
    for x in pool:
        key = x["title"][:18]
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "c": code,
            "t": f"{x['dt']:%m.%d}",
            "h": x["title"][:90],
            "s": x["src"] or "구글뉴스",
            "w": weight(x["title"]),
            "u": x["link"],
        })
        if len(out) >= 12:
            break
    print(f"  · {code}: 수집 {len(items)}건 → 신뢰매체 {len(trusted)}건 → 채택 {len(out)}건")
    return out


def main() -> int:
    print("헤드라인 수집 시작")
    all_items = []
    for code, q in QUERIES:
        try:
            all_items.extend(fetch_country(code, q))
        except Exception as e:  # noqa: BLE001
            print(f"  ! {code} 실패: {type(e).__name__}: {e}", file=sys.stderr)
        time.sleep(0.5)

    if len(all_items) < 5:
        print("  ! 수집이 거의 실패 — 기존 news.json 을 보존하고 종료합니다.")
        return 0

    payload = {"generated_at": datetime.now(KST).isoformat(timespec="seconds"),
               "items": all_items}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    print(f"저장 → {OUT} ({len(all_items)}건)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
