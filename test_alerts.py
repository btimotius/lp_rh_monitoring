"""Unit test mesin alert - fokus pada histeresis (anti-spam). Tanpa RPC."""

import alerts


def mk(key, pct, in_range=True, pair="MEME/USDG"):
    return {"key": key, "pair": pair, "pct_meme": pct, "in_range": in_range,
            "protocol": "uniswap_v3", "token_id": 1}


def test_first_cross_fires():
    st = {}
    out = alerts.evaluate_positions([mk("a", 78)], st)
    assert len(out) == 1 and out[0]["level"] == "warning"


def test_below_threshold_silent():
    st = {}
    assert alerts.evaluate_positions([mk("a", 60)], st) == []


def test_no_repeat_while_staying_above():
    """Inti histeresis: 5 run berturut-turut di atas ambang = 1 alert, bukan 5."""
    st = {}
    fired = 0
    for pct in (78, 79, 77, 80, 76):
        fired += len(alerts.evaluate_positions([mk("a", pct)], st))
    assert fired == 1, f"harusnya 1 alert, dapat {fired}"


def test_escalation_to_critical_fires_again():
    st = {}
    alerts.evaluate_positions([mk("a", 78)], st)
    out = alerts.evaluate_positions([mk("a", 93)], st)
    assert len(out) == 1 and out[0]["level"] == "critical"
    assert out[0]["from_level"] == "warning"


def test_oor_fires_even_from_critical():
    st = {}
    alerts.evaluate_positions([mk("a", 93)], st)
    out = alerts.evaluate_positions([mk("a", 100, in_range=False)], st)
    assert len(out) == 1 and out[0]["level"] == "oor"


def test_oscillation_near_threshold_does_not_refire():
    """74.9 / 75.1 bolak-balik tidak boleh memicu alert berulang."""
    st = {}
    fired = len(alerts.evaluate_positions([mk("a", 75.1)], st))
    for pct in (74.9, 75.2, 74.5, 75.5, 73.0):
        fired += len(alerts.evaluate_positions([mk("a", pct)], st))
    assert fired == 1, f"harusnya 1 alert, dapat {fired}"


def test_real_recovery_allows_refire_later():
    """Turun jauh di bawah reset margin = benar-benar pulih; naik lagi boleh alert."""
    st = {}
    alerts.evaluate_positions([mk("a", 78)], st)
    alerts.evaluate_positions([mk("a", 55)], st)      # pulih (di bawah 75-5=70)
    out = alerts.evaluate_positions([mk("a", 79)], st)
    assert len(out) == 1, "setelah pulih beneran, alert baru harus boleh keluar"


def test_closed_position_pruned_from_state():
    st = {}
    alerts.evaluate_positions([mk("a", 78), mk("b", 50)], st)
    assert set(st["alerts"]) == {"a", "b"}
    alerts.evaluate_positions([mk("a", 78)], st)
    assert set(st["alerts"]) == {"a"}, "posisi yang sudah ditutup harus dibersihkan"


def test_independent_positions_do_not_interfere():
    st = {}
    out = alerts.evaluate_positions([mk("a", 78), mk("b", 40), mk("c", 95)], st)
    levels = sorted(o["level"] for o in out)
    assert levels == ["critical", "warning"], levels


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"OK   {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{len(tests)-failed}/{len(tests)} test lulus")
    raise SystemExit(1 if failed else 0)
