# 로드맵 ③ — 종목 이벤트 스터디 (PEAD류)

종목 단위 사건(가격 급변, 거래량 급증) 이후 [T+1, T+60]에 사건 방향의 유의한 누적초과수익
표류(drift)가 존재하는지 30년·521종목·수만 건 표본으로 검증한다. 1차 이벤트 실험(거시 사건
n=3~4)의 검정력 문제를 종목 이벤트로 해소하는 실험.

## 실행

```bash
# 저장소 루트에서 — 521종목, 1990~, 이벤트 ~14만 건(dedup 후)
python3 -m experiments.market_signals.event_drift.run
# 스모크:
ED_MAX_ASSETS=25 ED_START_YEAR=2015 python3 -m experiments.market_signals.event_drift.run
```

①의 30년 스크래치 DB(`results/cross_sectional/long_history.duckdb`)를 재사용하므로
①의 history 수집이 선행돼야 한다. **프로덕션 DB는 열지 않는다.**

## 설계 결정 — 3b 변형 (이벤트 소급 재계산)

로드맵 원문의 3b(프로덕션 `events` 테이블 사용)는 불가능했다: `events`는 5일치(2026-06-26~)만
존재(역사화 갭), 3a용 `disclosures`는 0행. 대신 `croesus/events/detectors.py`의 가격 기반 규칙
2종을 동일 파라미터로 30년 이력에 소급 재계산했다:

- `abnormal_return` — |일수익률| ≥ 3 × trailing 63일 변동성 (방향 = 부호)
- `abnormal_volume` — 거래량 z ≥ 2 vs trailing 21일 평균/표준편차 (상방만)

두 규칙 모두 **trailing 윈도만 사용하므로 소급 적용에 look-ahead가 없다.** 같은 (자산, 유형)
내 21거래일 이내 후속 이벤트는 제거(dedup)해 표본 자기중첩을 줄였다.

## 방법

1. **CAAR**: 시장(같은 유니버스 EW)조정 CAR[T+1, T+h], h=1..60. 같은 날 이벤트들의 CAR을
   날짜 평균으로 축약(교차상관 처리)한 뒤 날짜 시계열에 Newey-West(lags=h) t.
2. **Placebo**: 자산별 이벤트 개수를 보존한 무작위 날짜(seed 42)로 동일 파이프라인 → 귀무 대조.
3. **크기 단조성**: |magnitude| 5분위별 CAAR — 진짜 PEAD면 서프라이즈가 클수록 drift가 커야 한다.
4. **Tradability**: calendar-time 포트폴리오(이벤트 후 [T+1, T+hold] 보유, hold∈{5,21}, 총노출 1
   정규화, 비용 {0,10}bps × |Δw|). return 이벤트는 방향 롱숏, volume 이벤트는 롱온리.

## 산출물 (`results/event_drift/`, gitignore)

- `events_summary.csv` — 유형×방향 이벤트 수 (raw/dedup)
- `caar_<type>_<dir>.csv` — CAAR(h)/t + placebo 병기
- `magnitude_quintiles.csv` — 크기 분위별 CAAR (h=5/21/60)
- `portfolio.csv` — 북×hold×비용 성과

## 한계 (자기기만 방지)

- **진짜 PEAD가 아니다** — 컨센서스(애널리스트 추정치)가 저장소에 없어 어닝 서프라이즈 기반
  검증(3c)은 불가. 가격·거래량 충격 후 표류만 다룬다.
- **섹터 중립화 생략** (스크래치 DB에 섹터 없음) — 시장조정까지만.
- **survivorship** — 생존 유니버스라 롱온리 수익 수준은 상방편향, 폭락 지속은 과소평가 가능.
- **이벤트 클러스터링 잔존** — 날짜 군집 + NW로 추론은 보수적으로 처리했으나 위기 집중 자체는 남는다.

결과 해석은 `FINDINGS.md` 참조.
