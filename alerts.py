"""
Mesin alert komposisi: kirim peringatan saat posisi mulai condong berat ke meme
token (harga jatuh mendekati batas bawah range).

KENAPA ADA HISTERESIS
---------------------
Kalau alert dikirim setiap kali persentase di atas ambang, satu posisi yang
mengambang di sekitar 75% akan mengirim notifikasi tiap run cron - puluhan pesan
per jam, dan kamu akan mematikan notifikasinya. Itu kegagalan sistem alert yang
paling umum.

Jadi: alert dikirim sekali saat MELEWATI ambang naik, lalu tidak dikirim lagi
sampai persentasenya turun di bawah ambang reset (default 5 poin di bawah).
Naik ke tingkat yang lebih parah (75% -> 90%) tetap memicu alert baru, karena
itu informasi baru yang layak mengganggu kamu.

Level:
  normal   -> di bawah semua ambang
  warning  -> melewati warn_pct (default 75%)
  critical -> melewati crit_pct (default 90%)
  oor      -> posisi keluar range sepenuhnya (100% satu sisi, fee berhenti jalan)
"""

LEVEL_ORDER = {"normal": 0, "warning": 1, "critical": 2, "oor": 3}


def classify(pct_meme: float, in_range: bool, warn_pct: float, crit_pct: float) -> str:
    if not in_range:
        return "oor"
    if pct_meme >= crit_pct:
        return "critical"
    if pct_meme >= warn_pct:
        return "warning"
    return "normal"


def should_alert(prev_level: str, new_level: str, pct_meme: float, prev_pct: float,
                 warn_pct: float, reset_margin: float) -> bool:
    """
    True kalau perubahan ini layak mengganggu user.

    Aturan:
    - Naik tingkat (normal->warning, warning->critical, apa pun ->oor): alert.
    - Tingkat sama: TIDAK alert (inilah histeresisnya).
    - Turun tingkat: tidak alert, TAPI hanya dianggap benar-benar turun kalau
      sudah lewat margin reset - supaya osilasi tipis di sekitar ambang tidak
      bolak-balik mereset dan memicu alert lagi.
    """
    prev_rank = LEVEL_ORDER.get(prev_level, 0)
    new_rank = LEVEL_ORDER.get(new_level, 0)
    if new_rank > prev_rank:
        return True
    return False


def resolve_level(prev_level: str, candidate_level: str, pct_meme: float,
                  warn_pct: float, reset_margin: float) -> str:
    """
    Tentukan level yang DISIMPAN ke state. Penurunan level hanya diakui kalau
    persentasenya sudah turun cukup jauh (di bawah warn_pct - reset_margin),
    supaya posisi yang bergetar di 74.9%/75.1% tidak dianggap reset terus.
    """
    prev_rank = LEVEL_ORDER.get(prev_level, 0)
    new_rank = LEVEL_ORDER.get(candidate_level, 0)
    if new_rank >= prev_rank:
        return candidate_level
    # kandidat lebih rendah - hanya terima kalau benar-benar sudah di bawah reset
    if candidate_level == "normal" and pct_meme > (warn_pct - reset_margin):
        return prev_level   # tahan level lama, belum benar-benar pulih
    return candidate_level


def evaluate_positions(positions, state, warn_pct=75.0, crit_pct=90.0, reset_margin=5.0):
    """
    Bandingkan kondisi posisi sekarang dengan level tersimpan, hasilkan daftar
    alert yang perlu dikirim, dan perbarui state di tempat.

    'positions' butuh field: key, pair, pct_meme, in_range, protocol, token_id.
    Return list dict alert.
    """
    alerts_out = []
    stored = state.setdefault("alerts", {})
    seen = set()

    for p in positions:
        key = p["key"]
        seen.add(key)
        pct = float(p["pct_meme"]) if p["pct_meme"] is not None else 0.0
        prev = stored.get(key, {"level": "normal", "last_pct": 0.0})
        prev_level = prev.get("level", "normal")

        candidate = classify(pct, p["in_range"], warn_pct, crit_pct)
        if should_alert(prev_level, candidate, pct, prev.get("last_pct", 0.0),
                        warn_pct, reset_margin):
            alerts_out.append({
                "key": key, "pair": p["pair"], "protocol": p["protocol"],
                "token_id": p["token_id"], "level": candidate,
                "pct_meme": pct, "in_range": p["in_range"],
                "from_level": prev_level,
            })
        stored[key] = {
            "level": resolve_level(prev_level, candidate, pct, warn_pct, reset_margin),
            "last_pct": pct,
        }

    # bersihkan posisi yang sudah tidak ada (ditutup) supaya state tidak membengkak
    for gone in [k for k in stored if k not in seen]:
        del stored[gone]

    return alerts_out
