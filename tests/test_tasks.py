from market_intelligence.tasks.health import health_ping


def test_health_ping_has_no_side_effects() -> None:
    assert health_ping.run() == {"status": "ok"}
