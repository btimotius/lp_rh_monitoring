"""Render posisi jadi teks Telegram (monospace, rapi di layar HP)."""

from decimal import Decimal

BASIS_LABEL = {
    "exact": "",                       # tidak perlu catatan - angkanya solid
    "approx": " ~",                    # cost basis dari event Swap (V4)
    "partial": " ⚠sebagian",           # sebagian deposit tak bisa dihargai
    "unknown": " ⚠tanpa basis",
    "n/a": "",
}


def _usd(x, dp=2):
    if x is None:
        return "n/a"
    x = Decimal(x)
    neg = x < 0
    s = f"${abs(x):,.{dp}f}"
    return f"-{s}" if neg else s


def _pct(x, dp=1):
    return "n/a" if x is None else f"{Decimal(x):+.{dp}f}%"


def _price(x):
    if x is None:
        return "n/a"
    x = Decimal(x)
    if x == 0:
        return "0"
    if x < Decimal("0.0001"):
        return f"{x:.10f}".rstrip("0")
    if x < 1:
        return f"{x:.6f}".rstrip("0")
    return f"{x:,.4f}"


def _bar(pct_meme, width=10):
    """Bar komposisi: bagian meme vs USDG, sekali lihat langsung kelihatan."""
    if pct_meme is None:
        return "─" * width
    filled = int(round(float(pct_meme) / 100 * width))
    return "█" * filled + "░" * (width - filled)


def format_position(p) -> str:
    lines = []
    proto = "V3" if p["protocol"] == "uniswap_v3" else "V4"
    status = "🟢 IN" if p["in_range"] else "🔴 OOR"
    meme = p.get("meme_symbol") or p["symbol0"]

    lines.append(f"{meme}/USDG [{proto}] #{p['token_id']}  {status}")

    if not p.get("usd_ok"):
        lines.append(f"  Pair bukan vs USDG - nilai USD tidak dihitung")
        lines.append(f"  Token: {p['amount0']:.4f} {p['symbol0']} + {p['amount1']:.4f} {p['symbol1']}")
        return "\n".join(lines)

    lo, hi = p["price_range_usd"]
    lines.append(f"  Range   {_price(lo)} – {_price(hi)}   now {_price(p['price_usd'])}")

    pm = p.get("pct_meme")
    pu = p.get("pct_usdg")
    if pm is not None:
        lines.append(f"  Komposisi {_bar(pm)} {float(pm):.0f}% {meme} / {float(pu):.0f}% USDG")

    basis_note = BASIS_LABEL.get(p.get("basis_quality", "n/a"), "")
    lines.append(f"  Modal   {_usd(p.get('invested_usd'))}{basis_note}"
                 f"   →   Nilai {_usd(p.get('value_usd'))}")

    unc = p.get("uncollected_usd")
    col = p.get("collected_usd")
    fee_txt = f"  Fee     belum {_usd(unc)}"
    fee_txt += f" | sudah {_usd(col)}" if col is not None else " | sudah n/a"
    lines.append(fee_txt)

    if p.get("pnl_usd") is not None:
        sign = "🟩" if p["pnl_usd"] >= 0 else "🟥"
        lines.append(f"  PnL     {sign} {_usd(p['pnl_usd'])} ({_pct(p['pnl_pct'])})")
    else:
        lines.append("  PnL     n/a (harga masuk tidak bisa ditentukan)")

    return "\n".join(lines)


def format_report(wallet_label, address, positions, notes=None) -> str:
    notes = notes or []
    out = [f"📊 {wallet_label} · {address[:6]}…{address[-4:]}"]

    usd_positions = [p for p in positions if p.get("usd_ok")]
    tot_val = sum((p["value_usd"] for p in usd_positions if p.get("value_usd")), Decimal(0))
    tot_unc = sum((p["uncollected_usd"] for p in usd_positions if p.get("uncollected_usd")), Decimal(0))
    tot_pnl_parts = [p["pnl_usd"] for p in usd_positions if p.get("pnl_usd") is not None]
    tot_inv_parts = [p["invested_usd"] for p in usd_positions if p.get("invested_usd") is not None]

    out.append(f"Posisi {len(positions)} · Nilai {_usd(tot_val)} · Fee belum diklaim {_usd(tot_unc)}")
    if tot_pnl_parts:
        tot_pnl = sum(tot_pnl_parts, Decimal(0))
        tot_inv = sum(tot_inv_parts, Decimal(0))
        pct = (tot_pnl / tot_inv * 100) if tot_inv else None
        covered = f" (dari {len(tot_pnl_parts)}/{len(positions)} posisi)" if len(tot_pnl_parts) != len(positions) else ""
        out.append(f"PnL total {_usd(tot_pnl)} ({_pct(pct)}){covered}")
    out.append("")

    if not positions:
        out.append("(tidak ada posisi terbuka)")

    # OOR dan komposisi meme tertinggi ditaruh di atas - itu yang butuh tindakan
    def urgency(p):
        if not p["in_range"]:
            return (0, 0)
        return (1, -float(p.get("pct_meme") or 0))

    for p in sorted(positions, key=urgency):
        out.append(format_position(p))
        out.append("")

    if notes:
        out.append("⚠️ " + "\n⚠️ ".join(notes))

    return "\n".join(out).rstrip()


def format_alert(alert, position=None) -> str:
    icon = {"warning": "⚠️", "critical": "🚨", "oor": "🔴"}.get(alert["level"], "ℹ️")
    proto = "V3" if alert["protocol"] == "uniswap_v3" else "V4"
    head = f"{icon} {alert['pair']} [{proto}] #{alert['token_id']}"

    if alert["level"] == "oor":
        body = "Posisi KELUAR RANGE - fee berhenti jalan sampai kamu reposisi."
    else:
        body = (f"Komposisi meme token naik ke {alert['pct_meme']:.0f}% "
                f"(dari level '{alert['from_level']}'). Harga bergerak ke batas bawah range.")

    lines = [head, body]
    if position is not None and position.get("usd_ok"):
        lo, hi = position["price_range_usd"]
        lines.append(f"Harga {_price(position['price_usd'])} · range {_price(lo)}–{_price(hi)}")
        lines.append(f"Nilai {_usd(position.get('value_usd'))} · fee belum diklaim {_usd(position.get('uncollected_usd'))}")
    return "\n".join(lines)
