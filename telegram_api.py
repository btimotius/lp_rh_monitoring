"""
Klien Telegram Bot API minimal lewat HTTP biasa (requests).

Sengaja TIDAK memakai python-telegram-bot: library itu dirancang untuk proses
yang hidup terus (event loop, scheduler, retry queue). Di GitHub Actions,
prosesnya hidup beberapa puluh detik lalu mati - jadi yang dibutuhkan hanya
tiga panggilan HTTP: ambil pesan tertunda, balas, tandai sudah dibaca.
Lebih sedikit dependensi, lebih sedikit yang bisa rusak.
"""

import requests

API = "https://api.telegram.org/bot{token}/{method}"
TELEGRAM_MAX_CHARS = 4096


def _call(token, method, **params):
    r = requests.post(API.format(token=token, method=method), json=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error pada {method}: {data}")
    return data["result"]


def get_updates(token, offset=0, limit=50):
    """
    Ambil pesan yang menumpuk sejak run terakhir.

    timeout=0 (short polling): kita TIDAK menunggu pesan baru, hanya mengambil
    yang sudah ada lalu keluar - inilah yang membuat pola ini cocok untuk cron.
    offset = update_id terakhir + 1; Telegram akan menghapus yang lebih lama,
    jadi tidak ada pesan yang terproses dua kali.
    """
    return _call(token, "getUpdates", offset=offset, limit=limit, timeout=0)


def send_message(token, chat_id, text, monospace=True):
    """
    Kirim pesan; otomatis dipotong kalau melebihi batas 4096 karakter Telegram
    (laporan dengan belasan posisi gampang melewatinya).
    """
    chunks = _split(text, TELEGRAM_MAX_CHARS - 20 if monospace else TELEGRAM_MAX_CHARS)
    results = []
    for c in chunks:
        body = f"```\n{c}\n```" if monospace else c
        results.append(_call(token, "sendMessage", chat_id=chat_id, text=body,
                             parse_mode="Markdown", disable_web_page_preview=True))
    return results


def _split(text, limit):
    if len(text) <= limit:
        return [text]
    chunks, cur = [], []
    size = 0
    for line in text.split("\n"):
        # baris tunggal yang kepanjangan dipotong paksa, bukan dibuang
        while len(line) > limit:
            chunks.append(line[:limit])
            line = line[limit:]
        if size + len(line) + 1 > limit:
            chunks.append("\n".join(cur))
            cur, size = [], 0
        cur.append(line)
        size += len(line) + 1
    if cur:
        chunks.append("\n".join(cur))
    return [c for c in chunks if c.strip()]
