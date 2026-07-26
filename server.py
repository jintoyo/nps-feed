#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
국민연금 지분공시 피드 서버 (Railway용)
=====================================
collect.py 를 평일 스케줄로 돌리고, 결과를 CORS 허용 상태로 서빙합니다.
대시보드에는 https://<앱주소>/api/nps 를 넣으면 됩니다.

  pip install fastapi uvicorn apscheduler requests
  python server.py

Railway 환경변수
  DART_API_KEY   (필수)
  NPS_DAYS       기본 14
  NPS_THRESHOLD  기본 0.10
  ALLOW_ORIGIN   기본 *   (특정 도메인만 허용하려면 지정)
  PORT           Railway가 자동 주입
"""

from __future__ import annotations

import os
import json
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

import uvicorn
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

KST = timezone(timedelta(hours=9))
OUT = Path("nps-latest.json")
app = FastAPI(title="NPS 지분공시 피드")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.environ.get("ALLOW_ORIGIN", "*")],
    allow_methods=["GET"],
    allow_headers=["*"],
)

_last_run: dict = {"at": None, "ok": None, "log": ""}


def run_collect() -> None:
    """collect.py 를 서브프로세스로 실행."""
    print(f"[{datetime.now(KST):%Y-%m-%d %H:%M}] 수집 시작", flush=True)
    proc = subprocess.run(
        ["python", "collect.py",
         "--days", os.environ.get("NPS_DAYS", "14"),
         "--threshold", os.environ.get("NPS_THRESHOLD", "0.10")],
        capture_output=True, text=True,
    )
    _last_run.update(
        at=datetime.now(KST).isoformat(timespec="seconds"),
        ok=(proc.returncode == 0),
        log=(proc.stdout or "")[-2000:] + (proc.stderr or "")[-1000:],
    )
    print(proc.stdout or proc.stderr, flush=True)


@app.get("/api/nps")
def feed():
    """대시보드가 읽는 엔드포인트."""
    if not OUT.exists():
        return JSONResponse(
            {"generated_at": None, "count": 0, "filings": [],
             "note": "아직 수집 전입니다. /api/refresh 로 즉시 실행할 수 있습니다."},
            headers={"Cache-Control": "no-store"},
        )
    return JSONResponse(
        json.loads(OUT.read_text(encoding="utf-8")),
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/refresh")
def refresh():
    """수동으로 즉시 수집."""
    run_collect()
    return {"ok": _last_run["ok"], "at": _last_run["at"]}


@app.get("/api/health")
def health():
    return {"ok": True, "last_run": _last_run,
            "has_file": OUT.exists(), "now": datetime.now(KST).isoformat(timespec="seconds")}


if __name__ == "__main__":
    sched = BackgroundScheduler(timezone="Asia/Seoul")
    # 평일 장중·마감 후 두 번
    sched.add_job(run_collect, CronTrigger(day_of_week="mon-fri", hour="16,19", minute=10))
    sched.start()

    if not OUT.exists():
        run_collect()          # 첫 기동 시 한 번 채워둔다

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
