# Sprint 004b: Portfolio Mark-to-Market and FX

## Goal

보유 포트폴리오를 **현재가로 자동 시가평가(mark-to-market)**한다. 사용자는 본인만 아는 것(종목·수량·평단가)만
입력하고, 시스템이 가져올 수 있는 것(현재가·환율)은 저장소에서 읽어 시가평가·평가손익·다통화 환산을 계산한다.

```text
Holdings CSV (quantity + avg_cost)
  -> Holdings Import
  -> Mark-to-Market Layer (prices_daily 최신 종가 + fx_rates 환율)
       market_value(base) = quantity × close × fx
       cost_basis(base)   = quantity × avg_cost × fx
       unrealized_pnl     = market_value − cost_basis
  -> Exposure / Policy Engine (집계 로직 변경 없음, market_value 소비)
  -> portfolio_snapshot job (총평가액·총원가·평가손익 영속화)
```

Sprint 004(Portfolio Snapshot & Exposure)가 완료되어 있음을 전제로 한다.
이 스프린트는 Sprint 004의 "Out of Scope"였던 **Quantity-only market value calculation**과
**Multi-currency FX conversion**을 정식 범위로 끌어올린다.

> **번호/포지셔닝:** canonical roadmap(`profile-first-roadmap.md`)에서 Sprint 008은 이미 *Research Agent*다.
> 충돌을 피하려고 이 문서는 Sprint 004의 후속(**004b**)으로 둔다. Sprint 006(Rebalancing)이 정확한 현재
> 평가액을 필요로 하므로 그 이전에 구현되는 것이 바람직하다. Sprint 007(*Valuation Layer*: 펀더멘털/DCF
> 기반 개별 종목 밸류에이션)과는 **성격이 다르다** — 이 스프린트는 보유분의 시가평가(mark-to-market)이며,
> 그래서 모듈·결과 타입에 "valuation" 대신 **"mark-to-market"** 명칭을 쓴다.

---

## 핵심 원칙

- **수집과 분석 분리** (AGENTS.md / CLAUDE.md): 스냅샷 잡은 저장소(`prices_daily`, `fx_rates`)에서 **읽기만**
  한다. 현재가/환율의 갱신은 수집 잡(`daily_run` 등)이 담당한다. 스냅샷 자체는 네트워크를 타지 않는다.
- **사용자 입력 최소화**: 현재가를 사용자가 매번 입력/업데이트하지 않는다. 다른 시계열 데이터와 동일하게
  yfinance로 수집한다.
- **부분 실패 허용**: 가격/환율이 없는 종목이 있어도 크래시 없이 스냅샷을 완성한다(경고로 표시).
- **모듈 의존 방향 유지**: 수집 계층(`prices`, `fx`)은 포트폴리오 내부를 모른다. FX 수집 함수는 통화 목록만
  받고, 그 목록을 만드는 일은 **잡 오케스트레이션 계층**의 책임이다(아래 §2, §8).

---

## Scope

### 1. Schema 업데이트

`croesus/db/schema.sql`에 환율 테이블을 추가하고, 스냅샷 테이블에 손익 컬럼을 **additive 마이그레이션**으로
추가한다. 이 repo는 이미 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` 패턴으로 기존 DB를 자동 마이그레이션한다
(`schema.sql`의 `macro_scores.regime_methods` 선례). 동일 패턴을 따라 `storage/croesus.duckdb`가 재생성
없이 갱신되도록 한다.

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

-- 기존 portfolio_snapshots에 손익 컬럼 추가 (fresh DB는 CREATE에도 반영, 기존 DB는 ALTER로 자동 마이그레이션)
ALTER TABLE portfolio_snapshots ADD COLUMN IF NOT EXISTS total_cost_basis DOUBLE;  -- base 통화 총 매입원가
ALTER TABLE portfolio_snapshots ADD COLUMN IF NOT EXISTS unrealized_pnl   DOUBLE;  -- total_market_value − total_cost_basis
```

`portfolio_snapshots`의 `CREATE TABLE` 문에도 두 컬럼을 추가해 새 DB에서 일관되게 생성되도록 한다.

> 종목별 평가손익은 `portfolio_holdings.market_value − portfolio_holdings.cost_basis`로 파생 가능하므로
> 별도 컬럼을 추가하지 않는다. `cost_basis`의 의미를 **base 통화 총 매입원가**로 확정한다(아래 참조).

### 2. FX 수집 모듈 (포트폴리오 비의존)

`prices_daily`/`ingest_prices`와 동일한 패턴으로 환율 수집을 구현한다. **이 모듈은 포트폴리오 내부를 모른다** —
변환 대상 통화 목록을 인자로 받을 뿐이다.

```text
croesus/fx/
  __init__.py
  repository.py        -- FxRepository: upsert_rates(), get_latest_rate(quote, as_of)
  ingest_fx_rates.py   -- ingest_fx_rates(conn, currencies, source): "<QUOTE>=X" 조회 → fx_rates 저장
  convert.py           -- to_base(amount, native, base, rates) 통화 변환 헬퍼
```

- `ingest_fx_rates(conn, currencies: list[str], source="yfinance")`: **호출자가 통화 목록을 전달**. 비-USD
  통화만 수집(USD는 1.0 고정). 개별 통화 실패 시 해당 통화만 건너뛰고 로그 기록.
- `FxRepository.get_latest_rate(quote_currency, as_of)`: `as_of` 이하 최신 환율 1건(carry-forward).
- **통화 목록 산정은 잡 계층의 책임**(§8). 단순히 `assets.currency`만 보면 **`CASH_<CUR>` 현금 통화를
  놓친다**(현금은 asset registry에 없음). 따라서 산정 소스는:
  `assets.currency` ∪ `portfolios.base_currency` ∪ `portfolio_holdings.currency`
  ∪ (스냅샷 잡 한정) 가져온 CSV 행들의 `currency`.

### 3. 가격 조회 헬퍼

`croesus/prices/repository.py`에 추가:

```python
def get_latest_close(self, asset_id: str, as_of: date) -> float | None:
    """as_of 이하 가장 최근 종가. 없으면 None."""
```

### 4. 현금 식별 일반화 (`CASH_<CUR>`)

현재 import/exposure는 `CASH_USD`만 하드코딩한다(`CASH_ASSET_ID`). 다통화 현금(`CASH_KRW` 등)을
지원하려면 **현금 판별을 접두사 기반 predicate로 일반화**한다.

```python
# croesus/portfolio/models.py (또는 공용 상수 모듈)
def is_cash(asset_id: str) -> bool:
    return asset_id.startswith("CASH_")
```

- `import_holdings`: 알 수 없는 자산 스킵 조건을 `not is_cash(asset_id) and asset_id not in known`으로 변경
  (현재는 `asset_id != "CASH_USD"`). → `CASH_KRW`도 통과.
- `exposure`/`policy`: 현금 분류(`sector/industry = Cash`)와 cash 슬리브 매칭을 `is_cash()`로 일반화.
  **핵심 집계 로직(market_value 소비)은 그대로**이고, 현금 식별만 공유 헬퍼로 교체한다.

### 5. Mark-to-Market 레이어 (신규)

```text
croesus/portfolio/mark_to_market.py
```

기존 `exposure.py`/`policy.py`의 집계 로직은 `market_value`만 소비하므로 **그 로직은 수정하지 않는다.**
이 레이어가 import된 보유분을 받아 base 통화 시가평가·원가·손익을 채운 보유분으로 변환한다.

```python
def mark_to_market(
    raw_holdings: list[Holding],
    price_lookup: Callable[[str], float | None],   # asset_id -> 최신 종가(native)
    fx_rates: dict[str, float],                     # quote_currency -> rate_per_usd
    assets_by_id: dict[str, AssetAttrs],
    *,
    base_currency: str,
    as_of_date: date,
) -> MarkToMarketResult:
    ...
```

**종목별 계산:**

1. native 시장가치 결정 (3단계 폴백, 결정적):
   - 현금(`is_cash(asset_id)`): CSV `market_value`(금액)를 사용.
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
- 현금/외화 현금: `asset_id = CASH_<CUR>`(예: `CASH_USD`, `CASH_KRW`), `market_value`에 금액, `currency`에 통화.
  `quantity`/`avg_cost`는 비움.
- `currency` 생략 시 portfolio 지배 프로파일의 base_currency로 기본값(Sprint 004 동작 유지).
- 알 수 없는 `asset_id`는 보고 후 스킵(`is_cash()`는 제외) — Sprint 004 동작 유지하되 `CASH_<CUR>`로 확장.

> **하위호환:** 기존 `market_value`만 있는 CSV도 동작해야 한다. 시세 자산인데 `quantity`/`avg_cost`가
> 없고 `market_value`만 있으면 `price_source = "manual"`로 그대로 사용(손익은 None). 즉 이번 변경은
> 입력을 **확장**하는 것이지 기존 입력을 깨지 않는다.

### 7. portfolio_snapshot 잡 업데이트

`run_portfolio_snapshot` 흐름에 mark-to-market 단계를 삽입한다.

```text
import (quantity + avg_cost)
  -> price_lookup = PriceRepository.get_latest_close 부분적용
  -> fx_rates = FxRepository로 필요한 통화 환율 로드 (보유분 currency 집합 기준)
  -> mark_to_market(...) → base 통화 market_value/cost_basis/pnl이 채워진 보유분
  -> compute_exposures / compute_policy_drifts (집계 로직 변경 없음)
  -> repository: holdings/exposures/drifts/snapshot 영속화
       portfolio_snapshots.total_cost_basis, unrealized_pnl 기록
  -> 결과 + 로그에 총평가액/총원가/총손익/종목별 손익/price_source 경고 포함
```

CLI는 Sprint 004와 동일(`--holdings`, `--portfolio-id`, `--date`). 가격/환율 수집 자체는 이 잡의
책임이 아니며, 사전에 `daily_run`(또는 FX 수집 단계)이 채워둔다.

### 8. daily_run / 잡 오케스트레이션 배선

FX 수집 통화 목록 산정은 **잡 계층**에서 한다(수집 모듈은 통화 목록만 받음 — §2).

- `daily_run`: 가격 수집 직후, `assets.currency` ∪ `portfolios.base_currency` ∪ `portfolio_holdings.currency`
  에서 비-USD 통화 집합을 구해 `ingest_fx_rates(conn, currencies=...)` 호출.
- `portfolio_snapshot` 잡: 스냅샷 직전, 가져온 CSV 행들의 `currency`까지 포함해 필요한 환율이 저장소에
  있는지 확인하고, 없으면 `FX_MISSING` 경고로 표시(스냅샷 잡은 네트워크 미사용 원칙 유지).

> 이렇게 하면 `prices`/`fx` 모듈은 포트폴리오 테이블을 import하지 않는다. 통화 집합 질의는 잡 함수 안의
> SQL로 두어 의존 방향(수집 → 분석)을 깨지 않는다.

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
    market_value: float | None      # mark-to-market 레이어가 base 통화로 채움 (현금은 입력 금액)
    currency: str
    cost_basis: float | None = None  # base 통화 총 매입원가 (= quantity × avg_cost × fx)
    avg_cost: float | None = None    # 신규: 주당 평단가 (native 통화)
    source: str | None = "manual_csv"
    metadata: dict[str, Any] = field(default_factory=dict)
```

### `MarkToMarketResult`

```python
@dataclass(frozen=True)
class MarkToMarketResult:
    holdings: list[Holding]          # market_value/cost_basis가 채워진 base 통화 보유분
    total_market_value: float
    total_cost_basis: float
    unrealized_pnl: float
    warnings: list[str]              # price_source / FX_MISSING 경고
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
tests/test_portfolio_mark_to_market.py
tests/test_portfolio_snapshot.py   (확장)
```

필수 테스트:

1. `migrate()`가 `fx_rates` 테이블과 `portfolio_snapshots`의 신규 컬럼을 생성하고, **기존 DB도
   `ADD COLUMN IF NOT EXISTS`로 자동 마이그레이션**된다.
2. `FxRepository.upsert_rates` / `get_latest_rate`가 carry-forward로 최신 환율을 반환한다.
3. `get_latest_close`가 `as_of` 이하 최신 종가를 반환하고, 없으면 None.
4. 시세 자산: `market_value = quantity × close`로 base 통화 평가된다.
5. 가격 없는 시세 자산: `quantity × avg_cost`로 대체되고 경고가 생기며 손익 0.
6. 외화 보유분(KRW)이 `fx_rates`로 base(USD)로 환산된다.
7. 평가손익: `unrealized_pnl = market_value − cost_basis`, 종목별 + 포트폴리오 합계.
8. 현금 일반화: `CASH_KRW`가 import에서 스킵되지 않고, 입력 금액으로 평가되어 USD로 환산된다.
9. FX 통화 산정이 `portfolio_holdings.currency` / CSV currency의 `CASH_<CUR>`를 포함한다.
10. 하위호환: `market_value`만 있는 기존 CSV가 여전히 동작한다(`price_source = "manual"`).
11. `run_portfolio_snapshot()`가 `total_cost_basis`/`unrealized_pnl`을 `portfolio_snapshots`에 기록한다.
12. 부분 실패(일부 종목 가격 없음 + 일부 통화 환율 없음)에도 스냅샷이 완성된다.

---

## Suggested Task Breakdown

### Task 1: FX 스키마 + 스냅샷 손익 컬럼 (additive 마이그레이션)

- Modify: `croesus/db/schema.sql`
- Test: `tests/test_portfolio_snapshot.py`

```bash
git commit -m "🗃️ chore: add fx_rates table and snapshot pnl columns (additive)"
```

### Task 2: FX 수집 모듈 (포트폴리오 비의존)

- Create: `croesus/fx/__init__.py`, `croesus/fx/repository.py`, `croesus/fx/ingest_fx_rates.py`, `croesus/fx/convert.py`
- Test: `tests/test_fx_ingest.py`

```bash
git commit -m "✨ feat: ingest daily FX rates via yfinance"
```

### Task 3: 가격 조회 헬퍼 + 현금 식별 일반화

- Modify: `croesus/prices/repository.py` (`get_latest_close`), `croesus/portfolio/models.py` (`is_cash`),
  `croesus/portfolio/import_holdings.py`, `croesus/portfolio/exposure.py`, `croesus/portfolio/policy.py`
- Test: `tests/test_portfolio_mark_to_market.py`, `tests/test_portfolio_exposure.py`

```bash
git commit -m "✨ feat: add latest-close lookup and generalize CASH_<CUR> handling"
```

### Task 4: Mark-to-Market 레이어

- Create: `croesus/portfolio/mark_to_market.py`
- Modify: `croesus/portfolio/models.py` (`Holding.avg_cost`, `MarkToMarketResult`)
- Test: `tests/test_portfolio_mark_to_market.py`

```bash
git commit -m "✨ feat: mark holdings to market with fx and unrealized pnl"
```

### Task 5: CSV 포맷 확장 (quantity + avg_cost)

- Modify: `croesus/portfolio/import_holdings.py`
- Test: `tests/test_portfolio_snapshot.py`

```bash
git commit -m "✨ feat: import quantity and avg_cost, market_value optional"
```

### Task 6: 잡 통합 + daily_run / 오케스트레이션 배선

- Modify: `croesus/jobs/portfolio_snapshot.py`, `croesus/jobs/daily_run.py`, `croesus/portfolio/repository.py`
- Test: `tests/test_portfolio_snapshot.py`

```bash
git commit -m "✨ feat: wire mark-to-market and fx into portfolio snapshot job"
```

---

## Acceptance Criteria

- 사용자는 현재가를 입력하지 않는다. `quantity`와 `avg_cost`만으로 현재 평가액이 계산된다.
- 시세는 `prices_daily`에서, 환율은 `fx_rates`에서 읽는다(스냅샷 잡은 네트워크 미사용).
- 외화 보유분/현금(`CASH_<CUR>`)이 base 통화로 환산되어 총평가액·비중에 반영된다.
- 종목별 및 포트폴리오 평가손익(`unrealized_pnl`)이 계산·저장된다.
- 가격 또는 환율이 없는 종목이 있어도 스냅샷이 크래시 없이 완성되고, 경고로 명확히 표시된다.
- 기존 `market_value` 기반 CSV가 여전히 동작한다(하위호환).
- exposure/policy 엔진의 집계 로직은 변경되지 않는다(`market_value`만 소비; 현금 식별만 공유 헬퍼로 교체).
- 기존 `storage/croesus.duckdb`가 재생성 없이 `ADD COLUMN IF NOT EXISTS`로 마이그레이션된다.

## Out of Scope

- 실시간/장중 시세(일일 종가 기준).
- 실현 손익(realized P&L), 세금 lot, 배당/수수료 반영.
- 브로커 API 직접 연동(여전히 수동 CSV).
- 종목 자동 등록(보유 종목은 사전에 자산 레지스트리에 등록되어 있어야 함 — 별도 관심사).
- FX 헤지/선물 환율, 통화별 현금 이자.
- 개별 종목 펀더멘털 밸류에이션/DCF (그것은 Sprint 007 *Valuation Layer*의 범위).

## Notes

- yfinance FX 심볼은 `"<QUOTE>=X"` (예: `KRW=X`, `JPY=X`, `EUR=X`). `EUR=X`는 USD per EUR로 방향이
  반대인 경우가 있으므로 수집 시 통화별 방향을 명시적으로 검증/정규화한다.
- 보유 종목이 자산 레지스트리에 없으면(현재 시드는 AAPL/MSFT/NVDA뿐) import 단계에서 스킵된다.
  실제 시연 전 보유 종목 등록 + 가격/환율 수집이 선행되어야 한다.
- `cost_basis`의 의미를 이번 스프린트에서 **base 통화 총 매입원가**로 확정한다(Sprint 004에서는
  미사용 필드였음).
- 명칭 주의: 이 스프린트의 "mark-to-market"은 **보유분 시가평가**다. Sprint 007의 "Valuation Layer"는
  **개별 종목 펀더멘털 내재가치(DCF)**로, 서로 다른 관심사다. 모듈/타입 이름을 섞지 않는다.
