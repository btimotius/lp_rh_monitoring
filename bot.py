"""
Entry point satu-run. Dijalankan oleh GitHub Actions tiap cron, lalu SELESAI -
tidak ada proses yang menetap, tidak butuh VPS/PC menyala.

Satu run melakukan tiga hal berurutan:
  1. Baca semua posisi wallet terdaftar dari chain.
  2. Evaluasi ambang komposisi; kirim alert kalau ada yang baru melewati batas.
  3. Ambil command Telegram yang menumpuk sejak run sebelumnya, balas.

Karena command diambil per run (bukan koneksi menetap), /position dijawab
dengan jeda maksimal satu interval cron. Itu konsekuensi langsung dari memilih
GitHub Actions - platform ini tidak bisa menerima webhook.

Command: /position /addwallet <addr> [label] /wallets /removewallet <addr> /help
"""

import sys
import time
import traceback
from decimal import Decimal

import config
import onchain
import alerts as alert_engine
import report
import telegram_api as tg
import v4_history
from state import StateStore


def _is_addr(s):
    return isinstance(s, str) and s.startswith("0x") and len(s) == 42 and \
        all(c in "0123456789abcdefABCDEF" for c in s[2:])


def collect_positions(w3, store, address, notes):
    """Baca V3 + V4 untuk satu address. Kegagalan satu sisi tidak menjatuhkan sisi lain."""
    positions = []
    try:
        positions += onchain.read_v3(w3, address)
    except Exception as e:
        notes.append(f"Gagal baca posisi V3: {type(e).__name__}: {e}")

    try:
        sync = onchain.sync_v4_index(w3, store.data)
        if not sync["synced"]:
            pct = (sync["at_block"] / sync["latest"] * 100) if sync["latest"] else 0
            notes.append(
                f"Index V4 baru {pct:.1f}% ({sync['at_block']:,}/{sync['latest']:,} block). "
                f"Posisi V4 mungkin belum lengkap; akan menyusul di run berikutnya.")
        recon = v4_history.make_reconstructor(w3)
        positions += onchain.read_v4(w3, address, store.data, fee_reconstructor=recon)
    except Exception as e:
        notes.append(f"Gagal baca posisi V4: {type(e).__name__}: {e}")

    return positions


def handle_commands(token, store, w3, position_cache):
    """Proses command yang menumpuk. Return jumlah command yang dilayani."""
    offset = int(store.data.get("telegram_offset", 0))
    try:
        updates = tg.get_updates(token, offset=offset + 1 if offset else 0)
    except Exception as e:
        print(f"getUpdates gagal: {e}", file=sys.stderr)
        return 0

    served = 0
    for u in updates:
        store.data["telegram_offset"] = max(store.data.get("telegram_offset", 0), u["update_id"])
        msg = u.get("message") or u.get("channel_post")
        if not msg or "text" not in msg:
            continue
        chat_id = msg["chat"]["id"]
        parts = msg["text"].strip().split()
        cmd = parts[0].split("@")[0].lower()
        args = parts[1:]
        served += 1

        try:
            if cmd == "/position":
                _cmd_position(token, chat_id, store, w3, args, position_cache)
            elif cmd == "/addwallet":
                _cmd_addwallet(token, chat_id, store, args)
            elif cmd == "/removewallet":
                _cmd_removewallet(token, chat_id, store, args)
            elif cmd == "/wallets":
                _cmd_wallets(token, chat_id, store)
            elif cmd in ("/help", "/start"):
                tg.send_message(token, chat_id, _help_text(), monospace=False)
            else:
                served -= 1
        except Exception as e:
            traceback.print_exc()
            tg.send_message(token, chat_id, f"Error saat menjalankan {cmd}: {e}", monospace=False)
    return served


def _help_text():
    return (
        "*Monitor LP Robinhood Chain*\n\n"
        "/position — semua posisi + PnL + komposisi\n"
        "/position `<address>` — cek satu address langsung\n"
        "/addwallet `<address>` `[label]` — daftarkan wallet\n"
        "/wallets — daftar wallet terpantau\n"
        "/removewallet `<address>` — hapus wallet\n\n"
        f"Alert otomatis saat komposisi meme token ≥ {config.WARN_PCT:.0f}% "
        f"(kritis ≥ {config.CRIT_PCT:.0f}%) atau posisi keluar range.\n\n"
        "_Bot berjalan terjadwal, jadi balasan bisa tertunda sampai satu siklus cron._"
    )


def _cmd_position(token, chat_id, store, w3, args, cache):
    if args and _is_addr(args[0]):
        targets = [{"address": args[0].lower(), "label": "wallet"}]
    else:
        targets = store.wallets()
    if not targets:
        tg.send_message(token, chat_id,
                        "Belum ada wallet terdaftar. Pakai /addwallet <address>.",
                        monospace=False)
        return
    for wal in targets:
        addr = wal["address"]
        if addr in cache:
            positions, notes = cache[addr]
        else:
            notes = []
            positions = collect_positions(w3, store, addr, notes)
            cache[addr] = (positions, notes)
        tg.send_message(token, chat_id,
                        report.format_report(wal.get("label") or "wallet", addr, positions, notes))


def _cmd_addwallet(token, chat_id, store, args):
    if not args or not _is_addr(args[0]):
        tg.send_message(token, chat_id, "Format: /addwallet <address> [label]", monospace=False)
        return
    label = " ".join(args[1:]) if len(args) > 1 else None
    added = store.add_wallet(args[0], label)
    tg.send_message(token, chat_id,
                    ("✅ Wallet ditambahkan." if added else "✅ Wallet sudah ada, label diperbarui.")
                    + " Data posisi muncul di /position berikutnya.", monospace=False)


def _cmd_removewallet(token, chat_id, store, args):
    if not args:
        tg.send_message(token, chat_id, "Format: /removewallet <address>", monospace=False)
        return
    ok = store.remove_wallet(args[0])
    tg.send_message(token, chat_id, "✅ Dihapus." if ok else "Wallet itu tidak ada di daftar.",
                    monospace=False)


def _cmd_wallets(token, chat_id, store):
    ws = store.wallets()
    if not ws:
        tg.send_message(token, chat_id, "Belum ada wallet terdaftar.", monospace=False)
        return
    lines = ["Wallet terpantau:"] + [
        f" • {w['address']}" + (f" ({w['label']})" if w.get("label") else "") for w in ws]
    tg.send_message(token, chat_id, "\n".join(lines), monospace=False)


def run_once():
    token = config.telegram_token()
    alert_chat = config.telegram_chat_id()
    store = StateStore(config.STATE_FILE, config.state_key())
    store.load()

    if store.load_error:
        tg.send_message(token, alert_chat,
                        f"⚠️ State lama tidak bisa dibaca ({store.load_error}). "
                        f"Bot mulai dari state kosong — riwayat fee terlacak hilang dan "
                        f"alert bisa terkirim ulang sekali. Cek apakah STATE_KEY berubah.",
                        monospace=False)

    w3 = onchain.get_web3()
    if not w3.is_connected():
        tg.send_message(token, alert_chat,
                        f"❌ Tidak bisa konek RPC {config.RPC_URL}", monospace=False)
        store.save()
        return 1

    # --- 1 & 2: baca posisi lalu evaluasi alert ---
    cache = {}
    all_positions = []
    for wal in store.wallets():
        notes = []
        positions = collect_positions(w3, store, wal["address"], notes)
        cache[wal["address"]] = (positions, notes)
        all_positions += positions

    alertable = [p for p in all_positions if p.get("usd_ok")]
    fired = alert_engine.evaluate_positions(
        alertable, store.data,
        warn_pct=config.WARN_PCT, crit_pct=config.CRIT_PCT,
        reset_margin=config.RESET_MARGIN)

    by_key = {p["key"]: p for p in alertable}
    for a in fired:
        tg.send_message(token, alert_chat, report.format_alert(a, by_key.get(a["key"])),
                        monospace=False)

    # --- 3: layani command yang menumpuk ---
    served = handle_commands(token, store, w3, cache)

    store.data["last_run_ts"] = int(time.time())
    store.save()
    print(f"selesai: {len(all_positions)} posisi, {len(fired)} alert, {served} command dilayani")
    return 0


if __name__ == "__main__":
    sys.exit(run_once())
