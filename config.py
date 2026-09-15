"""
Konfigurasi. Nilai sensitif (token bot, kunci state) TIDAK pernah ditulis di
sini - semuanya dari environment variable, yang di GitHub Actions diisi dari
repository secrets.

Address kontrak diambil dari dokumentasi resmi Uniswap Developers dan
docs.robinhood.com. Jalankan verify_setup.py untuk memastikan semuanya benar
adalah contract hidup di chain sebelum dipakai.
"""

import os


def _req(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        raise SystemExit(
            f"Environment variable {name} belum diisi. Di GitHub Actions, "
            f"tambahkan sebagai repository secret (Settings > Secrets and variables > Actions)."
        )
    return v


# --- Rahasia (dari GitHub Secrets) ---
def telegram_token() -> str:
    return _req("TELEGRAM_BOT_TOKEN")


def telegram_chat_id() -> str:
    """Chat tujuan alert. Command /position dibalas ke chat pengirimnya sendiri."""
    return _req("TELEGRAM_CHAT_ID")


def state_key() -> str:
    return _req("STATE_KEY")


# --- Jaringan ---
RPC_URL = os.environ.get("ROBINHOOD_RPC_URL", "https://rpc.mainnet.chain.robinhood.com")
CHAIN_ID = 4663

# --- Uniswap V3 di Robinhood Chain ---
V3_FACTORY = "0x1f7d7550b1b028f7571e69a784071f0205fd2efa"
V3_NFPM = "0x73991a25c818bf1f1128deaab1492d45638de0d3"

# --- Uniswap V4 di Robinhood Chain ---
V4_POOL_MANAGER = "0x8366a39cc670b4001a1121b8f6a443a643e40951"
V4_POSITION_MANAGER = "0x58daec3116aae6d93017baaea7749052e8a04fa7"
V4_STATE_VIEW = "0xf3334192d15450cdd385c8b70e03f9a6bd9e673b"

# --- Token acuan USD ---
USDG_ADDRESS = "0x5fc5360d0400a0fd4f2af552add042d716f1d168"

# --- Ambang alert komposisi (persen NILAI posisi yang berupa meme token) ---
WARN_PCT = float(os.environ.get("ALERT_WARN_PCT", "75"))
CRIT_PCT = float(os.environ.get("ALERT_CRIT_PCT", "90"))
RESET_MARGIN = float(os.environ.get("ALERT_RESET_MARGIN", "5"))

# --- Batas kerja per run (GitHub Actions job sebaiknya selesai cepat) ---
LOG_SCAN_CHUNK = int(os.environ.get("LOG_SCAN_CHUNK", "10000"))
MAX_SCAN_SECONDS = float(os.environ.get("MAX_SCAN_SECONDS", "45"))
V4_INDEX_START_BLOCK = int(os.environ.get("V4_INDEX_START_BLOCK", "0"))

STATE_FILE = os.environ.get("STATE_FILE", "state/bot_state.enc")

MAX_UINT128 = 2**128 - 1
