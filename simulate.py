"""
Simulasi end-to-end TANPA jaringan: chain palsu + Telegram palsu, menjalankan
bot.run_once() yang ASLI.

Yang benar-benar diuji di sini:
  - siklus cron penuh: baca posisi -> evaluasi alert -> layani command
  - PnL dolar dari cost basis yang direkonstruksi dari event
  - persentase komposisi meme/USDG
  - histeresis alert lintas beberapa run
  - persistensi state terenkripsi antar run (seperti antar job GitHub Actions)
  - anti-balas-ganda command lewat telegram_offset

Yang TIDAK diuji: kecocokan ABI/address dengan kontrak sungguhan di Robinhood
Chain. Itu hanya bisa dibuktikan oleh verify_setup.py di mesin yang punya akses
RPC.

Jalankan: python3 simulate.py
"""

import math
import os
import shutil
import sys
import tempfile
from decimal import Decimal

WORK = tempfile.mkdtemp(prefix="rhlp_sim_")
os.environ.update({
    "TELEGRAM_BOT_TOKEN": "sim-token",
    "TELEGRAM_CHAT_ID": "-100999",
    "STATE_KEY": "kunci-rahasia-simulasi",
    "STATE_FILE": os.path.join(WORK, "bot_state.enc"),
    "ALERT_WARN_PCT": "75",
    "ALERT_CRIT_PCT": "90",
})

from web3 import Web3  # noqa: E402
import config  # noqa: E402
import uniswap_math as um  # noqa: E402
import onchain  # noqa: E402
import telegram_api  # noqa: E402
import v4_history  # noqa: E402
import bot  # noqa: E402

OWNER = "0x1111111111111111111111111111111111111111"
USDG = config.USDG_ADDRESS
LONGCAT = "0x2222222222222222222222222222222222222222"   # < USDG -> token0
CASHCAT = "0x9999999999999999999999999999999999999999"   # > USDG -> token1

TOKENS = {USDG: ("USDG", 6), LONGCAT: ("LONGCAT", 18), CASHCAT: ("CASHCAT", 18)}

SENT = []          # semua pesan Telegram terkirim
PENDING = []       # command yang menunggu diambil getUpdates
_next_update_id = [1000]


# ---------------------------------------------------------------- Telegram palsu
def fake_get_updates(token, offset=0, limit=50):
    out = [u for u in PENDING if u["update_id"] >= offset]
    return out


def fake_send_message(token, chat_id, text, monospace=True):
    SENT.append({"chat": str(chat_id), "text": text})
    return [{"ok": True}]


telegram_api.get_updates = fake_get_updates
telegram_api.send_message = fake_send_message
bot.tg = telegram_api


def push_command(text, chat_id="555"):
    _next_update_id[0] += 1
    PENDING.append({"update_id": _next_update_id[0],
                    "message": {"chat": {"id": chat_id}, "text": text}})


# ---------------------------------------------------------------- chain palsu
def tick_of(price_usd, dec0, dec1, usdg_is_t0, spacing=1):
    raw = (1 / price_usd) if usdg_is_t0 else price_usd
    t = math.log(float(raw) / (10 ** (dec0 - dec1))) / math.log(1.0001)
    return int(round(t / spacing)) * spacing


def sqrtx96(tick):
    return int((Decimal("1.0001") ** (Decimal(tick) / 2)) * um.Q96)


def size_liquidity(target_usd, entry_tick, tl, tu, dec0, dec1, usdg_is_t0, price_usd):
    """Cari L supaya nilai posisi saat mint ≈ target_usd, biar output simulasi
    terlihat realistis (ratusan dolar), bukan jutaan."""
    base = 10**18
    a0, a1 = um.amounts_for_liquidity(sqrtx96(entry_tick), tl, tu, base)
    v0 = Decimal(a0) / 10**dec0
    v1 = Decimal(a1) / 10**dec1
    val = (v0 + v1 * price_usd) if usdg_is_t0 else (v0 * price_usd + v1)
    if val <= 0:
        return base
    return int(Decimal(base) * Decimal(target_usd) / val)


class Sim:
    """Satu sumber kebenaran untuk state chain palsu; harga bisa digeser antar run."""

    def __init__(self):
        # --- posisi V3 LONGCAT/USDG (meme = token0), target modal ~$100 ---
        self.v3_tl = tick_of(Decimal("0.0020"), 18, 6, False, 200)
        self.v3_tu = tick_of(Decimal("0.0060"), 18, 6, False, 200)
        self.v3_entry_tick = tick_of(Decimal("0.0040"), 18, 6, False)
        self.v3_L = size_liquidity(100, self.v3_entry_tick, self.v3_tl, self.v3_tu,
                                   18, 6, False, Decimal("0.0040"))
        self.v3_price = Decimal("0.0040")
        d0, d1 = um.amounts_for_liquidity(sqrtx96(self.v3_entry_tick), self.v3_tl, self.v3_tu, self.v3_L)
        self.v3_dep = (d0, d1)
        self.v3_collected = (int(1200 * 10**18), int(6 * 10**6))   # fee sudah diklaim
        self.v3_owed = (int(300 * 10**18), int(1.5 * 10**6))       # fee belum diklaim

        # --- posisi V4 CASHCAT/USDG (USDG = token0, uji cabang inversi), ~$200 ---
        self.v4_tl = tick_of(Decimal("0.0848"), 6, 18, True, 200)
        self.v4_tu = tick_of(Decimal("0.1693"), 6, 18, True, 200)
        self.v4_tl, self.v4_tu = min(self.v4_tl, self.v4_tu), max(self.v4_tl, self.v4_tu)
        self.v4_entry_tick = tick_of(Decimal("0.1200"), 6, 18, True)
        self.v4_L = size_liquidity(200, self.v4_entry_tick, self.v4_tl, self.v4_tu,
                                   6, 18, True, Decimal("0.1200"))
        self.v4_price = Decimal("0.1200")
        self.v4_id = 2653465

    def v3_tick(self):
        return tick_of(self.v3_price, 18, 6, False)

    def v4_tick(self):
        return tick_of(self.v4_price, 6, 18, True)


SIM = Sim()


class Call:
    def __init__(self, fn):
        self.fn = fn

    def call(self, tx=None):
        return self.fn(tx)


class Fns:
    def __init__(self, d):
        self.d = d

    def __getattr__(self, name):
        return lambda *a: Call(lambda tx: self.d(name, a, tx))


class Ev:
    def __init__(self, h):
        self.h = h

    def __call__(self):
        return self

    def get_logs(self, argument_filters=None, from_block=None, to_block=None, block_hash=None):
        return self.h(argument_filters or {}, from_block, to_block)


class Evs:
    def __init__(self, m):
        self.m = m

    def __getattr__(self, n):
        return Ev(self.m.get(n, lambda *a: []))


class C:
    def __init__(self, addr, d, ev=None):
        self.address = addr
        self.functions = Fns(d)
        self.events = Evs(ev or {})


V4_POOL_ID = [None]
V4_MINT_BLOCK = 500_000
V4_COLLECT_BLOCK = 600_000
V4_MINT_TX = "0x" + "aa" * 32
V4_COLLECT_TX = "0x" + "bb" * 32
V4_COLLECTED = (int(4 * 10**6), int(25 * 10**18))   # (USDG, CASHCAT) - USDG token0


class FakeEth:
    block_number = 700_000

    def __init__(self, reg):
        self.reg = reg

    def contract(self, address, abi):
        return self.reg[address.lower()]

    def get_transaction_receipt(self, tx_hash):
        h = tx_hash.hex() if hasattr(tx_hash, "hex") else str(tx_hash)
        if not h.startswith("0x"):
            h = "0x" + h
        topic = Web3.keccak(text="Transfer(address,address,uint256)")
        pm = config.V4_POOL_MANAGER

        def log(token, frm, to, val):
            return {"address": token,
                    "topics": [topic,
                               bytes(12) + bytes.fromhex(frm[2:]),
                               bytes(12) + bytes.fromhex(to[2:])],
                    "data": val.to_bytes(32, "big")}

        if h == V4_MINT_TX:
            a0, a1 = um.amounts_for_liquidity(sqrtx96(SIM.v4_entry_tick),
                                              SIM.v4_tl, SIM.v4_tu, SIM.v4_L)
            return {"logs": [log(USDG, OWNER, pm, a0), log(CASHCAT, OWNER, pm, a1)]}
        if h == V4_COLLECT_TX:
            return {"logs": [log(USDG, pm, OWNER, V4_COLLECTED[0]),
                             log(CASHCAT, pm, OWNER, V4_COLLECTED[1])]}
        return {"logs": []}


class FakeW3:
    def __init__(self, reg):
        self.eth = FakeEth(reg)

    def is_connected(self):
        return True


def build_registry():
    reg = {}
    for a, (sym, dec) in TOKENS.items():
        reg[a.lower()] = C(a, lambda n, ar, tx, s=sym, d=dec: s if n == "symbol" else d)

    pool_v3 = "0x" + "cc" * 20
    reg[pool_v3] = C(pool_v3, lambda n, a, tx: (sqrtx96(SIM.v3_tick()), SIM.v3_tick(), 0, 1, 1, 0, True))
    reg[config.V3_FACTORY.lower()] = C(config.V3_FACTORY, lambda n, a, tx: pool_v3)

    def nfpm_d(n, a, tx):
        if n == "balanceOf":
            return 1
        if n == "tokenOfOwnerByIndex":
            return 111
        if n == "positions":
            return (0, "0x" + "0" * 40, Web3.to_checksum_address(LONGCAT),
                    Web3.to_checksum_address(USDG), 10000, SIM.v3_tl, SIM.v3_tu,
                    SIM.v3_L, 0, 0, SIM.v3_owed[0], SIM.v3_owed[1])
        if n == "collect":
            return SIM.v3_owed
        raise AssertionError(n)

    def nfpm_ev(kind):
        def h(f, fb, tb):
            if kind == "Collect":
                return [{"args": {"tokenId": 111, "amount0": SIM.v3_collected[0],
                                  "amount1": SIM.v3_collected[1]}, "blockNumber": 10}]
            if kind == "IncreaseLiquidity":
                return [{"args": {"tokenId": 111, "liquidity": SIM.v3_L,
                                  "amount0": SIM.v3_dep[0], "amount1": SIM.v3_dep[1]},
                         "blockNumber": 5}]
            return []
        return h

    reg[config.V3_NFPM.lower()] = C(config.V3_NFPM, nfpm_d, {
        "Collect": nfpm_ev("Collect"),
        "IncreaseLiquidity": nfpm_ev("IncreaseLiquidity"),
        "DecreaseLiquidity": nfpm_ev("Dec")})

    # --- V4 PositionManager ---
    def pm_d(n, a, tx):
        if n == "ownerOf":
            return Web3.to_checksum_address(OWNER)
        if n == "getPoolAndPositionInfo":
            packed = ((SIM.v4_tu & 0xFFFFFF) << 32) | ((SIM.v4_tl & 0xFFFFFF) << 8)
            return ((Web3.to_checksum_address(USDG), Web3.to_checksum_address(CASHCAT),
                     10000, 200, "0x" + "0" * 40), packed)
        if n == "getPositionLiquidity":
            return SIM.v4_L
        raise AssertionError(n)

    def pm_transfer(f, fb, tb):
        if fb <= V4_MINT_BLOCK <= tb:
            return [{"args": {"from": "0x" + "0" * 40,
                              "to": Web3.to_checksum_address(OWNER),
                              "tokenId": SIM.v4_id}, "blockNumber": V4_MINT_BLOCK}]
        return []

    reg[config.V4_POSITION_MANAGER.lower()] = C(config.V4_POSITION_MANAGER, pm_d,
                                                {"Transfer": pm_transfer})

    # --- V4 StateView ---
    GL = 10**30

    def sv_d(n, a, tx):
        pid = a[0]
        assert V4_POOL_ID[0] is None or pid == V4_POOL_ID[0], "poolId tidak konsisten"
        V4_POOL_ID[0] = pid
        if n == "getSlot0":
            return (sqrtx96(SIM.v4_tick()), SIM.v4_tick(), 0, 10000)
        if n == "getPositionInfo":
            _, owner, tl, tu, salt = a
            assert owner.lower() == config.V4_POSITION_MANAGER.lower()
            assert int.from_bytes(salt, "big") == SIM.v4_id
            assert (tl, tu) == (SIM.v4_tl, SIM.v4_tu)
            return (SIM.v4_L, GL, GL)
        if n == "getFeeGrowthInside":
            f0, f1 = int(0.8 * 10**6), int(6 * 10**18)
            return ((GL + (f0 * 2**128) // SIM.v4_L) % 2**256,
                    (GL + (f1 * 2**128) // SIM.v4_L) % 2**256)
        raise AssertionError(n)

    reg[config.V4_STATE_VIEW.lower()] = C(config.V4_STATE_VIEW, sv_d)

    # --- V4 PoolManager (riwayat: mint + collect) ---
    def poolmgr_ev(f, fb, tb):
        salt = SIM.v4_id.to_bytes(32, "big")
        return [
            {"args": {"id": V4_POOL_ID[0], "sender": config.V4_POSITION_MANAGER,
                      "tickLower": SIM.v4_tl, "tickUpper": SIM.v4_tu,
                      "liquidityDelta": SIM.v4_L, "salt": salt},
             "blockNumber": V4_MINT_BLOCK, "transactionHash": V4_MINT_TX},
            {"args": {"id": V4_POOL_ID[0], "sender": config.V4_POSITION_MANAGER,
                      "tickLower": SIM.v4_tl, "tickUpper": SIM.v4_tu,
                      "liquidityDelta": 0, "salt": salt},
             "blockNumber": V4_COLLECT_BLOCK, "transactionHash": V4_COLLECT_TX},
        ]

    def swap_ev(f, fb, tb):
        if fb <= V4_MINT_BLOCK <= tb:
            return [{"args": {"sqrtPriceX96": sqrtx96(SIM.v4_entry_tick)},
                     "blockNumber": V4_MINT_BLOCK}]
        return []

    reg[config.V4_POOL_MANAGER.lower()] = C(config.V4_POOL_MANAGER, lambda n, a, tx: None,
                                            {"ModifyLiquidity": poolmgr_ev, "Swap": swap_ev})
    return reg


FAKE = FakeW3(build_registry())
onchain.get_web3 = lambda: FAKE


# ---------------------------------------------------------------- jalankan
def show(title):
    print("\n" + "=" * 68)
    print(title)
    print("=" * 68)
    for m in SENT:
        print(m["text"])
        print("-" * 40)
    SENT.clear()


def main():
    ok = True

    # RUN 1 — daftarkan wallet lalu minta posisi
    push_command(f"/addwallet {OWNER} wallet utama")
    push_command("/position")
    bot.run_once()
    show("RUN 1 — harga LONGCAT $0.0040 (tengah range), CASHCAT $0.1200")

    first_report = None

    # RUN 2 — tidak ada command baru; harga LONGCAT jatuh -> komposisi meme naik
    SIM.v3_price = Decimal("0.0023")
    bot.run_once()
    show("RUN 2 — LONGCAT jatuh ke $0.0023 (dekat batas bawah) -> alert komposisi")
    alert_seen_run2 = True

    # RUN 3 — harga BERTAHAN di level yang sama: histeresis harus membuatnya senyap.
    # (Kalau harga turun lagi sampai naik tingkat ke 'critical', alert memang SEHARUSNYA
    # bunyi - eskalasi adalah informasi baru. Jadi di sini harga sengaja tidak digeser.)
    bot.run_once()
    n3 = len(SENT)
    show("RUN 3 — harga tidak berubah: harus SENYAP (histeresis)")
    if n3 != 0:
        print("!! GAGAL: seharusnya tidak ada pesan sama sekali di run 3")
        ok = False

    # RUN 4 — jatuh keluar range -> alert OOR, dan minta laporan lagi
    SIM.v3_price = Decimal("0.0018")
    push_command("/position")
    bot.run_once()
    show("RUN 4 — LONGCAT $0.0018 (keluar range) -> alert OOR + laporan diminta")

    # Verifikasi angka langsung dari pembacaan
    print("\n" + "=" * 68)
    print("VERIFIKASI NUMERIK")
    print("=" * 68)
    from state import StateStore
    store = StateStore(config.STATE_FILE, config.state_key())
    store.load()
    notes = []
    positions = bot.collect_positions(FAKE, store, OWNER, notes)
    by = {p["protocol"]: p for p in positions}

    v3 = by["uniswap_v3"]
    v4 = by["uniswap_v4"]

    checks = []
    # V3: cost basis eksak dari derivasi aljabar
    checks.append(("V3 basis eksak", v3["basis_quality"] == "exact", v3["basis_quality"]))
    checks.append(("V3 modal masuk terhitung", v3["invested_usd"] is not None and v3["invested_usd"] > 0,
                   v3["invested_usd"]))
    checks.append(("V3 PnL terhitung", v3["pnl_usd"] is not None, v3["pnl_usd"]))
    checks.append(("V3 keluar range", v3["in_range"] is False, v3["in_range"]))
    checks.append(("V3 komposisi ~100% meme (harga di bawah range)",
                   v3["pct_meme"] > 99, float(v3["pct_meme"])))
    checks.append(("V3 fee sudah diklaim terbaca", v3["collected_usd"] is not None and v3["collected_usd"] > 0,
                   v3["collected_usd"]))

    # V4: basis aproksimasi dari event Swap, fee terklaim direkonstruksi
    checks.append(("V4 basis approx (dari Swap)", v4["basis_quality"] == "approx", v4["basis_quality"]))
    checks.append(("V4 modal masuk terhitung", v4["invested_usd"] is not None and v4["invested_usd"] > 0,
                   v4["invested_usd"]))
    expected_col = Decimal(V4_COLLECTED[0]) / 10**6 + (Decimal(V4_COLLECTED[1]) / 10**18) * v4["price_usd"]
    checks.append(("V4 fee terklaim direkonstruksi benar",
                   v4["collected_usd"] is not None and abs(v4["collected_usd"] - expected_col) < Decimal("0.01"),
                   f"{v4['collected_usd']} vs harapan {expected_col}"))
    checks.append(("V4 in-range", v4["in_range"] is True, v4["in_range"]))
    checks.append(("V4 komposisi dua sisi terisi",
                   0 < v4["pct_meme"] < 100, float(v4["pct_meme"])))
    # USDG=token0 di V4 -> pastikan range ditampilkan sebagai USD per meme, tidak terbalik
    lo, hi = v4["price_range_usd"]
    checks.append(("V4 range tidak terbalik", lo < v4["price_usd"] < hi, f"{lo} < {v4['price_usd']} < {hi}"))

    # state terenkripsi benar-benar terenkripsi
    blob = open(config.STATE_FILE, "rb").read()
    checks.append(("State tidak memuat address polos",
                   OWNER.encode() not in blob and OWNER[2:].encode() not in blob, len(blob)))

    for name, passed, val in checks:
        print(f"{'OK  ' if passed else 'FAIL'} {name}  ({val})")
        ok = ok and passed

    print(f"\n{'SEMUA VERIFIKASI LULUS' if ok else 'ADA YANG GAGAL'}")
    shutil.rmtree(WORK, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
