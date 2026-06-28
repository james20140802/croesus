# Croesus

Python-first investment research pipeline.

## Setup

This repo uses `uv` for dependency management.

```bash
uv sync
```

Optional local configuration:

```bash
cp .env.example .env
```

By default, Croesus stores local data at `storage/croesus.duckdb`.

## Sprint 001 Pipeline

Create the DuckDB schema and seed the initial US equity assets:

```bash
python -m croesus.jobs.bootstrap
```

Run the daily pipeline:

```bash
python -m croesus.jobs.daily_run
```

The daily run reads active US equities from the asset registry, downloads one
year of daily OHLCV data from yfinance, stores prices, and computes common
deterministic factors.

## Manual Verification

```python
from croesus.db.connection import get_connection

with get_connection() as conn:
    print(conn.execute("SELECT * FROM assets").df())
    print(conn.execute("SELECT * FROM prices_daily LIMIT 5").df())
    print(conn.execute("SELECT * FROM factor_values").df())
```

## Tests

```bash
python -m pytest
```

## 웹 대시보드

```bash
python -m croesus.web --port 8000          # 0.0.0.0 바인딩
```

기동 시 접속 URL을 출력합니다. Tailscale이 설치돼 있으면 tailnet IP가 표시되며,
태블릿·폰에서 같은 tailnet으로 접속하면 됩니다. HTTPS가 필요하면:

```bash
tailscale serve --bg 8000
```

### 자동 데이터 갱신

웹 서버가 떠 있는 동안 매일 정해진 시각에 데이터를 자동으로 수집·처리할 수
있습니다. `--schedule HH:MM`(로컬 시각)을 주면 됩니다. 장 마감 후 시각을
권장합니다.

```bash
python -m croesus.web --port 8000 --schedule 18:00
```

매일 그 시각에 일일 파이프라인(시세·환율·팩터)과 스크리닝이 한 번 실행됩니다.
갱신 중에는 DuckDB 파일이 쓰기 잠금되어, 웹 페이지가 잠깐 "데이터 갱신 중"
화면으로 바뀌었다가 완료되면 자동으로 최신 데이터를 보여줍니다. 별도 데몬이나
크론 없이 서버 프로세스 안에서 동작하므로, 서버가 떠 있어야 갱신됩니다.

`설정` 화면에서 다음 예정 시각·마지막 실행 결과를 확인할 수 있고, **지금 갱신**
버튼으로 즉시 한 번 돌릴 수도 있습니다. (매크로는 별도 주기로 갱신되므로 일일
자동 갱신에는 포함되지 않습니다.)
