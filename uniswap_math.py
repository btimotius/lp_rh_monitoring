"""
Matematika Uniswap V3/V4 yang dibutuhkan untuk membaca posisi:
- konversi tick <-> harga
- jumlah token dari liquidity (standar Uniswap V3 whitepaper, dipakai juga di V4
  karena core AMM math-nya sama)
- decode PositionInfo (V4, packed uint256) - layout sesuai
  v4-periphery/src/libraries/PositionInfoLibrary.sol
- hitung PoolId V4 - sesuai v4-core/src/types/PoolId.sol: keccak256(abi.encode(poolKey))

Bagian ini murni matematika/encoding, tidak menyentuh jaringan - bisa & sudah
di-unit-test tanpa RPC (lihat test_uniswap_math.py).
"""

from eth_abi import encode as abi_encode
from eth_utils import keccak
from decimal import Decimal, getcontext

getcontext().prec = 50

Q96 = Decimal(2) ** 96
Q128 = Decimal(2) ** 128


def sqrt_price_x96_to_price(sqrt_price_x96: int, decimals0: int, decimals1: int) -> Decimal:
    """Harga token1 per token0, dalam unit manusia (sudah dikoreksi desimal)."""
    sp = Decimal(sqrt_price_x96) / Q96
    raw = sp * sp  # token1/token0 dalam unit terkecil (wei-equivalent)
    return raw * (Decimal(10) ** (decimals0 - decimals1))


def tick_to_price(tick: int, decimals0: int, decimals1: int) -> Decimal:
    """Harga token1 per token0 dari tick (dipakai untuk lower/upper range)."""
    raw = Decimal("1.0001") ** tick
    return raw * (Decimal(10) ** (decimals0 - decimals1))


def sign_extend_24(value: int) -> int:
    """Sign-extend angka 24-bit unsigned menjadi int24 Python biasa."""
    value &= 0xFFFFFF
    if value & 0x800000:
        value -= 0x1000000
    return value


def decode_position_info_v4(info_packed: int):
    """
    Layout (v4-periphery PositionInfoLibrary):
      bit 0-7    : hasSubscriber flag
      bit 8-31   : tickLower (int24)
      bit 32-55  : tickUpper (int24)
      bit 56-255 : poolId terpotong (25 byte) - JANGAN dipakai untuk query StateView,
                   pakai poolKey dari getPoolAndPositionInfo lalu hitung ulang full
                   poolId lewat compute_pool_id_v4().
    """
    tick_lower = sign_extend_24((info_packed >> 8) & 0xFFFFFF)
    tick_upper = sign_extend_24((info_packed >> 32) & 0xFFFFFF)
    has_subscriber = bool(info_packed & 0xFF)
    return tick_lower, tick_upper, has_subscriber


def compute_pool_id_v4(currency0: str, currency1: str, fee: int, tick_spacing: int, hooks: str) -> bytes:
    """
    poolId = keccak256(abi.encode(currency0, currency1, fee, tickSpacing, hooks))
    Sesuai v4-core PoolId.toId(). Return 32 byte poolId siap dipakai ke StateView.
    """
    encoded = abi_encode(
        ["address", "address", "uint24", "int24", "address"],
        [
            _cs(currency0),
            _cs(currency1),
            fee,
            tick_spacing,
            _cs(hooks),
        ],
    )
    return keccak(encoded)


def _cs(addr: str) -> str:
    """checksum-agnostic helper - eth_abi cuma butuh string hex valid."""
    return addr


def amounts_for_liquidity(sqrt_price_x96: int, tick_lower: int, tick_upper: int, liquidity: int):
    """
    Jumlah token0/token1 (dalam unit terkecil / wei) yang direpresentasikan oleh
    'liquidity' pada harga sqrt_price_x96 saat ini, dengan range [tick_lower, tick_upper].
    Formula standar Uniswap V3 whitepaper (LiquidityAmounts.sol).
    """
    sqrt_ratio_a = Decimal("1.0001") ** (Decimal(tick_lower) / 2)
    sqrt_ratio_b = Decimal("1.0001") ** (Decimal(tick_upper) / 2)
    sqrt_price = Decimal(sqrt_price_x96) / Q96

    if sqrt_ratio_a > sqrt_ratio_b:
        sqrt_ratio_a, sqrt_ratio_b = sqrt_ratio_b, sqrt_ratio_a

    liq = Decimal(liquidity)

    if sqrt_price <= sqrt_ratio_a:
        amount0 = liq * (sqrt_ratio_b - sqrt_ratio_a) / (sqrt_ratio_a * sqrt_ratio_b)
        amount1 = Decimal(0)
    elif sqrt_price < sqrt_ratio_b:
        amount0 = liq * (sqrt_ratio_b - sqrt_price) / (sqrt_price * sqrt_ratio_b)
        amount1 = liq * (sqrt_price - sqrt_ratio_a)
    else:
        amount0 = Decimal(0)
        amount1 = liq * (sqrt_ratio_b - sqrt_ratio_a)

    return int(amount0), int(amount1)


def fee_growth_diff_uint256(growth_now: int, growth_last: int) -> int:
    """
    feeGrowth di Solidity itu uint256 yang sengaja dibiarkan overflow/wrap (unchecked).
    Python int tidak wrap otomatis, jadi harus di-mask manual ke 256 bit supaya hasil
    pengurangan match dengan perilaku on-chain walau growth_now 'lebih kecil' karena wrap.
    """
    return (growth_now - growth_last) % (2**256)


def uncollected_fee_from_growth(liquidity: int, growth_now: int, growth_last: int) -> int:
    diff = fee_growth_diff_uint256(growth_now, growth_last)
    return (diff * liquidity) // (2**128)
