"""한국어 라벨 계층(croesus.web.labels) 검증."""
from __future__ import annotations

from croesus.web import labels


def test_known_values_translate_to_korean():
    assert labels.regime_label("Goldilocks") == "골디락스"
    assert labels.positioning_label("Defensive") == "방어적"
    assert labels.action_label("block_new_buy") == "신규 매수 차단"
    assert labels.bucket_label("candidate") == "후보"
    assert labels.gate_label("block") == "편입 불가"
    assert labels.exposure_type_label("currency") == "통화"
    assert labels.sleeve_label("defensive_bonds") == "방어 채권"
    assert labels.reason_code_label("SECTOR_OVER_MAX") == "섹터 비중이 상한을 넘었습니다"
    assert labels.tx_type_label("buy") == "매수"
    assert labels.field_label("max_monthly_turnover") == "월 매매 한도"


def test_reason_codes_joined():
    out = labels.reason_codes_label(["POSITION_OVER_MAX", "TURNOVER_LIMIT"])
    assert "단일 종목 비중이 상한을 넘었습니다" in out
    assert "월 매매 한도에 걸려 규모를 줄였습니다" in out
    assert "·" in out


def test_tone_helpers():
    assert labels.positioning_tone("Defensive") == "bad"
    assert labels.bucket_tone("candidate") == "ok"
    assert labels.grade_value_tone("high") == "bad"   # 파괴 위험 높음 = 나쁨
    assert labels.grade_value_tone("wide") == "ok"


def test_unknown_value_falls_back_without_crashing():
    # 매핑에 없는 값은 사람이 읽을 수 있게 최소 변환만 하고 깨지지 않는다
    assert labels.action_label("some_new_action") == "some new action"
    assert labels.regime_label(None) == "—"
    assert labels.reason_code_label("UNKNOWN_CODE") == "UNKNOWN CODE"


def test_all_filters_registered():
    expected = {
        "regime_label", "positioning_label", "positioning_guidance", "action_label",
        "reason_codes_label", "bucket_label", "gate_label", "grade_value_label",
        "exposure_type_label", "sleeve_label", "tx_type_label", "field_label",
    }
    assert expected.issubset(set(labels.JINJA_FILTERS))
