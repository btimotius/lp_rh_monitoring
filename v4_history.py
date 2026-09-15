"""
Rekonstruksi riwayat posisi Uniswap V4 dari log chain: deposit (untuk cost
basis) dan fee yang sudah diklaim.

KAMU MEMILIH OPSI INI DENGAN SADAR - INI CATATAN JUJURNYA
---------------------------------------------------------
V4 tidak menyediakan event yang rapi seperti V3. Tidak ada `Collect(tokenId,
amount0, amount1)`. Yang ada hanya:

    ModifyLiquidity(PoolId indexed id, address indexed sender, int24 tickLower,
                    int24 tickUpper, int256 liquidityDelta, bytes32 salt)

- `salt` = bytes32(tokenId) (diverifikasi dari source v4-periphery), tapi TIDAK
  indexed, jadi kita harus menarik semua ModifyLiquidity untuk pool itu lalu
  menyaring sendiri. Untuk pool ramai, ini banyak log.
- Event itu TIDAK memuat jumlah token sama sekali. Jumlah fee yang benar-benar
  keluar hanya terlihat sebagai ERC20 Transfer dari PoolManager di transaksi
  yang sama.

Jadi metodenya: temukan transaksi yang menyentuh posisi ini, lalu baca Transfer
ERC20 dari PoolManager di transaksi tersebut, lalu klasifikasikan:

  liquidityDelta == 0  -> murni klaim fee. Seluruh transfer keluar = fee.
  liquidityDelta  > 0  -> tambah likuiditas. Transfer MASUK = modal disetor.
  liquidityDelta  < 0  -> tarik likuiditas. Transfer keluar = pokok + fee, jadi
                          pokoknya dihitung dulu dari |liquidityDelta| pada
                          harga saat itu, sisanya dianggap fee.

EMPAT SUMBER GALAT YANG HARUS KAMU TAHU (tidak bisa saya hilangkan dari sini):

  1. Batch. V4 dirancang untuk mengeksekusi banyak aksi dalam satu transaksi
     (flash accounting). Kalau satu transaksi menyentuh DUA posisi sekaligus -
     misalnya bot rebalance kamu menutup posisi lama dan membuka yang baru -
     transfer ERC20-nya sudah dinetokan jadi satu, dan tidak ada cara
     membelahnya kembali dari log. Kasus ini DITANDAI, bukan ditebak.
  2. Penerima perantara. Kalau fee dikirim ke router/kontrak lain dulu sebelum
     sampai ke wallet kamu, pencocokan penerima meleset.
  3. Harga saat penarikan diambil dari event Swap terdekat, jadi pemisahan
     pokok vs fee pada penarikan sebagian bersifat aproksimasi.
  4. Native ETH tidak muncul sebagai Transfer ERC20. Untuk pair vs USDG ini
     tidak berlaku, tapi kalau kamu punya pair vs ETH asli, angkanya tidak
     lengkap.

Semua hasil dari modul ini membawa field `confidence`. Tampilkan apa adanya -
jangan pernah menyajikan angka 'low' seolah-olah pasti.
"""

import time
from decimal import Decimal

from web3 import Web3

import config
import uniswap_math as um
import pnl as pnl_mod
from abis import V4_POOL_MANAGER_ABI, ERC20_TRANSFER_ABI

TRANSFER_TOPIC = Web3.keccak(text="Transfer(address,address,uint256)").hex()


def _topic_addr(addr: str) -> str:
    return "0x" + "0" * 24 + addr.lower().replace("0x", "")


def _norm_topic(t) -> str:
    return (t.hex() if hasattr(t, "hex") else str(t)).lower().replace("0x", "")


class V4HistoryReader:
    def __init__(self, w3, budget_seconds=None):
        self.w3 = w3
        self.pm = w3.eth.contract(
            address=Web3.to_checksum_address(config.V4_POOL_MANAGER),
            abi=V4_POOL_MANAGER_ABI)
        self.budget = budget_seconds if budget_seconds is not None else config.MAX_SCAN_SECONDS
        self._swap_cache = {}
        self._started = time.monotonic()

    def _out_of_time(self):
        return (time.monotonic() - self._started) > self.budget

    # ---------- harga historis dari event Swap ----------
    def sqrt_price_at_block(self, pool_id, block_number, lookback=200_000):
        """
        sqrtPriceX96 pool pada/sebelum block tertentu, diambil dari event Swap
        terakhir. Return None kalau tidak ada swap dalam jendela lookback -
        dalam kasus itu cost basis-nya ditandai unknown, tidak ditebak.
        """
        ck = (pool_id, block_number // 1000)
        if ck in self._swap_cache:
            return self._swap_cache[ck]
        from_b = max(0, block_number - lookback)
        try:
            logs = self.pm.events.Swap().get_logs(
                from_block=from_b, to_block=block_number,
                argument_filters={"id": pool_id})
        except Exception:
            return None
        val = logs[-1]["args"]["sqrtPriceX96"] if logs else None
        self._swap_cache[ck] = val
        return val

    # ---------- event posisi ----------
    def position_events(self, pool_id, token_id, from_block=0):
        """ModifyLiquidity untuk pool ini yang salt-nya == bytes32(tokenId)."""
        salt_hex = _norm_topic(token_id.to_bytes(32, "big").hex())
        try:
            logs = self.pm.events.ModifyLiquidity().get_logs(
                from_block=from_block, to_block="latest",
                argument_filters={"id": pool_id})
        except Exception:
            return []
        return [l for l in logs if _norm_topic(l["args"]["salt"]) == salt_hex]

    # ---------- transfer ERC20 di dalam satu transaksi ----------
    def transfers_in_tx(self, tx_hash, currency0, currency1):
        """
        Return (into_pm, out_of_pm) sebagai dict {currency_lower: total_amount}.
        Hanya Transfer yang melibatkan PoolManager yang dihitung.
        """
        receipt = self.w3.eth.get_transaction_receipt(tx_hash)
        pm_addr = config.V4_POOL_MANAGER.lower()
        wanted = {currency0.lower(), currency1.lower()}
        into, out = {}, {}

        for log in receipt["logs"]:
            addr = log["address"].lower()
            if addr not in wanted or not log["topics"]:
                continue
            if _norm_topic(log["topics"][0]) != _norm_topic(TRANSFER_TOPIC):
                continue
            if len(log["topics"]) < 3:
                continue
            frm = "0x" + _norm_topic(log["topics"][1])[-40:]
            to = "0x" + _norm_topic(log["topics"][2])[-40:]
            data = log["data"]
            raw = data if isinstance(data, (bytes, bytearray)) else bytes.fromhex(
                str(data).replace("0x", ""))
            value = int.from_bytes(raw[-32:], "big") if raw else 0
            if to == pm_addr:
                into[addr] = into.get(addr, 0) + value
            elif frm == pm_addr:
                out[addr] = out.get(addr, 0) + value
        return into, out

    # ---------- API utama ----------
    def reconstruct(self, w3, pool_id, token_id, tick_lower, tick_upper,
                    currency0, currency1, dec0, dec1):
        """
        Return (deposits, (collected0, collected1)).
        'deposits' berformat sama dengan yang dipakai pnl.build_cost_basis.
        collected bisa (None, None) kalau tidak bisa direkonstruksi sama sekali.
        """
        usdg_is_t0 = currency0.lower() == config.USDG_ADDRESS.lower()
        is_usdg_pair = usdg_is_t0 or currency1.lower() == config.USDG_ADDRESS.lower()

        events = self.position_events(pool_id, token_id)
        if not events:
            return [], (None, None)

        deposits = []
        collected0 = collected1 = 0
        any_low_confidence = False

        for ev in events:
            if self._out_of_time():
                any_low_confidence = True
                break

            delta = ev["args"]["liquidityDelta"]
            block = ev["blockNumber"]
            tx_hash = ev["transactionHash"]

            # Deteksi batch: lebih dari satu ModifyLiquidity di transaksi yang
            # sama berarti transfer ERC20-nya bercampur antar posisi.
            same_tx = [e for e in events if e["transactionHash"] == tx_hash]
            batched = len(same_tx) > 1

            try:
                into, out = self.transfers_in_tx(tx_hash, currency0, currency1)
            except Exception:
                any_low_confidence = True
                continue

            c0k, c1k = currency0.lower(), currency1.lower()

            if delta > 0:
                a0, a1 = into.get(c0k, 0), into.get(c1k, 0)
                entry_usd, method = None, pnl_mod.BASIS_UNKNOWN
                sqrt_x96 = self.sqrt_price_at_block(pool_id, block)
                if sqrt_x96 and is_usdg_pair:
                    raw = um.sqrt_price_x96_to_price(sqrt_x96, dec0, dec1)
                    entry_usd = pnl_mod.usd_price_from_raw(raw, usdg_is_t0)
                    method = pnl_mod.BASIS_SWAP_APPROX
                if batched:
                    # jumlah tokennya tidak bisa dipercaya -> jangan pakai untuk basis
                    entry_usd, method = None, pnl_mod.BASIS_UNKNOWN
                    any_low_confidence = True
                deposits.append({"amount0": a0, "amount1": a1,
                                 "entry_price_usd": entry_usd,
                                 "basis_method": method, "block": block})

            elif delta == 0:
                # klaim fee murni - seluruh keluaran adalah fee
                if batched:
                    any_low_confidence = True
                    continue
                collected0 += out.get(c0k, 0)
                collected1 += out.get(c1k, 0)

            else:
                # penarikan sebagian/penuh: keluaran = pokok + fee
                if batched:
                    any_low_confidence = True
                    continue
                sqrt_x96 = self.sqrt_price_at_block(pool_id, block)
                if not sqrt_x96:
                    any_low_confidence = True
                    continue
                p0, p1 = um.amounts_for_liquidity(sqrt_x96, tick_lower, tick_upper, -delta)
                collected0 += max(out.get(c0k, 0) - p0, 0)
                collected1 += max(out.get(c1k, 0) - p1, 0)

        confidence = "low" if any_low_confidence else "medium"
        for d in deposits:
            d["confidence"] = confidence
        return deposits, (collected0, collected1)


def make_reconstructor(w3, budget_seconds=None):
    """Adapter agar cocok dengan parameter fee_reconstructor di onchain.read_v4."""
    reader = V4HistoryReader(w3, budget_seconds)

    def _fn(w3_, pool_id, token_id, tl, tu, c0, c1, dec0, dec1):
        try:
            return reader.reconstruct(w3_, pool_id, token_id, tl, tu, c0, c1, dec0, dec1)
        except Exception:
            return [], (None, None)

    return _fn
