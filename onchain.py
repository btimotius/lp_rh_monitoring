"""
Pembacaan posisi LP dari Robinhood Chain, lengkap dengan cost basis untuk PnL.

Semua di sini read-only (eth_call / eth_getLogs). Tidak ada fungsi yang bisa
mengirim transaksi atau memindahkan dana - secara desain, bukan kebetulan.

PERBEDAAN PENTING V3 vs V4 (dua-duanya sudah diverifikasi ke source resmi):

  V3 - NonfungiblePositionManager mendukung enumerasi ERC721
       (balanceOf + tokenOfOwnerByIndex), jadi posisi ketemu tanpa scan log.
       Event IncreaseLiquidity membawa (liquidity, amount0, amount1), sehingga
       harga saat deposit bisa DIBALIK SECARA EKSAK (lihat pnl.py).
       Fee belum diklaim didapat dari simulasi eth_call ke collect() - persis
       sama dengan yang akan kamu terima kalau collect sungguhan.
       Fee sudah diklaim dijumlahkan dari event Collect.

  V4 - PositionManager TIDAK punya enumerasi (dikonfirmasi dari docs resmi:
       hanya getPoolAndPositionInfo / getPositionLiquidity). Jadi posisi ketemu
       lewat scan event Transfer, disimpan bertahap di state.
       Event ModifyLiquidity TIDAK membawa amount0/amount1 (dikonfirmasi dari
       IPoolManager.sol), jadi harga deposit diambil dari event Swap terdekat -
       aproksimasi, bukan eksak. Ditandai jelas di output.
       Fee belum diklaim dihitung dari feeGrowthInside via StateView.
"""

import time
from decimal import Decimal

from web3 import Web3

import config
import uniswap_math as um
import pnl as pnl_mod
from abis import (
    ERC20_ABI, V3_FACTORY_ABI, V3_POOL_ABI, V3_NFPM_ABI,
    V4_POSITION_MANAGER_ABI, V4_STATE_VIEW_ABI,
)

NATIVE = "0x0000000000000000000000000000000000000000"
_token_cache = {}


def get_web3() -> Web3:
    return Web3(Web3.HTTPProvider(config.RPC_URL, request_kwargs={"timeout": 25}))


def token_info(w3, address):
    a = address.lower()
    if a == NATIVE:
        return "ETH", 18
    if a in _token_cache:
        return _token_cache[a]
    c = w3.eth.contract(address=Web3.to_checksum_address(a), abi=ERC20_ABI)
    try:
        sym = c.functions.symbol().call()
    except Exception:
        sym = a[:8]
    try:
        dec = c.functions.decimals().call()
    except Exception:
        dec = 18
    _token_cache[a] = (sym, dec)
    return sym, dec


def _orient(token0, token1):
    """Tentukan mana leg USDG. Return (usdg_is_token0, ok)."""
    t0, t1 = token0.lower(), token1.lower()
    u = config.USDG_ADDRESS.lower()
    if t0 == u:
        return True, True
    if t1 == u:
        return False, True
    return False, False


def _finish(pos, w3):
    """Hitung turunan bersama untuk V3 & V4: harga USD, komposisi, PnL."""
    usdg_is_t0, ok = _orient(pos["token0"], pos["token1"])
    pos["usdg_is_token0"] = usdg_is_t0
    pos["usd_ok"] = ok

    raw_now = um.tick_to_price(pos["current_tick"], pos["decimals0"], pos["decimals1"])
    pos["raw_price"] = raw_now

    if not ok:
        # Pair bukan vs USDG - jangan mengarang nilai USD.
        pos.update(value_usd=None, pct_meme=None, pct_usdg=None, pnl_usd=None,
                   pnl_pct=None, invested_usd=None, basis_quality="n/a",
                   uncollected_usd=None, collected_usd=None,
                   meme_symbol=pos["symbol0"], price_usd=None,
                   price_range_usd=(None, None))
        return pos

    price_usd = pnl_mod.usd_price_from_raw(raw_now, usdg_is_t0)
    pos["price_usd"] = price_usd
    pos["meme_symbol"] = pos["symbol1"] if usdg_is_t0 else pos["symbol0"]

    # Range ditampilkan sebagai USD per 1 meme token, apa pun urutan token-nya.
    lo_raw = um.tick_to_price(pos["tick_lower"], pos["decimals0"], pos["decimals1"])
    hi_raw = um.tick_to_price(pos["tick_upper"], pos["decimals0"], pos["decimals1"])
    if usdg_is_t0:
        lo_usd = pnl_mod.usd_price_from_raw(hi_raw, True)
        hi_usd = pnl_mod.usd_price_from_raw(lo_raw, True)
    else:
        lo_usd, hi_usd = lo_raw, hi_raw
    pos["price_range_usd"] = (lo_usd, hi_usd)

    pct_meme, pct_usdg, v_meme, v_usdg = pnl_mod.composition(
        pos["amount0"], pos["amount1"], usdg_is_t0, price_usd)
    pos.update(pct_meme=pct_meme, pct_usdg=pct_usdg,
               value_meme_usd=v_meme, value_usdg_usd=v_usdg,
               value_usd=v_meme + v_usdg)

    if usdg_is_t0:
        unc = pos["uncollected0"] + pos["uncollected1"] * price_usd
        col = (pos["collected0"] or 0) + (pos["collected1"] or 0) * price_usd
    else:
        unc = pos["uncollected0"] * price_usd + pos["uncollected1"]
        col = (pos["collected0"] or 0) * price_usd + (pos["collected1"] or 0)
    pos["uncollected_usd"] = unc
    pos["collected_usd"] = col if pos.get("collected0") is not None else None

    cb = pnl_mod.build_cost_basis(pos.get("deposits", []), pos["decimals0"],
                                  pos["decimals1"], usdg_is_t0)
    pos["invested_usd"] = cb["invested_usd"]
    pos["basis_quality"] = cb["quality"]
    pos["deposited0"] = cb["deposited0"]
    pos["deposited1"] = cb["deposited1"]

    p, ppct = pnl_mod.compute_pnl(pos["value_usd"], unc, col or Decimal(0), cb["invested_usd"])
    pos["pnl_usd"] = p
    pos["pnl_pct"] = ppct
    return pos


# =====================================================================
# Uniswap V3
# =====================================================================

def read_v3(w3, owner):
    owner_cs = Web3.to_checksum_address(owner)
    nfpm = w3.eth.contract(address=Web3.to_checksum_address(config.V3_NFPM), abi=V3_NFPM_ABI)
    factory = w3.eth.contract(address=Web3.to_checksum_address(config.V3_FACTORY), abi=V3_FACTORY_ABI)

    out = []
    for i in range(nfpm.functions.balanceOf(owner_cs).call()):
        tid = nfpm.functions.tokenOfOwnerByIndex(owner_cs, i).call()
        (_, _, t0, t1, fee, tl, tu, liq, _, _, owed0, owed1) = nfpm.functions.positions(tid).call()
        if liq == 0 and owed0 == 0 and owed1 == 0:
            continue

        pool_addr = factory.functions.getPool(t0, t1, fee).call()
        pool = w3.eth.contract(address=Web3.to_checksum_address(pool_addr), abi=V3_POOL_ABI)
        slot0 = pool.functions.slot0().call()
        sqrt_x96, cur_tick = slot0[0], slot0[1]

        sym0, dec0 = token_info(w3, t0)
        sym1, dec1 = token_info(w3, t1)
        a0, a1 = um.amounts_for_liquidity(sqrt_x96, tl, tu, liq)

        try:
            unc0, unc1 = nfpm.functions.collect(
                (tid, owner_cs, config.MAX_UINT128, config.MAX_UINT128)
            ).call({"from": owner_cs})
        except Exception:
            unc0, unc1 = owed0, owed1

        col0, col1 = _v3_collected(nfpm, tid)
        deposits = _v3_deposits(nfpm, tid, tl, tu, dec0, dec1, t0, t1)

        out.append(_finish({
            "protocol": "uniswap_v3", "token_id": tid,
            "key": f"v3:{tid}",
            "pair": f"{sym0}/{sym1}",
            "token0": t0, "token1": t1, "symbol0": sym0, "symbol1": sym1,
            "decimals0": dec0, "decimals1": dec1,
            "tick_lower": tl, "tick_upper": tu, "current_tick": cur_tick,
            "in_range": tl <= cur_tick < tu,
            "liquidity": liq,
            "amount0": Decimal(a0) / 10**dec0, "amount1": Decimal(a1) / 10**dec1,
            "uncollected0": Decimal(unc0) / 10**dec0, "uncollected1": Decimal(unc1) / 10**dec1,
            "collected0": Decimal(col0) / 10**dec0, "collected1": Decimal(col1) / 10**dec1,
            "deposits": deposits,
        }, w3))
    return out


def _v3_collected(nfpm, tid):
    logs = nfpm.events.Collect().get_logs(from_block=0, to_block="latest",
                                          argument_filters={"tokenId": tid})
    return (sum(l["args"]["amount0"] for l in logs),
            sum(l["args"]["amount1"] for l in logs))


def _v3_deposits(nfpm, tid, tick_lower, tick_upper, dec0, dec1, token0, token1):
    """
    Ambil tiap event IncreaseLiquidity dan balikkan harga masuknya secara
    aljabar. Tiap deposit dihargai pada harganya sendiri - itulah cost basis
    yang benar kalau kamu menambah likuiditas beberapa kali.
    """
    usdg_is_t0, ok = _orient(token0, token1)
    logs = nfpm.events.IncreaseLiquidity().get_logs(from_block=0, to_block="latest",
                                                    argument_filters={"tokenId": tid})
    deposits = []
    for l in logs:
        liq = l["args"]["liquidity"]
        a0 = l["args"]["amount0"]
        a1 = l["args"]["amount1"]
        sqrt_p, method = pnl_mod.sqrt_price_at_deposit_from_amounts(liq, tick_lower, tick_upper, a0, a1)
        entry_usd = None
        if sqrt_p is not None and ok:
            raw = pnl_mod.sqrt_ratio_to_price(sqrt_p, dec0, dec1)
            entry_usd = pnl_mod.usd_price_from_raw(raw, usdg_is_t0)
        deposits.append({"amount0": a0, "amount1": a1,
                         "entry_price_usd": entry_usd, "basis_method": method,
                         "block": l["blockNumber"]})
    return deposits


# =====================================================================
# Uniswap V4
# =====================================================================

def sync_v4_index(w3, state, budget_seconds=None):
    """
    Scan event Transfer PositionManager V4 secara bertahap; simpan progres di
    state supaya run berikutnya melanjutkan, bukan mengulang. Berhenti kalau
    sudah menyusul 'latest' atau kehabisan jatah waktu.
    """
    budget = budget_seconds if budget_seconds is not None else config.MAX_SCAN_SECONDS
    pm = w3.eth.contract(address=Web3.to_checksum_address(config.V4_POSITION_MANAGER),
                         abi=V4_POSITION_MANAGER_ABI)
    idx = state.setdefault("v4_index", {"tokens": {}, "last_block": config.V4_INDEX_START_BLOCK})
    latest = w3.eth.block_number
    cursor = int(idx.get("last_block", 0))
    t0 = time.monotonic()

    while cursor < latest and (time.monotonic() - t0) < budget:
        to_b = min(cursor + config.LOG_SCAN_CHUNK, latest)
        for log in pm.events.Transfer().get_logs(from_block=cursor + 1, to_block=to_b):
            tid = str(log["args"]["tokenId"])
            prev = idx["tokens"].get(tid)
            if prev is None or log["blockNumber"] >= prev.get("block", 0):
                idx["tokens"][tid] = {"to": log["args"]["to"].lower(),
                                      "block": log["blockNumber"]}
        idx["last_block"] = to_b
        cursor = to_b

    return {"synced": cursor >= latest, "at_block": cursor, "latest": latest}


def read_v4(w3, owner, state, fee_reconstructor=None):
    pm = w3.eth.contract(address=Web3.to_checksum_address(config.V4_POSITION_MANAGER),
                         abi=V4_POSITION_MANAGER_ABI)
    sv = w3.eth.contract(address=Web3.to_checksum_address(config.V4_STATE_VIEW),
                         abi=V4_STATE_VIEW_ABI)

    idx = state.get("v4_index", {"tokens": {}})
    candidates = [int(t) for t, v in idx.get("tokens", {}).items()
                  if v.get("to") == owner.lower()]

    out = []
    for tid in candidates:
        try:
            if pm.functions.ownerOf(tid).call().lower() != owner.lower():
                continue
        except Exception:
            continue  # sudah di-burn

        pool_key, info_packed = pm.functions.getPoolAndPositionInfo(tid).call()
        c0, c1, fee, spacing, hooks = pool_key
        tl, tu, _ = um.decode_position_info_v4(info_packed)
        liq = pm.functions.getPositionLiquidity(tid).call()
        if liq == 0:
            continue

        pool_id = um.compute_pool_id_v4(c0, c1, fee, spacing, hooks)
        sqrt_x96, cur_tick, _, _ = sv.functions.getSlot0(pool_id).call()

        salt = tid.to_bytes(32, "big")
        pm_liq, g0_last, g1_last = sv.functions.getPositionInfo(
            pool_id, Web3.to_checksum_address(config.V4_POSITION_MANAGER), tl, tu, salt).call()
        g0_now, g1_now = sv.functions.getFeeGrowthInside(pool_id, tl, tu).call()
        unc0 = um.uncollected_fee_from_growth(pm_liq, g0_now, g0_last)
        unc1 = um.uncollected_fee_from_growth(pm_liq, g1_now, g1_last)

        sym0, dec0 = token_info(w3, c0)
        sym1, dec1 = token_info(w3, c1)
        a0, a1 = um.amounts_for_liquidity(sqrt_x96, tl, tu, liq)

        deposits, collected = [], (None, None)
        if fee_reconstructor is not None:
            deposits, collected = fee_reconstructor(
                w3, pool_id, tid, tl, tu, c0, c1, dec0, dec1)

        out.append(_finish({
            "protocol": "uniswap_v4", "token_id": tid,
            "key": f"v4:{tid}",
            "pair": f"{sym0}/{sym1}",
            "token0": c0, "token1": c1, "symbol0": sym0, "symbol1": sym1,
            "decimals0": dec0, "decimals1": dec1,
            "tick_lower": tl, "tick_upper": tu, "current_tick": cur_tick,
            "in_range": tl <= cur_tick < tu,
            "liquidity": liq,
            "amount0": Decimal(a0) / 10**dec0, "amount1": Decimal(a1) / 10**dec1,
            "uncollected0": Decimal(unc0) / 10**dec0, "uncollected1": Decimal(unc1) / 10**dec1,
            "collected0": (Decimal(collected[0]) / 10**dec0) if collected[0] is not None else None,
            "collected1": (Decimal(collected[1]) / 10**dec1) if collected[1] is not None else None,
            "deposits": deposits,
        }, w3))
    return out
