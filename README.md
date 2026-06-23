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
