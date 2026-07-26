name: 핵심 지표 수집

on:
  schedule:
    - cron: "7 * * * *"        # 매시간 7분 (UTC 기준, 하루 24회)
  workflow_dispatch:            # 버튼으로 즉시 실행

permissions:
  contents: write

jobs:
  indicators:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - run: pip install requests

      - name: 지표 수집
        run: python indicators.py

      - name: 외국인/기관 순매매 수집
        run: python flows.py

      - name: 변경분만 커밋
        run: |
          git config user.name  "ind-bot"
          git config user.email "ind-bot@users.noreply.github.com"
          git pull --rebase
          git add indicators.json flows.json
          git diff --staged --quiet || git commit -m "지표 갱신 $(date -u +'%Y-%m-%d %H:%M')"
          git push
