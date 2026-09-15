"""
Unit test untuk pnl.py - murni matematika, tanpa RPC.
Inti pembuktiannya: kalau kita SIMULASIKAN sebuah deposit pada harga X (pakai
rumus resmi Uniswap amounts_for_liquidity), lalu suruh pnl.py menebak harga
deposit itu HANYA dari (L, amount0, amount1) - apakah dia mengembalikan X lagi?
Kalau ya, derivasi aljabarnya benar.

Jalankan: python3 test_pnl.py
"""
from decimal import Decimal

import uniswap_math as um
import pnl


def approx_rel(a, b, tol=Decimal("1e-9")):
    a, b = Decimal(a), Decimal(b)
    if b == 0:
        return abs(a) <= tol
    return abs(a - b) / abs(b) <= tol


def simulate_deposit(tick_lower, tick_upper, tick_at_deposit, liquidity):
    """Hasilkan (amount0, amount1) persis seperti yang akan tercatat di event."""
    sqrt_x96 = int((Decimal("1.0001") ** (Decimal(tick_at_deposit) / 2)) * um.Q96)
    a0, a1 = um.amounts_for_liquidity(sqrt_x96, tick_lower, tick_upper, liquidity)
    return a0, a1


def test_roundtrip_entry_price_center():
    """Deposit tepat di tengah range -> harga masuk harus terekonstruksi persis."""
    tl, tu, td = -1000, 1000, 0
    L = 10**20
    a0, a1 = simulate_deposit(tl, tu, td, L)
    sqrt_p, method = pnl.sqrt_price_at_deposit_from_amounts(L, tl, tu, a0, a1)
    assert sqrt_p is not None, "harusnya bisa dihitung"
    expected = Decimal("1.0001") ** (Decimal(td) / 2)
    assert approx_rel(sqrt_p, expected, Decimal("1e-6")), (sqrt_p, expected)
    assert method == pnl.BASIS_EXACT


def test_roundtrip_entry_price_various_positions_in_range():
    """Coba banyak titik dalam range, termasuk dekat batas."""
    tl, tu = -5000, 3000
    L = 7 * 10**19
    for td in (-4900, -3000, -1000, 0, 1500, 2900):
        a0, a1 = simulate_deposit(tl, tu, td, L)
        if a0 == 0 or a1 == 0:
            continue
        sqrt_p, method = pnl.sqrt_price_at_deposit_from_amounts(L, tl, tu, a0, a1)
        expected = Decimal("1.0001") ** (Decimal(td) / 2)
        assert sqrt_p is not None, f"gagal di tick {td}"
        assert approx_rel(sqrt_p, expected, Decimal("1e-6")), (td, sqrt_p, expected)


def test_single_sided_deposit_returns_unknown():
    """Deposit di luar range (satu sisi) -> harus jujur bilang tidak tahu,
    BUKAN menebak angka."""
    tl, tu = 1000, 2000
    L = 10**20
    a0, a1 = simulate_deposit(tl, tu, 0, L)   # harga di bawah range -> a1 == 0
    assert a1 == 0
    sqrt_p, method = pnl.sqrt_price_at_deposit_from_amounts(L, tl, tu, a0, a1)
    assert sqrt_p is None
    assert method == pnl.BASIS_UNKNOWN


def test_zero_liquidity_guard():
    sqrt_p, method = pnl.sqrt_price_at_deposit_from_amounts(0, -100, 100, 5, 5)
    assert sqrt_p is None and method == pnl.BASIS_UNKNOWN


def test_usd_price_inversion():
    raw = Decimal("0.0031")  # token1 per token0
    # USDG = token1 -> harga mentah SUDAH harga USD token0
    assert pnl.usd_price_from_raw(raw, usdg_is_token0=False) == raw
    # USDG = token0 -> harus dibalik
    assert approx_rel(pnl.usd_price_from_raw(raw, usdg_is_token0=True), Decimal(1) / raw)


def test_composition_basic():
    # 60 USDG + 40 USD worth of meme (100 meme @ $0.40)
    pct_meme, pct_usdg, v_meme, v_usdg = pnl.composition(
        Decimal(100), Decimal(60), usdg_is_token0=False, other_price_usd=Decimal("0.40")
    )
    assert approx_rel(v_meme, Decimal(40)) and approx_rel(v_usdg, Decimal(60))
    assert approx_rel(pct_meme, Decimal(40)) and approx_rel(pct_usdg, Decimal(60))


def test_composition_all_meme_when_price_at_bottom():
    """Posisi yang jatuh ke batas bawah = 100% meme -> ini yang memicu alert."""
    pct_meme, pct_usdg, _, _ = pnl.composition(
        Decimal(1000), Decimal(0), usdg_is_token0=False, other_price_usd=Decimal("0.05")
    )
    assert pct_meme == 100 and pct_usdg == 0


def test_composition_zero_total_guard():
    assert pnl.composition(Decimal(0), Decimal(0), False, Decimal(1)) == (
        Decimal(0), Decimal(0), Decimal(0), Decimal(0))


def test_cost_basis_weighted_across_two_deposits():
    """Dua deposit di harga berbeda harus dijumlah pada harganya MASING-MASING."""
    deposits = [
        # 1000 meme (18dec) + 100 USDG (6dec) saat meme = $0.10 -> 100 + 100 = $200
        {"amount0": 1000 * 10**18, "amount1": 100 * 10**6,
         "entry_price_usd": Decimal("0.10"), "basis_method": pnl.BASIS_EXACT},
        # 500 meme + 100 USDG saat meme = $0.20 -> 100 + 100 = $200
        {"amount0": 500 * 10**18, "amount1": 100 * 10**6,
         "entry_price_usd": Decimal("0.20"), "basis_method": pnl.BASIS_EXACT},
    ]
    cb = pnl.build_cost_basis(deposits, 18, 6, usdg_is_token0=False)
    assert approx_rel(cb["invested_usd"], Decimal(400)), cb["invested_usd"]
    assert cb["quality"] == "exact"
    assert approx_rel(cb["deposited0"], Decimal(1500))
    assert approx_rel(cb["deposited1"], Decimal(200))


def test_cost_basis_partial_quality_flag():
    """Kalau satu deposit tidak bisa dihargai, kualitas harus 'partial' -
    supaya user tahu PnL-nya understated, bukan diam-diam salah."""
    deposits = [
        {"amount0": 1000 * 10**18, "amount1": 100 * 10**6,
         "entry_price_usd": Decimal("0.10"), "basis_method": pnl.BASIS_EXACT},
        {"amount0": 500 * 10**18, "amount1": 0,
         "entry_price_usd": None, "basis_method": pnl.BASIS_UNKNOWN},
    ]
    cb = pnl.build_cost_basis(deposits, 18, 6, usdg_is_token0=False)
    assert cb["quality"] == "partial"
    assert cb["deposits_priced"] == 1 and cb["deposits_total"] == 2


def test_cost_basis_approx_quality_for_v4_swap_method():
    deposits = [{"amount0": 10 * 10**18, "amount1": 10 * 10**6,
                 "entry_price_usd": Decimal("1"), "basis_method": pnl.BASIS_SWAP_APPROX}]
    cb = pnl.build_cost_basis(deposits, 18, 6, usdg_is_token0=False)
    assert cb["quality"] == "approx"


def test_pnl_includes_collected_fees():
    # invested $100, posisi sekarang $80, fee belum diklaim $5, sudah diklaim $30
    # -> total sekarang $115, PnL +$15 (+15%)
    p, pct = pnl.compute_pnl(Decimal(80), Decimal(5), Decimal(30), Decimal(100))
    assert approx_rel(p, Decimal(15)) and approx_rel(pct, Decimal(15))


def test_pnl_negative_when_il_exceeds_fees():
    p, pct = pnl.compute_pnl(Decimal(60), Decimal(2), Decimal(8), Decimal(100))
    assert p == Decimal(-30) and pct == Decimal(-30)


def test_pnl_none_when_basis_unknown():
    assert pnl.compute_pnl(Decimal(80), Decimal(1), Decimal(1), None) == (None, None)


def test_deposit_value_usd_both_orientations():
    # meme=token0 (1000 @ $0.10), usdg=token1 (50) -> $150
    v = pnl.deposit_value_usd(1000 * 10**18, 50 * 10**6, 18, 6, False, Decimal("0.10"))
    assert approx_rel(v, Decimal(150)), v
    # usdg=token0 (50), meme=token1 (1000 @ $0.10) -> $150
    v = pnl.deposit_value_usd(50 * 10**6, 1000 * 10**18, 6, 18, True, Decimal("0.10"))
    assert approx_rel(v, Decimal(150)), v


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
