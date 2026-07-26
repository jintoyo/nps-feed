# 국민연금 지분공시 자동 피드

대시보드([02 국민연금 지분공시] 탭)가 읽어갈 `nps-latest.json` 을 만드는 수집기입니다.
DART OpenAPI를 **서버에서** 호출하므로 브라우저 CORS 제한을 받지 않습니다.

## 0. 준비
1. https://opendart.fss.or.kr 에서 API 키 발급 (무료, 즉시)
2. `baseline_1q.json` 에 1분기 기준 보유비율 입력 — `{"종목명": 비율}`

## 방법 A · GitHub Actions (서버 없음, 추천)
1. 이 폴더를 **public** 저장소로 push
2. Settings → Secrets and variables → Actions → New secret
   - Name `DART_API_KEY` / Value 발급받은 키
3. Actions 탭에서 `NPS 지분공시 수집` → Run workflow 로 첫 실행
4. 대시보드 피드 주소에 아래를 입력

```
https://raw.githubusercontent.com/<계정>/<저장소>/main/nps-latest.json
```

raw.githubusercontent.com 은 CORS를 열어두므로 대시보드가 바로 읽습니다.
평일 19:10 KST에 자동 실행되고, 내용이 바뀔 때만 커밋합니다.

## 방법 B · Railway (상시 서버)
1. 이 폴더를 Railway에 배포 (Start command: `python server.py`)
2. Variables에 `DART_API_KEY` 추가
3. 대시보드 피드 주소에 `https://<앱주소>/api/nps` 입력

| 엔드포인트 | 용도 |
|---|---|
| `GET /api/nps` | 대시보드가 읽는 피드 |
| `GET /api/refresh` | 즉시 수집 |
| `GET /api/health` | 마지막 실행 상태 확인 |

## 로컬에서 한 번 돌려보기
```bash
pip install requests
export DART_API_KEY="발급키"
python collect.py --days 14
```

## 출력 형식
```json
{
  "generated_at": "2026-07-27T19:10:00+09:00",
  "baseline_label": "2026 1Q",
  "baseline": {"삼성전자": 7.28},
  "filings": [
    {"corp":"삼성전자","rcept_no":"20260727000123","date":"20260727",
     "report":"주식등의대량보유상황보고서(약식)","rate":7.41,
     "base":7.28,"delta":0.13,"verdict":"매수"}
  ]
}
```

## 알아둘 점
- 대량보유 **변동보고 의무는 지분율 1%p 단위**입니다. 그보다 작은 매매는 공시에 잡히지 않으므로
  이 리포트는 "체결 추적"이 아니라 "방향 확인"으로 보셔야 합니다.
- 국민연금은 경영참가 목적이 아닌 경우 **약식보고** 특례를 쓰며, 보고 시점이 매매 시점보다 늦습니다.
- DART OpenAPI는 분당 호출 제한이 있어 `SLEEP` 값을 낮추면 차단될 수 있습니다.
