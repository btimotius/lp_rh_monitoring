"""
Unit test murni-matematika (tanpa RPC) untuk uniswap_math.py.
Jalankan: python3 test_uniswap_math.py
"""
from decimal import Decimal
import uniswap_math as um


def approx(a, b, tol=Decimal("0.0001")):
    return abs(Decimal(a) - Decimal(b)) <= tol


def test_tick_zero_equal_decimals():
    # tick 0 -> price 1.0 kalau desimal token0==token1
    p = um.tick_to_price(0, 18, 18)
    assert approx(p, 1), p


def test_sqrt_price_matches_tick_price():
    # sqrtPriceX96 untuk tick T harusnya kasih harga yang sama dengan tick_to_price(T)
    tick = 12345
    sqrt_ratio = Decimal("1.0001") ** (Decimal(tick) / 2)
    sqrt_price_x96 = int(sqrt_ratio * um.Q96)
    p1 = um.tick_to_price(tick, 18, 6)
    p2 = um.sqrt_price_x96_to_price(sqrt_price_x96, 18, 6)
    assert approx(p1, p2, tol=Decimal("0.001")), (p1, p2)


def test_sign_extend_24():
    assert um.sign_extend_24(0) == 0
    assert um.sign_extend_24(0x000001) == 1
    assert um.sign_extend_24(0xFFFFFF) == -1  # -1 dalam int24
    assert um.sign_extend_24(0x800000) == -8388608  # nilai negatif terkecil int24


def test_decode_position_info_roundtrip():
    tick_lower = -1000
    tick_upper = 2000
    packed = ((tick_upper & 0xFFFFFF) << 32) | ((tick_lower & 0xFFFFFF) << 8) | 1
    tl, tu, has_sub = um.decode_position_info_v4(packed)
    assert tl == tick_lower, tl
    assert tu == tick_upper, tu
    assert has_sub is True


def test_amounts_for_liquidity_in_range_symmetry():
    # Kalau harga persis di tengah-tengah (secara sqrt) range, amount0 dan amount1
    # harus > 0 keduanya (posisi in-range dua sisi terisi)
    tick_lower, tick_upper = -1000, 1000
    tick_mid = 0
    sqrt_price_x96 = int((Decimal("1.0001") ** (Decimal(tick_mid) / 2)) * um.Q96)
    a0, a1 = um.amounts_for_liquidity(sqrt_price_x96, tick_lower, tick_upper, 10**18)
    assert a0 > 0 and a1 > 0, (a0, a1)


def test_amounts_for_liquidity_out_of_range_below():
    # Harga di bawah range -> semua di token0, token1 = 0
    tick_lower, tick_upper = 1000, 2000
    tick_now = 0
    sqrt_price_x96 = int((Decimal("1.0001") ** (Decimal(tick_now) / 2)) * um.Q96)
    a0, a1 = um.amounts_for_liquidity(sqrt_price_x96, tick_lower, tick_upper, 10**18)
    assert a0 > 0 and a1 == 0, (a0, a1)


def test_amounts_for_liquidity_out_of_range_above():
    tick_lower, tick_upper = -2000, -1000
    tick_now = 0
    sqrt_price_x96 = int((Decimal("1.0001") ** (Decimal(tick_now) / 2)) * um.Q96)
    a0, a1 = um.amounts_for_liquidity(sqrt_price_x96, tick_lower, tick_upper, 10**18)
    assert a0 == 0 and a1 > 0, (a0, a1)


def test_fee_growth_wraparound():
    # growth_now 'lebih kecil' dari growth_last karena wrap uint256 - hasil harus tetap
    # non-negative dan masuk akal (bukan exception / bukan angka raksasa negatif)
    growth_last = (2**256) - 5
    growth_now = 10
    diff = um.fee_growth_diff_uint256(growth_now, growth_last)
    assert diff == 15, diff  # wrap: (10 - (2^256-5)) mod 2^256 = 15


def test_compute_pool_id_v4_deterministic_and_order_sensitive():
    id1 = um.compute_pool_id_v4(
        "0x0000000000000000000000000000000000000001",
        "0x0000000000000000000000000000000000000002",
        3000, 60,
        "0x0000000000000000000000000000000000000000",
    )
    id2 = um.compute_pool_id_v4(
        "0x0000000000000000000000000000000000000001",
        "0x0000000000000000000000000000000000000002",
        3000, 60,
        "0x0000000000000000000000000000000000000000",
    )
    id3 = um.compute_pool_id_v4(
        "0x0000000000000000000000000000000000000002",  # tukar urutan currency
        "0x0000000000000000000000000000000000000001",
        3000, 60,
        "0x0000000000000000000000000000000000000000",
    )
    assert id1 == id2, "poolId harus deterministik untuk input sama"
    assert id1 != id3, "poolId harus beda kalau urutan currency0/1 tertukar"
    assert len(id1) == 32


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
    if failed:
        raise SystemExit(1)
