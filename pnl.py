"""
Cost basis, PnL, dan komposisi token untuk posisi concentrated liquidity.

MASALAH YANG DISELESAIKAN DI SINI
---------------------------------
Untuk menghitung PnL dolar yang sesungguhnya ("saya taruh $100, sekarang jadi
berapa") kita butuh HARGA SAAT DEPOSIT, bukan harga sekarang. Cara biasa adalah
membaca state pool di block deposit lewat archive node - tapi RPC publik hampir
selalu memangkas state lama, jadi cara itu tidak bisa diandalkan.

Modul ini memakai dua jalan lain yang TIDAK butuh archive node:

1. DERIVASI ALJABAR (dipakai untuk Uniswap V3)
   Event IncreaseLiquidity memberi (L, amount0, amount1) pada saat mint.
   Untuk posisi in-range berlaku identitas Uniswap:
       amount1 = L * (sqrtP - sqrtPa)
   sehingga harga saat deposit bisa dibalik secara EKSAK:
       sqrtP = sqrtPa + amount1 / L
   Tidak ada estimasi, tidak ada pembacaan state historis - murni aljabar dari
   angka yang sudah tercatat permanen di event log.

2. HARGA DARI EVENT SWAP TERAKHIR (dipakai untuk Uniswap V4)
   Event ModifyLiquidity di V4 TIDAK membawa amount0/amount1 (sudah diverifikasi
   dari IPoolManager.sol - isinya hanya id, sender, tickLower, tickUpper,
   liquidityDelta, salt), jadi cara aljabar di atas tidak bisa dipakai. Tapi
   event Swap V4 membawa sqrtPriceX96. Jadi harga saat deposit diambil dari Swap
   terakhir pada pool itu di block <= block deposit.
   Ini APROKSIMASI: kalau tidak ada swap sejak beberapa block sebelum deposit,
   harga yang dipakai sedikit basi. Untuk pool meme yang ramai, biasanya
   selisihnya kecil, tapi ini tetap sumber galat yang harus kamu tahu.

Semua fungsi di sini murni matematika - tidak menyentuh jaringan, jadi bisa
diuji penuh tanpa RPC (lihat test_pnl.py).
"""

from decimal import Decimal

import uniswap_math as um

# Alasan kenapa sebuah cost basis tidak bisa dihitung - dipakai untuk memberi
# label jujur di output, bukan diam-diam menampilkan angka salah.
BASIS_EXACT = "exact"            # derivasi aljabar dari amount1 (V3, deposit in-range)
BASIS_EXACT_AMOUNT0 = "exact0"   # derivasi aljabar dari amount0 (V3, deposit in-range)
BASIS_SWAP_APPROX = "swap"       # dari event Swap terdekat (V4)
BASIS_UNKNOWN = "unknown"        # deposit single-sided / data kurang -> tidak bisa


def sqrt_price_at_deposit_from_amounts(liquidity: int, tick_lower: int, tick_upper: int,
                                       amount0: int, amount1: int):
    """
    Balikkan harga saat deposit dari (L, amount0, amount1) sebuah event
    IncreaseLiquidity. Return (sqrt_price_ratio, metode) atau (None, BASIS_UNKNOWN).

    sqrt_price_ratio yang dikembalikan adalah sqrt(P) dalam satuan RASIO MENTAH
    (bukan X96, bukan dikoreksi desimal) - sama dengan basis yang dipakai
    uniswap_math.amounts_for_liquidity.
    """
    if liquidity <= 0:
        return None, BASIS_UNKNOWN

    sqrt_a = Decimal("1.0001") ** (Decimal(tick_lower) / 2)
    sqrt_b = Decimal("1.0001") ** (Decimal(tick_upper) / 2)
    L = Decimal(liquidity)

    # Kasus utama: deposit in-range, kedua sisi terisi.
    if amount1 > 0 and amount0 > 0:
        # amount1 = L * (sqrtP - sqrtPa)  ->  sqrtP = sqrtPa + amount1/L
        sqrt_p = sqrt_a + Decimal(amount1) / L
        if sqrt_a <= sqrt_p <= sqrt_b:
            return sqrt_p, BASIS_EXACT
        # kalau di luar batas berarti asumsi in-range salah; coba lewat amount0
        # amount0 = L*(sqrtPb - sqrtP)/(sqrtP*sqrtPb) -> sqrtP = L*sqrtPb/(amount0*sqrtPb + L)
        denom = Decimal(amount0) * sqrt_b + L
        if denom > 0:
            sqrt_p = L * sqrt_b / denom
            if sqrt_a <= sqrt_p <= sqrt_b:
                return sqrt_p, BASIS_EXACT_AMOUNT0
        return None, BASIS_UNKNOWN

    # Deposit single-sided: harga hanya bisa dibatasi, tidak ditentukan.
    # Kita TIDAK menebak - lebih baik jujur "unknown" daripada PnL yang salah.
    return None, BASIS_UNKNOWN


def sqrt_ratio_to_price(sqrt_ratio: Decimal, decimals0: int, decimals1: int) -> Decimal:
    """sqrt(P) rasio mentah -> harga token1 per token0 dalam unit manusia."""
    return (sqrt_ratio * sqrt_ratio) * (Decimal(10) ** (decimals0 - decimals1))


def usd_price_from_raw(raw_price_t1_per_t0: Decimal, usdg_is_token0: bool) -> Decimal:
    """
    Ubah harga mentah (token1 per token0) jadi harga USD token non-USDG,
    dengan asumsi USDG = $1.
    """
    if usdg_is_token0:
        if raw_price_t1_per_t0 == 0:
            return Decimal(0)
        return Decimal(1) / raw_price_t1_per_t0
    return raw_price_t1_per_t0


def deposit_value_usd(amount0: int, amount1: int, decimals0: int, decimals1: int,
                      usdg_is_token0: bool, other_price_usd: Decimal) -> Decimal:
    """Nilai USD sebuah deposit, dihargai pada harga SAAT deposit itu terjadi."""
    a0 = Decimal(amount0) / (Decimal(10) ** decimals0)
    a1 = Decimal(amount1) / (Decimal(10) ** decimals1)
    if usdg_is_token0:
        return a0 + a1 * other_price_usd   # token0 = USDG ($1), token1 = meme
    return a0 * other_price_usd + a1       # token0 = meme, token1 = USDG ($1)


def composition(amount0: Decimal, amount1: Decimal, usdg_is_token0: bool,
                other_price_usd: Decimal):
    """
    Komposisi posisi saat ini. Return (pct_meme, pct_usdg, value_meme_usd,
    value_usdg_usd) - persentase berbasis NILAI USD, bukan jumlah token, karena
    itu yang relevan untuk risiko.

    pct_meme tinggi (mendekati 100%) = harga sudah jatuh ke batas bawah range,
    posisi hampir seluruhnya berubah jadi meme token -> ini sinyal alert.
    """
    if usdg_is_token0:
        usdg_val = amount0
        meme_val = amount1 * other_price_usd
    else:
        meme_val = amount0 * other_price_usd
        usdg_val = amount1
    total = meme_val + usdg_val
    if total <= 0:
        return Decimal(0), Decimal(0), Decimal(0), Decimal(0)
    return (meme_val / total * 100), (usdg_val / total * 100), meme_val, usdg_val


def build_cost_basis(deposits, decimals0: int, decimals1: int, usdg_is_token0: bool):
    """
    Gabungkan beberapa event deposit jadi satu cost basis.

    'deposits' adalah list dict:
        {amount0, amount1, entry_price_usd (Decimal|None), basis_method}

    Tiap deposit dihargai pada harga saat deposit ITU SENDIRI (weighted average
    cost yang benar), bukan pada satu harga rata-rata yang disamaratakan.

    Return dict berisi total invested USD, jumlah token yang disetor, dan
    'quality': 'exact' kalau SEMUA deposit punya harga masuk yang bisa
    ditentukan, 'partial' kalau sebagian, 'unknown' kalau tidak ada sama sekali.
    Kualitas ini WAJIB ditampilkan ke user - PnL dari basis 'partial' itu
    understated dan menyesatkan kalau dianggap final.
    """
    total_usd = Decimal(0)
    dep0 = Decimal(0)
    dep1 = Decimal(0)
    n_known = 0
    n_total = 0
    methods = set()

    for d in deposits:
        n_total += 1
        dep0 += Decimal(d["amount0"]) / (Decimal(10) ** decimals0)
        dep1 += Decimal(d["amount1"]) / (Decimal(10) ** decimals1)
        if d.get("entry_price_usd") is None:
            continue
        n_known += 1
        methods.add(d.get("basis_method", BASIS_UNKNOWN))
        total_usd += deposit_value_usd(
            d["amount0"], d["amount1"], decimals0, decimals1,
            usdg_is_token0, d["entry_price_usd"],
        )

    if n_total == 0:
        quality = "unknown"
    elif n_known == n_total:
        quality = "exact" if methods <= {BASIS_EXACT, BASIS_EXACT_AMOUNT0} else "approx"
    elif n_known == 0:
        quality = "unknown"
    else:
        quality = "partial"

    return {
        "invested_usd": total_usd if n_known else None,
        "deposited0": dep0,
        "deposited1": dep1,
        "quality": quality,
        "deposits_priced": n_known,
        "deposits_total": n_total,
    }


def compute_pnl(current_value_usd: Decimal, fees_uncollected_usd: Decimal,
                fees_collected_usd: Decimal, invested_usd):
    """
    PnL absolut ala dashboard: (nilai posisi sekarang + semua fee) - modal masuk.
    Fee yang sudah diklaim IKUT dihitung karena itu uang yang sudah kamu terima.
    Return (pnl_usd, pnl_pct) atau (None, None) kalau cost basis tidak diketahui.
    """
    if invested_usd is None or invested_usd <= 0:
        return None, None
    total_now = current_value_usd + fees_uncollected_usd + fees_collected_usd
    pnl = total_now - invested_usd
    return pnl, (pnl / invested_usd * 100)
