# Monitor LP Robinhood Chain — tanpa VPS, tanpa PC menyala

Bot Telegram yang memantau posisi LP Uniswap V3 & V4 kamu di Robinhood Chain:
PnL dolar, modal masuk, nilai sekarang, fee (sudah & belum diklaim), dan
komposisi meme token vs USDG — plus alert otomatis saat komposisi meme token
melewati ambang yang kamu set.

Berjalan sebagai **GitHub Actions terjadwal**. Tidak ada proses yang menetap,
tidak ada server yang harus kamu bayar atau jaga.

---

## Yang harus kamu tahu sebelum memutuskan pakai ini

**1. `/position` tidak instan.** GitHub Actions tidak punya endpoint HTTPS, jadi
tidak bisa menerima webhook Telegram. Yang dilakukan bot: tiap kali cron jalan,
ia mengambil semua command yang menumpuk lalu membalasnya. Jadi jawaban
`/position` datang dengan **jeda sampai satu interval cron** (default 5 menit,
sering lebih lama karena cron GitHub memang tidak presisi saat platformnya sibuk).
Kalau kamu butuh jawaban seketika, GitHub Actions bukan tempatnya — perlu
serverless dengan webhook.

**2. Repo harus PUBLIK supaya gratis.** Repo privat hanya dapat 2.000 menit
Actions per bulan dan tiap job dibulatkan ke menit penuh. Cron 5 menit =
~8.640 menit/bulan, empat kali lipat kuota. Repo publik = menit tanpa batas di
runner standar. Karena itu **seluruh state bot dienkripsi AES-256-GCM** sebelum
disimpan — address wallet dan tokenId posisi kamu tidak pernah tertulis polos di
repo publik. Kalau kamu tetap ingin repo privat, ubah cron ke `*/30 * * * *`
dan terima jeda 30 menit.

**3. Bot ini hanya MEMBACA.** Tidak ada satu pun fungsi yang bisa mengirim
transaksi atau memindahkan dana. Tidak ada private key di mana pun. Kalau
token bot Telegram-mu bocor, kerugian maksimalnya orang lain bisa melihat
posisimu — bukan mengambilnya.

**4. Ini bukan pengaman.** Cron bisa telat atau terlewat. Jangan jadikan bot ini
satu-satunya yang menjaga posisi kamu.

---

## Setup

**a. Buat repo publik baru**, salin semua isi folder ini ke sana, push.

**b. Buat bot Telegram** lewat @BotFather, simpan tokennya. Kirim satu pesan ke
bot itu, lalu buka
`https://api.telegram.org/bot<TOKEN>/getUpdates` untuk melihat `chat.id` kamu.

**c. Isi repository secrets** (Settings → Secrets and variables → Actions → Secrets):

| Secret | Isi |
|---|---|
| `TELEGRAM_BOT_TOKEN` | token dari BotFather |
| `TELEGRAM_CHAT_ID` | chat id tujuan alert |
| `STATE_KEY` | passphrase acak panjang untuk enkripsi state — **simpan baik-baik**, kalau hilang riwayat fee terlacak ikut hilang |
| `ROBINHOOD_RPC_URL` | opsional, kalau kamu punya RPC sendiri yang limitnya lebih longgar |

Ambang alert bisa diatur lewat *Variables* (bukan Secrets): `ALERT_WARN_PCT`
(default 75), `ALERT_CRIT_PCT` (default 90).

**d. Verifikasi address kontrak** — jalankan di mesin mana pun yang bisa
menjangkau RPC:

```bash
pip install -r requirements.txt
python3 verify_setup.py --wallet 0xAlamatKamu
```

**e. Aktifkan workflow**, lalu jalankan manual sekali lewat tombol *Run workflow*.
Kirim `/addwallet 0x…` ke bot, tunggu run berikutnya.

---

## Command

```
/position                    semua posisi + PnL + komposisi
/position <address>          cek satu address langsung
/addwallet <address> [label] daftarkan wallet
/wallets                     daftar wallet terpantau
/removewallet <address>      hapus
/help
```

Alert dikirim otomatis (tanpa diminta) saat komposisi meme token melewati 75%,
lalu 90%, lalu saat posisi keluar range.

---

## Contoh keluaran

```
📊 wallet utama · 0x1111…1111
Posisi 2 · Nilai $256.19 · Fee belum diklaim $3.56
PnL total -$25.09 (-8.4%)

LONGCAT/USDG [V3] #111  🔴 OOR
  Range   0.002015 – 0.006052   now 0.0018
  Komposisi ██████████ 100% LONGCAT / 0% USDG
  Modal   $100.00   →   Nilai $56.19
  Fee     belum $2.04 | sudah $8.16
  PnL     🟥 -$33.61 (-33.6%)

CASHCAT/USDG [V4] #2653465  🟢 IN
  Range   0.084799 – 0.170758   now 0.119997
  Komposisi █████░░░░░ 50% CASHCAT / 50% USDG
  Modal   $200.00 ~   →   Nilai $200.00
  Fee     belum $1.52 | sudah $7.00
  PnL     🟩 $8.52 (+4.3%)
```

Tanda `~` di belakang Modal artinya cost basis-nya aproksimasi (lihat bawah).

---

## Bagaimana PnL dihitung tanpa archive node

Menghitung PnL dolar butuh **harga saat kamu deposit**, bukan harga sekarang.
Cara umum adalah membaca state pool di block deposit lewat archive node — tapi
RPC publik memangkas state lama, jadi tidak bisa diandalkan. Bot ini memakai dua
jalan lain:

**Uniswap V3 — derivasi aljabar (eksak).** Event `IncreaseLiquidity` mencatat
`(L, amount0, amount1)` saat mint. Dari identitas Uniswap `amount1 = L·(√P − √Pa)`,
harga saat deposit bisa dibalik persis: `√P = √Pa + amount1/L`. Tidak ada
estimasi. Ini sudah diuji round-trip: simulasikan deposit di harga X, lalu minta
kode menebak harganya hanya dari angka event — hasilnya kembali ke X.

**Uniswap V4 — dari event Swap (aproksimasi).** Event `ModifyLiquidity` di V4
tidak memuat jumlah token sama sekali (diverifikasi dari `IPoolManager.sol`),
jadi cara aljabar tidak bisa dipakai. Harga deposit diambil dari event `Swap`
terakhir sebelum block deposit. Kalau pool sepi menjelang deposit, harganya
sedikit basi. Posisi seperti ini ditandai `~`.

Kalau deposit dilakukan *single-sided* (di luar range), harga masuk secara
matematis tidak dapat ditentukan — bot menampilkan `PnL n/a`, bukan menebak.

---

## Fee terklaim V4: keterbatasan yang harus kamu terima

Kamu memilih rekonstruksi penuh dari riwayat chain. Itu diimplementasikan di
`v4_history.py`, tapi V4 tidak punya event `Collect` yang rapi seperti V3.
Metodenya: cari `ModifyLiquidity` dengan `salt == bytes32(tokenId)`, lalu baca
Transfer ERC20 dari PoolManager di transaksi yang sama.

Empat hal bisa membuat angkanya meleset, dan tidak bisa saya hilangkan:

1. **Transaksi batch.** V4 dirancang mengeksekusi banyak aksi sekaligus dengan
   flash accounting. Kalau bot rebalance kamu menutup satu posisi dan membuka
   yang lain dalam satu transaksi, transfer ERC20-nya sudah dinetokan dan tidak
   bisa dibelah lagi. Kasus ini **ditandai, bukan ditebak**.
2. **Penerima perantara.** Kalau fee lewat router dulu, pencocokan meleset.
3. **Penarikan sebagian.** Pemisahan pokok vs fee memakai harga dari event Swap
   terdekat, jadi aproksimasi.
4. **Native ETH** tidak muncul sebagai Transfer ERC20 (tidak relevan untuk pair
   vs USDG).

**Validasi sebelum percaya:** ambil satu posisi V4 yang pernah kamu klaim
fee-nya, bandingkan angka bot dengan yang benar-benar masuk ke wallet.

---

## Status pengujian

Yang **sudah terbukti** — 33 test lulus, semuanya berjalan tanpa jaringan:

- `test_uniswap_math.py` (9) — tick↔harga, liquidity→token, decode PositionInfo
  V4, PoolId, wraparound feeGrowth uint256
- `test_pnl.py` (15) — round-trip derivasi harga masuk, cost basis
  tertimbang lintas beberapa deposit, penandaan kualitas basis, komposisi, PnL
- `test_alerts.py` (9) — histeresis: 5 run berturut di atas ambang = 1 alert
  (bukan 5), eskalasi tetap bunyi, osilasi di 74,9/75,1% tidak spam
- `simulate.py` — siklus cron penuh dengan chain & Telegram palsu: baca posisi →
  alert → layani command, lintas 4 run, termasuk persistensi state terenkripsi

Yang **belum terbukti** dan hanya bisa kamu buktikan sendiri: apakah ABI dan
address benar-benar cocok dengan kontrak di Robinhood Chain. Sandbox tempat kode
ini ditulis diblokir aksesnya ke RPC Robinhood Chain, jadi tidak ada satu pun
panggilan ke chain sungguhan yang pernah dijalankan. `verify_setup.py` ada untuk
menutup celah itu.

---

## Berkas

| Berkas | Isi |
|---|---|
| `bot.py` | entry point satu-run (dipanggil cron) |
| `onchain.py` | pembacaan posisi V3 & V4 |
| `pnl.py` | cost basis, PnL, komposisi |
| `v4_history.py` | rekonstruksi riwayat & fee terklaim V4 |
| `alerts.py` | mesin ambang + histeresis |
| `state.py` | state terenkripsi AES-256-GCM |
| `report.py` | render teks Telegram |
| `telegram_api.py` | klien Telegram minimal (HTTP) |
| `uniswap_math.py` | matematika tick/liquidity |
| `verify_setup.py` | **jalankan duluan** |
| `simulate.py` | simulasi end-to-end tanpa jaringan |
| `.github/workflows/monitor.yml` | jadwal cron |
