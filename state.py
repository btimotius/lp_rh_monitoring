"""
State terenkripsi untuk dijalankan di repo GitHub PUBLIK.

KENAPA DIENKRIPSI
-----------------
Repo publik dapat menit GitHub Actions tanpa batas (repo privat cuma 2.000
menit/bulan - tidak cukup untuk cron tiap 5 menit). Tapi konsekuensinya isi repo
bisa dibaca siapa saja. State bot ini memuat address wallet, tokenId posisi, dan
riwayat fee - kalau ditulis polos ke repo publik, siapa pun bisa memetakan
seluruh aktivitas LP kamu dan mengaitkannya dengan akun GitHub kamu.

Jadi state disimpan sebagai satu file terenkripsi AES-256-GCM. Kuncinya
diturunkan dari GitHub Secret (STATE_KEY) yang tidak pernah ikut ter-commit.
GCM memberi authentication tag, jadi file yang diutak-atik orang akan DITOLAK,
bukan didekripsi jadi sampah diam-diam.

Yang TETAP terlihat publik: ukuran file dan waktu commit. Itu tidak
mengungkapkan posisi kamu.

CATATAN OPERASIONAL: kalau STATE_KEY hilang/diganti, state lama tidak bisa
dibaca lagi. Bot akan mulai dari state kosong (tidak crash) - artinya riwayat
fee terklaim yang dilacak sejak awal hilang, dan alert akan dikirim ulang sekali
untuk posisi yang sedang melewati ambang. Simpan kuncinya baik-baik.
"""

import base64
import json
import os
from pathlib import Path

from Crypto.Cipher import AES
from Crypto.Protocol.KDF import PBKDF2
from Crypto.Hash import SHA256

MAGIC = b"RHLP1"      # penanda format, biar salah-file ketahuan cepat
SALT_LEN = 16
NONCE_LEN = 12
TAG_LEN = 16
PBKDF2_ROUNDS = 200_000


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    return PBKDF2(passphrase, salt, dkLen=32, count=PBKDF2_ROUNDS, hmac_hash_module=SHA256)


def encrypt_state(data: dict, passphrase: str) -> bytes:
    plaintext = json.dumps(data, separators=(",", ":"), sort_keys=True).encode()
    salt = os.urandom(SALT_LEN)
    nonce = os.urandom(NONCE_LEN)
    cipher = AES.new(_derive_key(passphrase, salt), AES.MODE_GCM, nonce=nonce)
    ct, tag = cipher.encrypt_and_digest(plaintext)
    return base64.b64encode(MAGIC + salt + nonce + tag + ct)


def decrypt_state(blob: bytes, passphrase: str) -> dict:
    raw = base64.b64decode(blob)
    if not raw.startswith(MAGIC):
        raise ValueError("format state tidak dikenali (bukan file state bot ini)")
    body = raw[len(MAGIC):]
    salt = body[:SALT_LEN]
    nonce = body[SALT_LEN:SALT_LEN + NONCE_LEN]
    tag = body[SALT_LEN + NONCE_LEN:SALT_LEN + NONCE_LEN + TAG_LEN]
    ct = body[SALT_LEN + NONCE_LEN + TAG_LEN:]
    cipher = AES.new(_derive_key(passphrase, salt), AES.MODE_GCM, nonce=nonce)
    plaintext = cipher.decrypt_and_verify(ct, tag)  # melempar ValueError kalau tag salah
    return json.loads(plaintext)


EMPTY_STATE = {
    "version": 1,
    "telegram_offset": 0,      # update_id terakhir yang sudah diproses (anti balas ganda)
    "wallets": [],             # [{address, label}]
    "alerts": {},              # {position_key: {"level": "...", "last_pct": float}}
    "fee_snapshots": {},       # {position_key: {"uncollected_usd": float, "collected_tracked_usd": float}}
    "last_run_ts": 0,
}


class StateStore:
    def __init__(self, path: str, passphrase: str):
        self.path = Path(path)
        self.passphrase = passphrase
        self.data = dict(EMPTY_STATE)
        self.load_error = None

    def load(self):
        if not self.path.exists():
            return self.data
        try:
            self.data = decrypt_state(self.path.read_bytes(), self.passphrase)
        except Exception as e:
            # Sengaja TIDAK crash: bot tetap berguna dengan state kosong, dan
            # penyebabnya dilaporkan ke Telegram supaya kamu sadar (paling sering:
            # STATE_KEY berubah).
            self.load_error = str(e)
            self.data = dict(EMPTY_STATE)
        for k, v in EMPTY_STATE.items():
            self.data.setdefault(k, v if not isinstance(v, (dict, list)) else type(v)())
        return self.data

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(encrypt_state(self.data, self.passphrase))

    # --- helper wallet ---
    def add_wallet(self, address: str, label: str = None) -> bool:
        addr = address.lower()
        for w in self.data["wallets"]:
            if w["address"] == addr:
                w["label"] = label or w.get("label")
                return False
        self.data["wallets"].append({"address": addr, "label": label})
        return True

    def remove_wallet(self, address: str) -> bool:
        addr = address.lower()
        before = len(self.data["wallets"])
        self.data["wallets"] = [w for w in self.data["wallets"] if w["address"] != addr]
        return len(self.data["wallets"]) < before

    def wallets(self):
        return list(self.data["wallets"])
