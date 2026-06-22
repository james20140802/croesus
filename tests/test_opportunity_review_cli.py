from croesus.jobs.opportunity_review import _build_parser


def test_parser_has_risk_gate_args():
    parser = _build_parser()
    args = parser.parse_args(
        ["--methodology", "moat_adjusted_intrinsic_value",
         "--portfolio-id", "p1", "--profile-id", "pr1",
         "--no-risk-gate", "--min-liquidity-usd", "2000000"]
    )
    assert args.portfolio_id == "p1"
    assert args.profile_id == "pr1"
    assert args.apply_risk_gate is False
    assert args.min_liquidity_usd == 2000000.0


def test_parser_risk_gate_defaults():
    parser = _build_parser()
    args = parser.parse_args(["--methodology", "moat_adjusted_intrinsic_value"])
    assert args.portfolio_id == "default"
    assert args.profile_id == "default"
    assert args.apply_risk_gate is True
