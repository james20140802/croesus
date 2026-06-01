# Sprint 008: Portfolio Current-Price Valuation and FX

## Goal

보유 포트폴리오를 **현재가로 자동 평가**한다. 사용자는 본인만 아는 것(종목·수량·평단가)만 입력하고,
시스템이 가져올 수 있는 것(현재가·환율)은 저장소에서 읽어 시가평가·평가손익·다통화 환산을 계산한다.

```text
Holdings CSV (quantity + avg_cost)
  -> Holdings Import
  -> Valuation Layer (prices_daily 최신 종가 + fx_rates 환율)
       market_value(base) = quantity × close × fx
       cost_basis(base)   = quantity × avg_cost × fx
       unrealized_pnl     = market_value − cost_basis
  -> Exposure / Policy Engine (변경 없음, market_value 소비)
  -> portfolio_snapshot job (총평가액·총원가·평가손익 영속화)
```

Sprint 004(Portfolio Snapshot & Exposure)가 완료되어 있음을 전제로 한다.
이 스프린트는 Sprint 004의 "Out of Scope"였던 **Quantity-only market value calculation**과
**Multi-currency FX conversion**을 정식 범위로 끌어올린다.

> 논리적 의존: 이 스프린트는 Sprint 004 위에 쌓이며, Sprint 006(Rebalancing Proposal Engine)이
> 정확한 현재 평가액을 필요로 하므로 그 이전에 구현되는 것이 바람직하다. 기존 005~007 번호는
> 변경하지 않고 008로 추가한다.

---

## 핵심 원칙

- **수집과 분석 분리** (CLAUDE.md): 스냅샷 잡은 저장소(`prices_daily`, `fx_rates`)에서 **읽기만** 한다.
  현재가/환율의 갱신은 수집 잡(`daily_run` 등)이 담당한다. 스냅샷 자체는 네트워크를 타지 않는다.
- **사용자 입력 최소화**: 현재가를 사용자가 매번 입력/업데이트하지 않는다. 다른 시계열 데이터와
  동일하게 yfinance로 수집한다.
- **부분 실패 허용** (CLAUDE.md): 가격/환율이 없는 종목이 있어도 크래시 없이 스냅샷을 완성한다.

---

## Scope

### 1. Schema 업데이트

`croesus/db/schema.sql`에 환율 테이블을 추가하고, 스냅샷 테이블에 손익 컬럼을 추가한다.

```sql
-- 신규: 통화별 일일 환율. yfinance "<QUOTE>=X"는 "USD 1단위당 QUOTE 단위"를 의미하므로
-- rate_per_usd 규격으로 저장한다 (예: KRW=X -> rate_per_usd ≈ 1507.58). USD는 항상 1.0.
CREATE TABLE IF NOT EXISTS fx_rates (
  quote_currency TEXT NOT NULL,
  date           DATE NOT NULL,
  rate_per_usd   DOUBLE,          -- 1 USD = rate_per_usd × quote_currency
  source         TEXT,
  PRIMARY KEY (quote_currency, date)
);
```

`portfolio_snapshots`에 컬럼 추가. 이 프로젝트는 마이그레이션 프레임워크 없이 `migrate.py`가
`schema.sql`을 `CREATE TABLE IF NOT EXISTS`로 실행하므로(Sprint 004 방식), **기존 CREATE 문에 컬럼을
직접 추가**한다(`ALTER TABLE` 아님). 프로토타이핑 단계라 기존 DB는 재생성을 전제로 한다.

```sql
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
  portfolio_id     TEXT NOT NULL,
  as_of_date       DATE NOT NULL,
  total_market_value DOUBLE,
  cash_value       DOUBLE,
  total_cost_basis DOUBLE,          -- 신규: base 통화 총 매입원가
  unrealized_pnl   DOUBLE,          -- 신규: total_market_value − total_cost_basis
  metadata         JSON,
  PRIMARY KEY (portfolio_id, as_of_date)
);
```

> 종목별 평가손익은 `portfolio_holdings.market_value − portfolio_holdings.cost_basis`로 파생 가능하므로
> 별도 컬럼을 추가하지 않는다. `cost_basis`의 의미를 **base 통화 총 매입원가**로 확정한다(아래 참조).
> 기존 DB에 컬럼을 반영하려면 DB 파일 재생성(또는 수동 `ALTER TABLE ... ADD COLUMN`)이 필요하다.

### 2. FX 수집 모듈

`prices_daily`/`ingest_prices`와 동일한 패턴으로 환율 수집을 구현한다.

```text
croesus/fx/
  __init__.py
  repository.py        -- FxRepository: upsert_rates(), get_latest_rate(quote, as_of)
  ingest_fx_rates.py   -- yfinance "<QUOTE>=X" 조회 → fx_rates 저장
```

- 수집 대상 통화: `assets.currency` ∪ `portfolios.base_currency` 중 **USD가 아닌** 통화 집합에서 도출.
- `FxRepository.get_latest_rate(quote_currency, as_of)`: `as_of` 이하 최신 환율 1건(carry-forward).
- 개별 통화 수집 실패 시 해당 통화만 건너뛰고 로그 기록(전체 실행은 계속).

### 3. 가격 조회 헬퍼

`croesus/prices/repository.py`에 추가:

```python
def get_latest_close(self, asset_id: str, as_of: date) -> float | None:
    """as_of 이하 가장 최근 종가. 없으면 None."""
```

### 4. 통화 변환 헬퍼

`croesus/fx/convert.py` (또는 `FxRepository` 정적 메서드):

```python
def to_base(amount: float, native: str, base: str, rates: dict[str, float]) -> float | None:
    """rates: {quote_currency: rate_per_usd}, USD는 1.0 가정.
    amount_base = amount × (rates[base] / rates[native]).
    native/base 환율이 없으면 None (호출부에서 폴백 + 경고)."""
```

### 5. 평가 레이어 (신규)

```text
croesus/portfolio/valuation.py
```

기존 `exposure.py`/`policy.py`가 `market_value`만 소비하므로 **두 엔진은 수정하지 않는다.**
평가 레이어가 import된 보유분을 받아 base 통화 시가평가·원가·손익을 채운 보유분으로 변환한다.

```python
def value_holdings(
    raw_holdings: list[Holding],
    price_lookup: Callable[[str], float | None],   # asset_id -> 최신 종가(native)
    fx_rates: dict[str, float],                     # quote_currency -> rate_per_usd
    assets_by_id: dict[str, AssetAttrs],
    *,
    base_currency: str,
    as_of_date: date,
) -> ValuationResult:
    ...
```

**종목별 계산:**

1. native 시장가치 결정 (3단계 폴백, 결정적):
   - 현금/시세 없는 자산(`asset_type == "cash"` 또는 `CASH_*`): CSV `market_value`(금액)를 사용.
   - 그 외: `close = price_lookup(asset_id)`.
     - `close` 존재 → `native_mv = quantity × close`, `price_source = "store"`.
     - 없고 CSV `market_value` 제공 → 그 값 사용, `price_source = "manual"` + 경고.
     - 둘 다 없음 → `native_mv = quantity × avg_cost` (매입원가 대체), `price_source = "cost_basis"` + 경고.
2. native 원가: `native_cost = quantity × avg_cost` (현금은 `native_cost = native_mv`, 손익 0).
3. base 환산: `market_value = to_base(native_mv, currency, base, fx_rates)`,
   `cost_basis = to_base(native_cost, currency, base, fx_rates)`.
   - 환율 없으면 1:1로 처리 + `FX_MISSING` 경고(데이터 품질 경고). 실무상 수집 잡이 채우므로 드묾.
4. 손익: `unrealized_pnl = market_value − cost_basis`,
   `return_pct = unrealized_pnl / cost_basis` (cost_basis 0이면 None).

### 6. Holdings CSV 포맷 변경

**이전(Sprint 004):** `market_value`가 필수 — 사용자가 현재 평가액을 직접 입력.

**이후(이번):** `quantity` + `avg_cost`(주당 평단가)를 입력, 현재가는 시스템이 채움.

```csv
portfolio_id,asset_id,quantity,avg_cost,currency,market_value
default,US_ETF_VOO,2,549.22,USD,
default,US_EQ_AMZN,2,248.21,USD,
default,CASH_USD,,,USD,2243.07
default,CASH_KRW,,,KRW,421391
```

규칙:
- 시세 자산: `quantity`와 `avg_cost` 필수. `market_value`는 비움(파생) — 단, 가격 없을 때 수동 대체값으로 사용 가능.
- 현금/외화 현금: `asset_id = CASH_<CUR>`, `market_value`에 금액, `currency`에 통화. `quantity`/`avg_cost`는 비움.
- `currency` 생략 시 portfolio 지배 프로파일의 base_currency로 기본값(Sprint 004 동작 유지).
- 알 수 없는 `asset_id`는 보고 후 스킵(`CASH_*` 제외) — Sprint 004 동작 유지.

> **하위호환:** 기존 `market_value`만 있는 CSV도 동작해야 한다. 시세 자산인데 `quantity`/`avg_cost`가
> 없고 `market_value`만 있으면 `price_source = "manual"`로 그대로 사용(손익은 None). 즉 이번 변경은
> 입력을 **확장**하는 것이지 기존 입력을 깨지 않는다.

### 7. portfolio_snapshot 잡 업데이트

`run_portfolio_snapshot` 흐름에 평가 단계를 삽입한다.

```text
import (quantity + avg_cost)
  -> price_lookup = PriceRepository.get_latest_close 부분적용
  -> fx_rates = FxRepository로 필요한 통화 환율 로드
  -> value_holdings(...) → base 통화 market_value/cost_basis/pnl이 채워진 보유분
  -> compute_exposures / compute_policy_drifts (변경 없음)
  -> repository: holdings/exposures/drifts/snapshot 영속화
       portfolio_snapshots.total_cost_basis, unrealized_pnl 기록
  -> 결과 + 로그에 총평가액/총원가/총손익/종목별 손익/price_source 경고 포함
```

CLI는 Sprint 004와 동일(`--holdings`, `--portfolio-id`, `--date`). 가격/환율 수집 자체는 이 잡의
책임이 아니며, 사전에 `daily_run`(또는 FX 수집 단계)이 채워둔다.

### 8. daily_run / bootstrap 배선

- `daily_run`에 FX 수집 단계 추가: 가격 수집 직후 `ingest_fx_rates(conn, currencies=...)`.
- 수집 대상 통화는 `assets.currency` ∪ `portfolios.base_currency`에서 비-USD를 도출.

---

## Data Models

### `Holding` (필드 추가)

```python
@dataclass(frozen=True)
class Holding:
    portfolio_id: str
    asset_id: str
    as_of_date: date
    quantity: float
    market_value: float | None      # 평가 레이어가 base 통화로 채움 (현금은 입력 금액)
    currency: str
    cost_basis: float | None = None  # base 통화 총 매입원가 (= quantity × avg_cost × fx)
    avg_cost: float | None = None    # 신규: 주당 평단가 (native 통화)
    source: str | None = "manual_csv"
    metadata: dict[str, Any] = field(default_factory=dict)
```

### `ValuationResult`

```python
@dataclass(frozen=True)
class ValuationResult:
    holdings: list[Holding]          # market_value/cost_basis가 채워진 base 통화 보유분
    total_market_value: float
    total_cost_basis: float
    unrealized_pnl: float
    warnings: list[str]              # price_source/ FX_MISSING 경고
```

### `PortfolioSnapshotResult` (필드 추가)

```python
@dataclass(frozen=True)
class PortfolioSnapshotResult:
    portfolio_id: str
    as_of_date: date
    total_market_value: float
    total_cost_basis: float          # 신규
    unrealized_pnl: float            # 신규
    holdings_imported: int
    holdings_skipped: int
    exposures: list[Exposure]
    policy_drifts: list[PolicyDrift]
    warnings: list[str]
```

---

## FX 변환 규칙

- 저장: `rate_per_usd` = 1 USD가 몇 quote 단위인지 (yfinance `<QUOTE>=X` 그대로). USD = 1.0.
- 변환: `amount_base = amount_native × rate_per_usd[base] / rate_per_usd[native]`.
  - base=USD, native=KRW 예: `421391 × (1 / 1507.58) = 279.51 USD`.
- 조회: `as_of` 이하 최신 환율(carry-forward). 주말/공휴일 대응.
- 누락: base 또는 native 환율이 전혀 없으면 1:1 처리 + `FX_MISSING` 경고(크래시 금지).

---

## Tests

```text
tests/test_fx_ingest.py
tests/test_portfolio_valuation.py
tests/test_portfolio_snapshot.py   (확장)
```

필수 테스트:

1. `migrate()`가 `fx_rates` 테이블과 `portfolio_snapshots`의 신규 컬럼을 생성한다.
2. `FxRepository.upsert_rates` / `get_latest_rate`가 carry-forward로 최신 환율을 반환한다.
3. `get_latest_close`가 `as_of` 이하 최신 종가를 반환하고, 없으면 None.
4. 시세 자산: `market_value = quantity × close`로 base 통화 평가된다.
5. 가격 없는 시세 자산: `quantity × avg_cost`로 대체되고 경고가 생기며 손익 0.
6. 외화 보유분(KRW)이 `fx_rates`로 base(USD)로 환산된다.
7. 평가손익: `unrealized_pnl = market_value − cost_basis`, 종목별 + 포트폴리오 합계.
8. 현금(CASH_USD/CASH_KRW)이 입력 금액으로 평가되고 외화 현금은 환산된다.
9. 하위호환: `market_value`만 있는 기존 CSV가 여전히 동작한다(`price_source = "manual"`).
10. `run_portfolio_snapshot()`가 `total_cost_basis`/`unrealized_pnl`을 `portfolio_snapshots`에 기록한다.
11. 부분 실패(일부 종목 가격 없음 + 일부 통화 환율 없음)에도 스냅샷이 완성된다.

---

## Suggested Task Breakdown

### Task 1: FX 스키마 + 스냅샷 손익 컬럼

- Modify: `croesus/db/schema.sql`
- Test: `tests/test_portfolio_snapshot.py`

```bash
git commit -m "🗃️ chore: add fx_rates table and snapshot pnl columns"
```

### Task 2: FX 수집 모듈

- Create: `croesus/fx/__init__.py`, `croesus/fx/repository.py`, `croesus/fx/ingest_fx_rates.py`
- Test: `tests/test_fx_ingest.py`

```bash
git commit -m "✨ feat: ingest daily FX rates via yfinance"
```

### Task 3: 가격 조회 + 통화 변환 헬퍼

- Modify: `croesus/prices/repository.py` (`get_latest_close`)
- Create: `croesus/fx/convert.py`
- Test: `tests/test_portfolio_valuation.py`

```bash
git commit -m "✨ feat: add latest-close lookup and fx conversion helpers"
```

### Task 4: 평가 레이어

- Create: `croesus/portfolio/valuation.py`
- Modify: `croesus/portfolio/models.py` (`Holding.avg_cost`, `ValuationResult`)
- Test: `tests/test_portfolio_valuation.py`

```bash
git commit -m "✨ feat: value holdings at current prices with fx and pnl"
```

### Task 5: CSV 포맷 확장 (quantity + avg_cost)

- Modify: `croesus/portfolio/import_holdings.py`
- Test: `tests/test_portfolio_snapshot.py`

```bash
git commit -m "✨ feat: import quantity and avg_cost, market_value optional"
```

### Task 6: 잡 통합 + daily_run 배선

- Modify: `croesus/jobs/portfolio_snapshot.py`, `croesus/jobs/daily_run.py`, `croesus/portfolio/repository.py`
- Test: `tests/test_portfolio_snapshot.py`

```bash
git commit -m "✨ feat: wire valuation and fx into portfolio snapshot job"
```

---

## Acceptance Criteria

- 사용자는 현재가를 입력하지 않는다. `quantity`와 `avg_cost`만으로 현재 평가액이 계산된다.
- 시세는 `prices_daily`에서, 환율은 `fx_rates`에서 읽는다(스냅샷 잡은 네트워크 미사용).
- 외화 보유분/현금이 base 통화로 환산되어 총평가액·비중에 반영된다.
- 종목별 및 포트폴리오 평가손익(`unrealized_pnl`)이 계산·저장된다.
- 가격 또는 환율이 없는 종목이 있어도 스냅샷이 크래시 없이 완성되고, 경고로 명확히 표시된다.
- 기존 `market_value` 기반 CSV가 여전히 동작한다(하위호환).
- exposure/policy 엔진 로직은 변경되지 않는다(`market_value`만 소비).

## Out of Scope

- 실시간/장중 시세(일일 종가 기준).
- 실현 손익(realized P&L), 세금 lot, 배당/수수료 반영.
- 브로커 API 직접 연동(여전히 수동 CSV).
- 종목 자동 등록(보유 종목은 사전에 자산 레지스트리에 등록되어 있어야 함 — 별도 관심사).
- FX 헤지/선물 환율, 통화별 현금 이자.

## Notes

- yfinance FX 심볼은 `"<QUOTE>=X"` (예: `KRW=X`, `JPY=X`, `EUR=X`). `EUR=X`는 EUR per USD가
  아니라 USD per EUR로 반대인 경우가 있으므로 수집 시 통화별 방향을 명시적으로 검증/정규화한다.
- 보유 종목이 자산 레지스트리에 없으면(현재 시드는 AAPL/MSFT/NVDA뿐) import 단계에서 스킵된다.
  실제 시연 전 보유 종목 등록 + 가격/환율 수집이 선행되어야 한다.
- `cost_basis`의 의미를 이번 스프린트에서 **base 통화 총 매입원가**로 확정한다(Sprint 004에서는
  미사용 필드였음). 마이그레이션 시 기존 행의 의미 차이에 주의.
