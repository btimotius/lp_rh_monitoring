# Konteks Project — LP Monitor Robinhood Chain

Dokumen serah-terima untuk sesi/alat berikutnya (Claude Code, Copilot, Cursor,
atau manusia). Isinya: keputusan yang sudah diambil, fakta yang sudah
diverifikasi beserta sumbernya, jebakan yang sudah pernah menggigit, dan status
terkini. Tujuannya supaya tidak ada yang perlu menemukan ulang hal-hal mahal di
bawah ini.

Terakhir diperbarui: 2026-09-15.

---

## Apa ini

Bot Telegram yang memantau posisi LP Uniswap V3 & V4 milik pemilik repo di
Robinhood Chain (chainId 4663): PnL dolar, modal masuk, nilai posisi, fee
(sudah & belum diklaim), dan komposisi meme token vs USDG. Mengirim alert
otomatis saat komposisi meme token melewati ambang.

Repo: `btimotius/lp_rh_monitoring` (PUBLIK, disengaja — lihat di bawah).

**Bot ini hanya MEMBACA chain.** Tidak ada private key di mana pun, tidak ada
fungsi yang bisa mengirim transaksi. Jangan tambahkan kemampuan menulis ke
chain di repo ini tanpa membahas ulang model keamanannya — repo ini publik.

---

## Keputusan arsitektur dan alasannya

**Runtime: GitHub Actions terjadwal, bukan proses menetap.** Syarat utama dari
pemilik: tidak mau menyalakan VPS/PC 24 jam. Konsekuensi yang harus diterima:

- **`/position` tidak instan.** GitHub Actions tidak punya endpoint HTTPS, jadi
  webhook Telegram mustahil. Command diambil dengan `getUpdates` tiap cron
  jalan, jadi balasan tertunda sampai satu interval (dan cron GitHub sering
  telat lagi saat platformnya sibuk). Ini sudah dijelaskan ke pemilik dan
  diterima. Jangan "perbaiki" dengan menambah polling menetap — itu melanggar
  syarat awalnya.
- Kalau suatu saat `/position` instan benar-benar dibutuhkan, jalurnya adalah
  komponen webhook terpisah di serverless (Vercel/Cloud Run), bukan mengubah
  GitHub Actions.

**Repo PUBLIK, disengaja.** Repo privat hanya dapat 2.000 menit Actions/bulan
dan tiap job dibulatkan ke menit penuh; cron 5 menit = ~8.640 menit/bulan,
empat kali lipat kuota. Repo publik = menit tanpa batas di runner standar.
Karena itu:

- **Seluruh state dienkripsi AES-256-GCM** (`state.py`) sebelum disimpan.
  Alamat wallet dan tokenId tidak boleh pernah tertulis polos di repo.
- **Jangan pernah mencetak alamat wallet atau detail posisi ke log Actions.**
  Log repo publik bisa dibaca siapa saja. `verify.yml` sengaja tidak menerima
  parameter wallet karena alasan ini.
- Kunci enkripsi ada di secret `STATE_KEY`. Kalau hilang: bot tidak crash, ia
  mulai dari state kosong dan memberi tahu lewat Telegram. Yang hilang hanya
  progres index V4 dan riwayat fee terlacak.

**State disimpan di branch `bot-state` sebagai satu commit yang selalu
ditimpa** (force push), bukan di branch utama. Alasan: commit tiap 5 menit ke
`main` akan menumpuk ~8.600 commit sampah per bulan.

---

## Fakta terverifikasi (jangan cari ulang)

Semua di bawah ini sudah dicek ke sumber primer. Sumbernya dicantumkan supaya
bisa diperiksa ulang kalau ada yang mencurigakan.

### Alamat kontrak Robinhood Chain (chainId 4663)

Sumber: developers.uniswap.org (halaman deployment V3 & V4) dan
docs.robinhood.com/chain/contracts, diambil 13 Sep 2026.

| Kontrak | Address |
|---|---|
| UniswapV3Factory | `0x1f7d7550b1b028f7571e69a784071f0205fd2efa` |
| V3 NonfungiblePositionManager | `0x73991a25c818bf1f1128deaab1492d45638de0d3` |
| V4 PoolManager | `0x8366a39cc670b4001a1121b8f6a443a643e40951` |
| V4 PositionManager | `0x58daec3116aae6d93017baaea7749052e8a04fa7` |
| V4 StateView | `0xf3334192d15450cdd385c8b70e03f9a6bd9e673b` |
| USDG | `0x5fc5360d0400a0fd4f2af552add042d716f1d168` |

**BELUM diverifikasi terhadap chain sungguhan.** Diambil lewat fetch
dokumentasi otomatis, jadi ada risiko kecil salah transkripsi digit.
`verify_setup.py` memverifikasi tiap address punya bytecode — jalankan itu dan
pastikan hijau sebelum mempercayai angka apa pun.

### RPC

Dokumentasi resmi Robinhood (docs.robinhood.com/chain/connecting) **hanya
mencantumkan Alchemy dengan API key**:
`https://robinhood-mainnet.g.alchemy.com/v2/{API_KEY}`.

Endpoint publik `https://rpc.mainnet.chain.robinhood.com` (dari situs pihak
ketiga) memang resolve — Cloudflare di depan `customer-origin.offchainlabs.com`,
konsisten dengan Robinhood Chain sebagai Arbitrum Orbit. **Tapi belum terbukti
bisa dipakai dari runner GitHub**; Cloudflare rutin menolak IP datacenter. Ini
masih masalah terbuka (lihat Status di bawah). Kalau tertolak: daftar Alchemy
gratis, isi secret `ROBINHOOD_RPC_URL`.

### Perilaku Uniswap V4 (diverifikasi dari source code)

- **PositionManager TIDAK mendukung enumerasi ERC721.** Hanya ada
  `getPoolAndPositionInfo` dan `getPositionLiquidity` — tidak ada
  `tokenOfOwnerByIndex`. Karena itu posisi V4 ditemukan lewat scan event
  `Transfer` bertahap yang progresnya disimpan di state. (V3 sebaliknya:
  enumerasi didukung, jadi murah dan langsung.)
- **`owner` di position key level PoolManager adalah alamat PositionManager
  itu sendiri, bukan wallet user**, dan **`salt = bytes32(tokenId)`**.
  Diverifikasi dari `v4-periphery/src/PositionManager.sol`
  (`Position.calculatePositionKey(address(this), tickLower, tickUpper, bytes32(tokenId))`).
  Salah di sini menghasilkan liquidity/fee nol atau ngawur tanpa error.
- **`poolId = keccak256(abi.encode(poolKey))`** dengan urutan field
  `(currency0, currency1, fee, tickSpacing, hooks)` — dari `v4-core/src/types/PoolId.sol`.
- **`PositionInfo` packed uint256**: bit 0-7 hasSubscriber, bit 8-31 tickLower
  (int24), bit 32-55 tickUpper (int24), bit 56+ poolId TERPOTONG (25 byte —
  jangan dipakai untuk query StateView, hitung ulang poolId penuh dari poolKey).
  Dari `v4-periphery/src/libraries/PositionInfoLibrary.sol`.
- **Event `ModifyLiquidity` TIDAK memuat amount0/amount1** — hanya
  `(id indexed, sender indexed, tickLower, tickUpper, liquidityDelta, salt)`.
  `salt` tidak indexed, jadi filter tokenId harus dilakukan di sisi klien.
  Dari `v4-core/src/interfaces/IPoolManager.sol`.
- Event `Swap` V4 memuat `sqrtPriceX96` — ini dipakai sebagai sumber harga
  historis untuk cost basis V4.

### LP Agent (sumber data alternatif yang TIDAK dipakai)

API-nya nyata (`https://api.lpagent.io/open-api/v1/lp-positions/opening`,
auth `x-api-key`, mendukung `chain=ROBINHOOD`), berbayar mulai $10 sekali bayar
dengan 5 request/menit. **Tidak dipakai** karena pemilik memilih jalur on-chain
gratis. Dua catatan kalau suatu saat dipertimbangkan lagi: dokumentasi
integrasi Robinhood mereka hanya menyebut Uniswap V3 (tidak menyebut V4 sama
sekali, padahal posisi pemilik ada yang V4), dan dokumentasi mereka sendiri
menyatakan *"PnL untuk posisi Robinhood tidak lengkap"*.

---

## Temuan teknis paling penting: PnL tanpa archive node

Menghitung PnL dolar butuh **harga saat deposit**, bukan harga sekarang. Cara
biasa adalah membaca state pool di block deposit lewat archive node — RPC
publik memangkas state lama, jadi tidak bisa diandalkan.

**Untuk V3, harga masuk bisa diturunkan secara EKSAK secara aljabar.** Event
`IncreaseLiquidity` mencatat `(L, amount0, amount1)`. Dari identitas Uniswap
untuk posisi in-range:

```
amount1 = L · (√P − √Pa)      →      √P = √Pa + amount1/L
```

Tidak ada estimasi, tidak ada pembacaan state historis. Diuji round-trip di
`test_pnl.py`: simulasikan deposit di harga X, lalu minta kode menebak harganya
hanya dari angka event — hasilnya kembali ke X.

**Untuk V4 tidak bisa** karena `ModifyLiquidity` tidak memuat jumlah token.
Harga diambil dari event `Swap` terakhir sebelum block deposit — aproksimasi,
ditandai `~` di output dan `basis_quality = "approx"`.

**Deposit single-sided (di luar range) → harga masuk secara matematis tidak
dapat ditentukan.** Kode menampilkan `PnL n/a`, tidak menebak. Jangan "perbaiki"
ini dengan menebak batas range sebagai harga masuk — itu menghasilkan PnL yang
salah dan tampak meyakinkan.

---

## Keterbatasan yang diketahui (jangan diklaim sudah beres)

- **Fee terklaim V4 direkonstruksi best-effort** (`v4_history.py`): cari
  `ModifyLiquidity` dengan salt cocok, lalu baca Transfer ERC20 dari PoolManager
  di transaksi yang sama. Gagal akurat kalau satu transaksi menyentuh lebih dari
  satu posisi — flash accounting V4 sudah menetokan transfernya dan tidak bisa
  dibelah lagi. Kasus begitu **ditandai, bukan ditebak**. Ini relevan karena bot
  rebalance milik pemilik kemungkinan menutup+membuka posisi dalam satu tx.
  **Belum divalidasi terhadap posisi nyata.**
- Nilai USD hanya dihitung untuk pair yang salah satu sisinya USDG (diasumsikan
  $1). Pair lain tampil `n/a`, bukan angka karangan.
- Cron GitHub tidak presisi: bisa telat, sesekali terlewat. Bot ini **bukan
  pengaman posisi**, hanya pemantau.

---

## Jebakan yang sudah pernah menggigit

- **`os.environ.get(name, default)` tidak memakai default kalau variabelnya ADA
  tapi KOSONG.** Di GitHub Actions, `FOO: ${{ secrets.FOO }}` tetap membuat
  variabel meski secret-nya belum diisi — isinya string kosong. Ini pernah
  membuat `RPC_URL` kosong dan bot gagal konek dengan pesan membingungkan.
  Sudah diperbaiki: pakai helper `_env_or()` di `config.py`. **Gunakan
  `_env_or()` untuk setiap env var baru**, jangan `os.environ.get` langsung.
- **Setting repo "Workflow permissions" adalah plafon, bukan default.** Repo
  pribadi baru memberi `GITHUB_TOKEN` akses read-only untuk `contents`, dan blok
  `permissions: contents: write` di workflow TIDAK bisa menaikkannya. Tanpa
  diubah ke "Read and write permissions" di Settings → Actions → General,
  langkah simpan state gagal diam-diam — akibatnya alert spam tiap 5 menit
  (histeresis ter-reset) dan index V4 tidak pernah maju.
- **Arah harga tergantung urutan address token.** Uniswap mengurutkan
  token0 < token1 by address, jadi USDG bisa jadi token0 ATAU token1. Pernah
  membuat range tampil terbalik untuk pair yang USDG-nya token0. Ditangani di
  `report.display_prices()`; ada test untuk ini di `simulate.py`.
- **Jangan pakai token bot Telegram yang sama dengan bot lain.** Dua proses
  `getUpdates` pada satu token saling mencuri pesan.

---

## Status terkini & yang masih harus dikerjakan

Selesai: seluruh kode, 33 test lulus (`test_uniswap_math.py` 9,
`test_pnl.py` 15, `test_alerts.py` 9, plus `simulate.py` end-to-end dengan chain
& Telegram palsu). Repo sudah dibuat publik dan di-push. `verify.yml` sudah ada.

Selesai (update 2026-09-16): `verify_setup.py` via workflow `Verify Setup`
sudah **hijau** (run pertama 2026-09-15 17:26 gagal konek RPC dari runner —
kemungkinan gangguan sesaat di sisi RPC publik/Cloudflare, bukan pola
konsisten; run ulang 2026-09-15 18:52 sukses). Jadi: runner GitHub terbukti
bisa menjangkau RPC publik default dan semua address kontrak terverifikasi
punya bytecode.

**RPC publik default TERBUKTI kena rate limit (429) di beban nyata.**
`verify_setup.py` lolos karena cuma sekali panggil `eth_getLogs` ringan, tapi
index V4 sungguhan (`onchain.sync_v4_index`) butuh ribuan panggilan
`eth_getLogs` berturutan (chunk 10.000 block, dari `V4_INDEX_START_BLOCK` ke
block terkini) — 2026-09-15 19:xx, run nyata pertama dengan wallet terdaftar
langsung dapat `HTTPError: 429` dan gagal baca semua posisi V4. **Pindah ke
`ROBINHOOD_RPC_URL` ber-API-key sekarang wajib**, bukan opsional lagi — ini
bukan skenario hipotetis di README, tapi sudah kejadian.

**Perbandingan provider RPC (dicoba 2026-09-15/16, jangan diulang tanpa
alasan baru):**

| Provider | Hasil |
|---|---|
| Alchemy | Dashboard pemilik error terus ("Page could not be loaded") di semua browser/jaringan yang dicoba — bukan masalah akun, kemungkinan besar isu di sisi Alchemy/regional. Belum bisa dipakai sampai itu beres sendiri. |
| QuickNode | Free tier sekarang cuma trial berwaktu, bukan free permanen. |
| thirdweb | Sama, cuma trial. |
| Chainstack | `eth_getLogs` **diblokir total** di free plan — dianggap fitur "Archive/Debug/Trace", butuh upgrade berbayar. Bukan soal limit range, memang dikunci. |
| **dRPC** | **Yang dipakai sekarang.** `eth_getLogs` jalan tapi limit **100 block per panggilan** (pesan error mereka bilang "10000" — itu salah/menyesatkan, sudah dibuktikan lewat bisection manual). Throughput terukur: ~172 panggilan sukses / 45 detik (~378 block/detik), sesekali `408 Request timeout` (~1 dari 85 panggilan). |

**Konsekuensi ke config:** `LOG_SCAN_CHUNK` dan `MAX_SCAN_SECONDS` di
`config.py` awalnya TIDAK disambungkan ke `monitor.yml` (cuma bisa diubah
lewat kode, bukan repo Variable). Sudah ditambahkan sebagai env di
`monitor.yml` (baca dari `vars.LOG_SCAN_CHUNK` / `vars.MAX_SCAN_SECONDS`,
default tetap 10000/45 kalau tidak diisi). Nilai yang dipakai sekarang:
`LOG_SCAN_CHUNK=90` (di bawah limit 100 dRPC), `MAX_SCAN_SECONDS=240` (masih
aman di bawah timeout job 10 menit). Dengan ini, estimasi index V4 mengejar
dari `V4_INDEX_START_BLOCK` ke block terkini: beberapa hari, bukan
berminggu-minggu seperti kalau dibiarkan di setting default.

Belum selesai:

1. Secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `STATE_KEY` — status
   pengisian belum dikonfirmasi.
4. Workflow permissions belum dikonfirmasi diubah ke Read and write.
5. `V4_INDEX_START_BLOCK` belum diisi — tanpa ini scan V4 mulai dari block 0 dan
   butuh banyak siklus cron sebelum posisi V4 muncul.
6. **Validasi angka V4 terhadap posisi nyata belum dilakukan** — bandingkan fee
   terklaim yang dilaporkan bot dengan yang benar-benar masuk ke wallet.

---

## Cara kerja yang disepakati

Jangan menyajikan angka yang belum tervalidasi sebagai fakta. Kode ini
membedakan secara eksplisit antara yang eksak, aproksimasi (`~`), dan tidak
diketahui (`n/a`) — pertahankan pembedaan itu di setiap fitur baru. Kalau
sebuah nilai tidak bisa dihitung dengan benar, tampilkan `n/a` dan katakan
kenapa; jangan isi dengan tebakan yang tampak masuk akal.
