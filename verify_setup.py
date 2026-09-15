"""
Jalankan SEBELUM mempercayai angka apa pun dari bot ini:

    python3 verify_setup.py

Read-only sepenuhnya - tidak mengirim transaksi, tidak menyentuh dana.
Tujuannya membuktikan bahwa address kontrak di config.py memang contract hidup
di Robinhood Chain, bukan salah ketik hasil transkripsi dokumentasi.

Tambahkan --wallet 0x... untuk sekalian mencoba membaca posisi wallet itu.
"""

import argparse
import sys

import requests
from web3 import Web3

import config


def diagnose_rpc():
    """
    Tembak RPC dengan HTTP mentah SEBELUM web3 dipakai.

    Kenapa perlu: web3.is_connected() cuma mengembalikan True/False, jadi kalau
    gagal kita tidak tahu apa sebabnya - DNS mati, Cloudflare menolak IP runner,
    endpoint minta API key, atau balasannya HTML bukan JSON. Fungsi ini mencetak
    status code dan cuplikan body apa adanya supaya penyebabnya kelihatan
    langsung di log, bukan ditebak.
    """
    print("=" * 60)
    print("DIAGNOSA RPC (HTTP mentah)")
    print("=" * 60)
    url = config.RPC_URL
    shown = url if "alchemy" not in url else url.rsplit("/", 1)[0] + "/<API_KEY disembunyikan>"
    print(f"URL: {shown}")

    try:
        r = requests.post(
            url,
            json={"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []},
            timeout=20,
            headers={"Content-Type": "application/json"},
        )
    except Exception as e:
        print(f"GAGAL di level HTTP: {type(e).__name__}: {e}")
        print("\nArtinya endpoint tidak bisa dihubungi sama sekali dari mesin ini "
              "(DNS, firewall, atau host mati).")
        return False

    print(f"HTTP status: {r.status_code}")
    ctype = r.headers.get("content-type", "-")
    print(f"Content-Type: {ctype}")
    body = r.text[:300].replace("\n", " ")
    print(f"Body (300 karakter pertama): {body}")

    if r.status_code == 200 and "json" in ctype:
        try:
            data = r.json()
            if "result" in data:
                print(f"OK - chainId dari RPC: {int(data['result'], 16)}")
                return True
            print(f"Balasan JSON tapi tanpa 'result': {data}")
        except Exception as e:
            print(f"Status 200 tapi body bukan JSON valid: {e}")
    elif r.status_code in (401, 403):
        print("\nDITOLAK. Dua sebab paling umum:")
        print("  - endpoint butuh API key (dokumentasi resmi Robinhood memang "
              "menganjurkan Alchemy dengan API key), atau")
        print("  - Cloudflare memblokir IP datacenter seperti runner GitHub.")
        print("Solusinya sama: pakai RPC ber-API-key, isi secret ROBINHOOD_RPC_URL.")
    elif r.status_code == 429:
        print("\nKENA RATE LIMIT. Pakai RPC ber-API-key lewat secret ROBINHOOD_RPC_URL.")
    elif "html" in ctype.lower():
        print("\nBalasannya HALAMAN HTML, bukan JSON-RPC - ini ciri khas halaman "
              "tantangan/blokir Cloudflare. Pakai RPC ber-API-key lewat secret "
              "ROBINHOOD_RPC_URL.")
    return False


def check_code(w3, label, address):
    try:
        code = w3.eth.get_code(Web3.to_checksum_address(address))
    except Exception as e:
        print(f"  GAGAL {label} ({address}): {e}")
        return False
    if not code or len(code) == 0:
        print(f"  GAGAL {label} ({address}): TIDAK ADA BYTECODE - address salah/typo")
        return False
    print(f"  OK    {label} ({address}) - {len(code)} byte bytecode")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wallet", help="opsional: coba baca posisi wallet ini")
    args = ap.parse_args()

    rpc_ok = diagnose_rpc()
    print()
    if not rpc_ok:
        print("Berhenti di sini: tanpa RPC yang bisa dihubungi, pengecekan kontrak "
              "tidak mungkin dilakukan. Perbaiki RPC dulu (lihat pesan di atas), "
              "lalu jalankan ulang.")
        return 1

    w3 = Web3(Web3.HTTPProvider(config.RPC_URL, request_kwargs={"timeout": 25}))
    try:
        if not w3.is_connected():
            print("GAGAL: HTTP mentah berhasil tapi web3 tetap menolak koneksi - "
                  "kemungkinan balasan RPC tidak sesuai standar JSON-RPC.")
            return 1
    except Exception as e:
        print(f"GAGAL konek lewat web3: {type(e).__name__}: {e}")
        return 1

    cid = w3.eth.chain_id
    print(f"OK: terhubung, chain_id={cid} (harusnya {config.CHAIN_ID}), "
          f"block terkini {w3.eth.block_number:,}")
    if cid != config.CHAIN_ID:
        print("GAGAL: chain id tidak cocok - kamu tersambung ke chain lain.")
        return 1

    print("\nMemverifikasi address kontrak:")
    ok = all([
        check_code(w3, "Uniswap V3 Factory", config.V3_FACTORY),
        check_code(w3, "Uniswap V3 NonfungiblePositionManager", config.V3_NFPM),
        check_code(w3, "Uniswap V4 PoolManager", config.V4_POOL_MANAGER),
        check_code(w3, "Uniswap V4 PositionManager", config.V4_POSITION_MANAGER),
        check_code(w3, "Uniswap V4 StateView", config.V4_STATE_VIEW),
        check_code(w3, "USDG token", config.USDG_ADDRESS),
    ])

    print("\nMemeriksa token USDG:")
    try:
        from abis import ERC20_ABI
        c = w3.eth.contract(address=Web3.to_checksum_address(config.USDG_ADDRESS), abi=ERC20_ABI)
        print(f"  symbol={c.functions.symbol().call()} decimals={c.functions.decimals().call()}")
        print("  (decimals selalu dibaca langsung dari kontrak saat runtime, "
              "tidak pernah di-hardcode)")
    except Exception as e:
        print(f"  GAGAL baca metadata USDG: {e}")
        ok = False

    # Cek batas eth_getLogs - ini yang paling sering membatasi di RPC publik,
    # dan langsung menentukan seberapa cepat index V4 bisa menyusul.
    print("\nMenguji dukungan eth_getLogs (dipakai untuk index posisi V4):")
    try:
        latest = w3.eth.block_number
        logs = w3.eth.get_logs({"fromBlock": max(0, latest - 1000), "toBlock": latest,
                                "address": Web3.to_checksum_address(config.V4_POSITION_MANAGER)})
        print(f"  OK - {len(logs)} log dalam 1000 block terakhir")
    except Exception as e:
        print(f"  PERINGATAN: eth_getLogs ditolak/dibatasi: {e}")
        print("  Index posisi V4 akan lambat atau gagal. Pertimbangkan RPC lain "
              "(set ROBINHOOD_RPC_URL) yang mengizinkan query log lebih lebar.")

    if args.wallet:
        print(f"\nMencoba membaca posisi {args.wallet}:")
        try:
            import onchain
            v3 = onchain.read_v3(w3, args.wallet)
            print(f"  V3: {len(v3)} posisi terbuka")
            for p in v3:
                print(f"    #{p['token_id']} {p['pair']} "
                      f"{'IN' if p['in_range'] else 'OOR'} "
                      f"nilai={p.get('value_usd')} modal={p.get('invested_usd')} "
                      f"basis={p.get('basis_quality')}")
        except Exception as e:
            print(f"  GAGAL baca V3: {type(e).__name__}: {e}")
            ok = False
        print("  V4: butuh index Transfer yang tersinkron - jalankan bot.py sekali "
              "(atau beberapa kali sampai index selesai) untuk mengujinya.")

    print("\n" + ("SEMUA CEK UTAMA LULUS." if ok else
                  "ADA YANG GAGAL - jangan pakai angkanya sampai ini beres."))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
