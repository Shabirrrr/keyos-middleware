# 🔌 Keyos Dashboard — Olsera Middleware

Server ini menghubungkan Rocket.new dashboard kamu dengan Olsera API.
Sudah disesuaikan persis dengan struktur Financial Dashboard Keyos.

---

## 📁 Isi Folder
```
keyos_middleware/
├── app.py           ← Server Flask (jangan diubah)
├── requirements.txt
├── Procfile
└── README.md
```

---

## 🚀 Deploy ke Railway

### Step 1 — Upload ke GitHub
1. github.com → New repository → nama: `keyos-middleware`
2. Upload semua file dari folder ini
3. Commit changes

### Step 2 — Deploy di Railway
1. railway.app → New Project → Deploy from GitHub
2. Pilih repo `keyos-middleware`
3. Tunggu ~2 menit sampai status Active

### Step 3 — Isi Environment Variables
Railway → Variables → tambahkan:

| Variable | Nilai |
|---|---|
| `OLSERA_APP_ID` | App ID dari Olsera |
| `OLSERA_SECRET_KEY` | Secret Key dari Olsera |

### Step 4 — Generate Domain
Railway → Settings → Domains → Generate Domain

Test di browser:
```
https://[URL KAMU]/api/status
```
Harus muncul: `"has_weekly": true, "has_monthly": true`

---

## 📊 Format Data yang Dikembalikan

### Weekly: GET /api/dashboard?period=weekly
### Monthly: GET /api/dashboard?period=monthly

```json
{
  "total_revenue": 11240000,
  "total_profit": 7200000,
  "total_transaksi": 296,
  "basket_size": 37966,
  "gpm_total": 64.1,

  "store1": {
    "name": "Jl. Yos Sudarso IV",
    "revenue": 6580000,
    "profit": 4210000,
    "transaksi": 178,
    "gpm": 64.1
  },
  "store2": {
    "name": "Jl. Diponegoro",
    "revenue": 4660000,
    "profit": 3000000,
    "transaksi": 118,
    "gpm": 64.4
  },

  "growth": {
    "total_revenue": 9.3,
    "total_transaksi": 4.9,
    "basket_size": 4.2,
    "store1_revenue": 6.7,
    "store2_revenue": 13.4,
    "store1_gpm_diff": -0.8,
    "store2_gpm_diff": 0.2
  },

  "daily_total": [
    {"tanggal": "2026-03-31", "revenue": 1200000, "profit": 768000, "transaksi": 32}
  ],
  "daily_store1": [...],
  "daily_store2": [...],

  "hourly_store1": [
    {"hour": "08:00", "revenue": 0},
    {"hour": "09:00", "revenue": 250000},
    ...
    {"hour": "22:00", "revenue": 150000}
  ],
  "hourly_store2": [...],

  "product_mix": [
    {"name": "Victoria", "revenue": 3800000, "pct": 34},
    {"name": "Scent of Green", "revenue": 2470000, "pct": 22},
    {"name": "Noir Essence", "revenue": 2020000, "pct": 18},
    {"name": "Amber Dusk", "revenue": 1572000, "pct": 14},
    {"name": "Others", "revenue": 1378000, "pct": 12}
  ],

  "top_products": [
    {"item_name": "Victoria", "qty": 51, "revenue": 4080000, "profit": 2536638, "margin_pct": 62.2}
  ],

  "payment": [
    {"payment_type": "CASH", "amount": 6500000},
    {"payment_type": "Qris Yos Sudarso IV", "amount": 3100000},
    {"payment_type": "Qris Diponegoro", "amount": 1640000}
  ],

  "ai_insights": [
    {
      "type": "positive",
      "icon": "trending_up",
      "title": "Revenue up 9.3% this period",
      "message": "Total revenue grew 9.3% vs previous period, peak on 2026-04-05"
    },
    {
      "type": "info",
      "icon": "star",
      "title": "Top product: Victoria",
      "message": "Victoria contributed 34% of revenue across both stores."
    },
    {
      "type": "positive",
      "icon": "flash",
      "title": "Store 2 (Diponegoro) accelerating",
      "message": "Jl. Diponegoro grew 13.4% — fastest growth this period."
    },
    {
      "type": "alert",
      "icon": "warning",
      "title": "Margin compression — Store 1",
      "message": "GPM dipped 0.8pp. Review COGS and procurement costs."
    }
  ],

  "last_updated": "09 Apr 2026, 08:00 WIB"
}
```

---

## 🔗 Prompt untuk Rocket.new

Setelah Railway live, buka Rocket.new → chat editor, paste prompt ini:

```
Connect to external API: https://[URL RAILWAY KAMU]/api/dashboard

Map the JSON response to the dashboard as follows:

KPI CARDS:
- total_revenue → "Total Revenue" card value
- store1.revenue → "Store 1" card value (subtitle: store1.name)
- store2.revenue → "Store 2" card value (subtitle: store2.name)
- total_transaksi → "Transactions" card value
- basket_size → "Basket Size" card value
- store1.profit → Store 1 "Gross Profit" value
- store1.gpm → Store 1 "GPM" percentage
- store2.profit → Store 2 "Gross Profit" value
- store2.gpm → Store 2 "GPM" percentage

GROWTH BADGES (show as green if positive, red if negative):
- growth.total_revenue → Total Revenue % badge
- growth.store1_revenue → Store 1 % badge
- growth.store2_revenue → Store 2 % badge
- growth.total_transaksi → Transactions % badge
- growth.basket_size → Basket Size % badge
- growth.store1_gpm_diff → Store 1 GPM diff (show as "pp" not "%")
- growth.store2_gpm_diff → Store 2 GPM diff

REVENUE TREND CHART (line chart with 3 lines):
- daily_total array (field: tanggal, revenue) → Total line
- daily_store1 array (field: tanggal, revenue) → Store 1 line
- daily_store2 array (field: tanggal, revenue) → Store 2 line

HOURLY SALES CHART (line chart):
- hourly_store1 array (field: hour, revenue) → Store 1 line
- hourly_store2 array (field: hour, revenue) → Store 2 line

PRODUCT MIX (donut chart):
- product_mix array (fields: name, pct) → donut segments
- Show top product name in center of donut

AI INSIGHTS (cards on right side):
- ai_insights array (fields: type, title, message, icon) → insight cards
- type "positive" = green, "warning" = yellow, "alert" = red, "info" = blue

WEEKLY/MONTHLY TOGGLE:
- When "Weekly" is selected: fetch /api/dashboard?period=weekly
- When "Monthly" is selected: fetch /api/dashboard?period=monthly
- Re-render all cards and charts with new data

Refresh data every 5 minutes automatically.
Show loading skeleton while fetching.
```

---

## ❓ Troubleshooting

| Masalah | Solusi |
|---|---|
| `/api/status` → `config_ok: false` | Cek Railway Variables |
| `/api/dashboard` → 503 loading | Tunggu 30 detik, klik refresh |
| Data Store 1 dan Store 2 terbalik | Pastikan produk Store 2 pakai nama "...2nd Store" di Olsera |
| Growth selalu null | Data periode sebelumnya kosong, cek tanggal di Olsera |
