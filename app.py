"""
app.py — Olsera Middleware untuk Keyos Financial Dashboard
Pisahkan Store 1 vs Store 2 dari item_name, Weekly + Monthly toggle

Endpoints:
  GET /api/dashboard?period=weekly   → data minggu ini vs minggu lalu
  GET /api/dashboard?period=monthly  → data bulan ini vs bulan lalu
  GET /api/status
  GET /api/refresh
"""

import os, requests, threading, time
from datetime import datetime, timedelta, date
from flask import Flask, jsonify, request, Response

app = Flask(__name__)

APP_ID     = os.environ.get('OLSERA_APP_ID', '')
SECRET_KEY = os.environ.get('OLSERA_SECRET_KEY', '')
BASE_URL   = "https://api-open.olsera.co.id/api/open-api/v1/id"

_cache = {
    "weekly": None, "monthly": None,
    "token": None, "token_saved_at": None,
    "fetching": False, "last_updated": None,
}

# Store 2 = item_name mengandung "2nd store" (case-insensitive)
def is_store2_item(name):
    return "2nd store" in str(name).lower()

# ── CORS ──────────────────────────────────────────────
@app.after_request
def cors(r):
    r.headers['Access-Control-Allow-Origin'] = '*'
    r.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
    r.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return r

@app.route('/api/dashboard', methods=['OPTIONS'])
def options_handler():
    return Response('', 200)

# ── Token ─────────────────────────────────────────────
def get_token():
    if _cache['token'] and _cache['token_saved_at']:
        if (datetime.now() - _cache['token_saved_at']).seconds < 3000:
            return _cache['token']
    return _generate_token()

def _generate_token():
    try:
        r = requests.post(f"{BASE_URL}/token",
            json={"app_id": APP_ID, "secret_key": SECRET_KEY, "grant_type": "secret_key"},
            timeout=30)
        r.raise_for_status()
        d = r.json()
        ti = d.get('data', d)
        token = ti.get('access_token') or ti.get('token')
        if token:
            _cache['token'] = token
            _cache['token_saved_at'] = datetime.now()
            print(f"[TOKEN] OK {datetime.now().strftime('%H:%M:%S')}")
            return token
    except Exception as e:
        print(f"[TOKEN] Error: {e}")
    return None

# ── Fetch orders ──────────────────────────────────────
def fetch_orders(start_date, end_date):
    token = get_token()
    if not token:
        return []

    all_orders, page = [], 1
    print(f"[FETCH] {start_date} → {end_date}")

    while True:
        headers = {"Accept": "application/json", "Content-Type": "application/json",
                   "Authorization": f"Bearer {token}"}
        params  = {"start_date": str(start_date), "end_date": str(end_date),
                   "page": page, "per_page": 100}
        try:
            r = requests.get(f"{BASE_URL}/order/closeorder/list",
                             headers=headers, params=params, timeout=30)
            if r.status_code == 401:
                token = _generate_token()
                if not token: break
                headers["Authorization"] = f"Bearer {token}"
                r = requests.get(f"{BASE_URL}/order/closeorder/list",
                                 headers=headers, params=params, timeout=30)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"[FETCH] Error p{page}: {e}")
            break

        orders = []
        if isinstance(data, list):
            orders = data
        elif isinstance(data, dict):
            for k in ['data', 'orders', 'result', 'items']:
                if k in data and isinstance(data[k], list):
                    orders = data[k]; break
        if not orders: break
        all_orders.extend(orders)

        meta = data.get("meta", {}) if isinstance(data, dict) else {}
        last = int(meta.get("last_page") or meta.get("total_pages") or 1)
        if page >= last or len(orders) < 100: break
        page += 1

    print(f"[FETCH] {len(all_orders)} orders")
    return all_orders

# ── Process orders → dashboard metrics ───────────────
def process_orders(orders):
    if not orders:
        return _empty()

    s1_rev = s1_prof = s1_txn = 0.0
    s2_rev = s2_prof = s2_txn = 0.0
    tot_rev = tot_prof = tot_txn = 0.0

    daily_s1 = {}; daily_s2 = {}; daily_tot = {}
    hourly_s1 = {}; hourly_s2 = {}
    produk_all = {}
    payment_map = {}

    for o in orders:
        tgl = str(o.get("order_date") or o.get("order_date_time") or
                  o.get("created_at") or "")[:10]
        if not tgl or tgl < "2020-01-01": continue

        dt_str  = str(o.get("order_date_time") or o.get("created_at") or "")
        hour    = int(dt_str[11:13]) if len(dt_str) >= 13 else -1
        amount  = float(o.get("total_amount") or o.get("amount") or o.get("grand_total") or 0)
        profit  = float(o.get("profit") or o.get("total_profit") or 0)
        payment = str(o.get("payment_type") or o.get("payment_method") or "Lainnya").strip()

        items = (o.get("items") or o.get("order_items") or
                 o.get("details") or o.get("order_details") or [])

        # Tentukan store: ada item "2nd store" → Store 2
        order_is_s2 = any(is_store2_item(i.get("item_name") or i.get("product_name") or "")
                          for i in items)
        if not items:  # fallback jika tidak ada item detail
            sn = str(o.get("sales_name") or "").lower()
            order_is_s2 = "fatin" in sn or "nanda" in sn

        # Totals
        tot_rev += amount; tot_prof += profit; tot_txn += 1
        if tgl not in daily_tot:
            daily_tot[tgl] = {"tanggal": tgl, "revenue": 0.0, "profit": 0.0, "transaksi": 0}
        daily_tot[tgl]["revenue"] += amount
        daily_tot[tgl]["profit"]  += profit
        daily_tot[tgl]["transaksi"] += 1

        # Payment
        payment_map.setdefault(payment, {"payment_type": payment, "amount": 0.0})
        payment_map[payment]["amount"] += amount

        # Per store
        ds, hs = (daily_s2, hourly_s2) if order_is_s2 else (daily_s1, hourly_s1)
        if order_is_s2:
            s2_rev += amount; s2_prof += profit; s2_txn += 1
        else:
            s1_rev += amount; s1_prof += profit; s1_txn += 1

        ds.setdefault(tgl, {"tanggal": tgl, "revenue": 0.0, "profit": 0.0, "transaksi": 0})
        ds[tgl]["revenue"] += amount
        ds[tgl]["profit"]  += profit
        ds[tgl]["transaksi"] += 1
        if hour >= 0:
            hs[hour] = hs.get(hour, 0.0) + amount

        # Products
        for item in items:
            nama     = str(item.get("item_name") or item.get("product_name") or item.get("name") or "Unknown")
            qty      = float(item.get("qty") or item.get("quantity") or 0)
            i_amount = float(item.get("amount") or item.get("subtotal") or 0)
            i_profit = float(item.get("profit") or 0)
            display  = nama.replace(" 2nd Store", "").replace(" 2nd store", "").strip()
            produk_all.setdefault(display, {"item_name": display, "qty": 0.0, "revenue": 0.0, "profit": 0.0})
            produk_all[display]["qty"]     += qty
            produk_all[display]["revenue"] += i_amount
            produk_all[display]["profit"]  += i_profit

    # Build daily lists
    daily_list = sorted(daily_tot.values(), key=lambda x: x["tanggal"])

    # Hourly (08:00 - 22:00)
    def hourly(hm):
        return [{"hour": f"{h:02d}:00", "revenue": round(hm.get(h, 0), 0)} for h in range(8, 23)]

    # Top products
    top = sorted(produk_all.values(), key=lambda x: x["revenue"], reverse=True)[:10]

    # Product mix donut
    mix = {}
    for p in top:
        mix[p["item_name"]] = mix.get(p["item_name"], 0) + p["revenue"]
    mix_sorted = sorted(mix.items(), key=lambda x: x[1], reverse=True)
    top4 = mix_sorted[:4]
    others = sum(v for _, v in mix_sorted[4:])
    product_mix = [{"name": n, "revenue": round(r, 0),
                    "pct": round(r / tot_rev * 100, 1) if tot_rev else 0}
                   for n, r in top4]
    if others > 0:
        product_mix.append({"name": "Others", "revenue": round(others, 0),
                            "pct": round(others / tot_rev * 100, 1) if tot_rev else 0})

    gpm = lambda p, r: round(p / r * 100, 1) if r else 0

    return {
        "total_revenue":   round(tot_rev, 0),
        "total_profit":    round(tot_prof, 0),
        "total_transaksi": int(tot_txn),
        "basket_size":     round(tot_rev / tot_txn, 0) if tot_txn else 0,
        "gpm_total":       gpm(tot_prof, tot_rev),

        "store1": {"name": "Jl. Yos Sudarso IV", "revenue": round(s1_rev, 0),
                   "profit": round(s1_prof, 0), "transaksi": int(s1_txn), "gpm": gpm(s1_prof, s1_rev)},
        "store2": {"name": "Jl. Diponegoro", "revenue": round(s2_rev, 0),
                   "profit": round(s2_prof, 0), "transaksi": int(s2_txn), "gpm": gpm(s2_prof, s2_rev)},

        "daily_total":   [dict(d, revenue=round(d["revenue"],0), profit=round(d["profit"],0)) for d in daily_list],
        "daily_store1":  sorted(daily_s1.values(), key=lambda x: x["tanggal"]),
        "daily_store2":  sorted(daily_s2.values(), key=lambda x: x["tanggal"]),
        "hourly_store1": hourly(hourly_s1),
        "hourly_store2": hourly(hourly_s2),

        "product_mix":  product_mix,
        "top_products": [{"item_name": p["item_name"], "qty": round(p["qty"],0),
                          "revenue": round(p["revenue"],0), "profit": round(p["profit"],0),
                          "margin_pct": gpm(p["profit"], p["revenue"])} for p in top],
        "payment": sorted(payment_map.values(), key=lambda x: x["amount"], reverse=True)[:6],
        "days_count":    len(daily_list),
        "last_updated":  datetime.now().strftime("%d %b %Y, %H:%M WIB"),
    }

def _empty():
    base = {"revenue": 0, "profit": 0, "transaksi": 0, "gpm": 0}
    return {"total_revenue": 0, "total_profit": 0, "total_transaksi": 0,
            "basket_size": 0, "gpm_total": 0,
            "store1": dict(base, name="Jl. Yos Sudarso IV"),
            "store2": dict(base, name="Jl. Diponegoro"),
            "daily_total": [], "daily_store1": [], "daily_store2": [],
            "hourly_store1": [], "hourly_store2": [],
            "product_mix": [], "top_products": [], "payment": [],
            "days_count": 0, "last_updated": "-"}

# ── Growth % + AI Insights ────────────────────────────
def pct(curr, prev):
    return round((curr - prev) / abs(prev) * 100, 1) if prev else None

def build_response(curr, prev):
    if not curr:
        return {"status": "loading", "message": "Sedang mengambil data Olsera..."}
    result = dict(curr)
    if prev:
        result["growth"] = {
            "total_revenue":   pct(curr["total_revenue"],   prev["total_revenue"]),
            "total_transaksi": pct(curr["total_transaksi"], prev["total_transaksi"]),
            "basket_size":     pct(curr["basket_size"],     prev["basket_size"]),
            "store1_revenue":  pct(curr["store1"]["revenue"], prev["store1"]["revenue"]),
            "store2_revenue":  pct(curr["store2"]["revenue"], prev["store2"]["revenue"]),
            "store1_gpm_diff": round(curr["store1"]["gpm"] - prev["store1"]["gpm"], 1) if prev["store1"]["gpm"] else None,
            "store2_gpm_diff": round(curr["store2"]["gpm"] - prev["store2"]["gpm"], 1) if prev["store2"]["gpm"] else None,
        }
        result["ai_insights"] = make_insights(curr, result["growth"])
    else:
        result["growth"] = {}
        result["ai_insights"] = []
    return result

def make_insights(curr, growth):
    ins = []
    rev_g = growth.get("total_revenue")
    if rev_g is not None:
        if rev_g >= 0:
            best = max(curr["daily_total"], key=lambda d: d["revenue"], default={})
            ins.append({"type": "positive", "icon": "trending_up",
                        "title": f"Revenue up {rev_g}% this period",
                        "message": f"Total revenue grew {rev_g}% vs previous period" +
                                   (f", peak on {best.get('tanggal','')}" if best else "")})
        else:
            ins.append({"type": "warning", "icon": "trending_down",
                        "title": f"Revenue down {abs(rev_g)}%",
                        "message": f"Revenue declined {abs(rev_g)}% vs previous period."})

    if curr["top_products"]:
        top = curr["top_products"][0]
        pct_share = curr["product_mix"][0]["pct"] if curr["product_mix"] else 0
        ins.append({"type": "info", "icon": "star",
                    "title": f"Top product: {top['item_name']}",
                    "message": f"{top['item_name']} contributed {pct_share}% of revenue across both stores."})

    s1g = growth.get("store1_revenue"); s2g = growth.get("store2_revenue")
    if s1g is not None and s2g is not None and max(s1g, s2g) > 0:
        if s2g > s1g:
            ins.append({"type": "positive", "icon": "flash",
                        "title": "Store 2 (Diponegoro) accelerating",
                        "message": f"Jl. Diponegoro grew {s2g}% — fastest growth this period."})
        else:
            ins.append({"type": "positive", "icon": "flash",
                        "title": "Store 1 (Yos Sudarso) leading",
                        "message": f"Jl. Yos Sudarso grew {s1g}% this period."})

    for name, diff_key in [("Store 1", "store1_gpm_diff"), ("Store 2", "store2_gpm_diff")]:
        diff = growth.get(diff_key)
        if diff is not None and diff < -0.5:
            ins.append({"type": "alert", "icon": "warning",
                        "title": f"Margin compression — {name}",
                        "message": f"GPM dipped {abs(diff)}pp. Review COGS and procurement costs."})
            break
    return ins[:4]

# ── Refresh all data ──────────────────────────────────
def refresh_all():
    if _cache['fetching']: return
    _cache['fetching'] = True
    try:
        today = date.today()
        wday  = today.weekday()
        ws    = today - timedelta(days=wday)
        we    = today
        pws   = ws - timedelta(days=7)
        pwe   = ws - timedelta(days=1)
        ms    = today.replace(day=1)
        me    = today
        pms   = (ms - timedelta(days=1)).replace(day=1)
        pme   = ms - timedelta(days=1)

        print("[REFRESH] Fetching 4 periods...")
        cw = process_orders(fetch_orders(ws,  we))
        pw = process_orders(fetch_orders(pws, pwe))
        cm = process_orders(fetch_orders(ms,  me))
        pm = process_orders(fetch_orders(pms, pme))

        _cache['weekly']       = build_response(cw, pw)
        _cache['monthly']      = build_response(cm, pm)
        _cache['last_updated'] = datetime.now()

        print(f"[OK] Weekly: Rp {cw['total_revenue']:,.0f} | Monthly: Rp {cm['total_revenue']:,.0f}")
    except Exception as e:
        print(f"[ERROR] refresh_all: {e}")
    finally:
        _cache['fetching'] = False

def background_loop():
    while True:
        time.sleep(6 * 3600)
        refresh_all()

# ── Routes ────────────────────────────────────────────
@app.route('/api/dashboard')
def api_dashboard():
    period = request.args.get('period', 'weekly').lower()
    data   = _cache['monthly'] if period == 'monthly' else _cache['weekly']
    if not data:
        if not _cache['fetching']:
            threading.Thread(target=refresh_all, daemon=True).start()
        return jsonify({"status": "loading",
                        "message": "Mengambil data Olsera, coba lagi dalam 30 detik"}), 503
    return jsonify(data)

@app.route('/api/status')
def api_status():
    return jsonify({"status": "online",
                    "has_weekly": _cache['weekly'] is not None,
                    "has_monthly": _cache['monthly'] is not None,
                    "fetching": _cache['fetching'],
                    "config_ok": bool(APP_ID and SECRET_KEY),
                    "last_updated": str(_cache['last_updated']) if _cache['last_updated'] else None})

@app.route('/api/refresh')
def api_refresh():
    if not _cache['fetching']:
        threading.Thread(target=refresh_all, daemon=True).start()
    return jsonify({"status": "ok", "message": "Refresh dimulai (~30 detik)"})

@app.route('/')
def index():
    w = _cache['weekly'];  m = _cache['monthly']
    return jsonify({"service": "Keyos Dashboard — Olsera Middleware", "status": "online",
                    "endpoints": {"/api/dashboard?period=weekly": "weekly",
                                  "/api/dashboard?period=monthly": "monthly",
                                  "/api/refresh": "trigger refresh"},
                    "weekly_revenue":  f"Rp {w['total_revenue']:,.0f}" if w else "loading",
                    "monthly_revenue": f"Rp {m['total_revenue']:,.0f}" if m else "loading"})

if __name__ == '__main__':
    if APP_ID and SECRET_KEY:
        print("🚀 Keyos Middleware starting...")
        refresh_all()
        threading.Thread(target=background_loop, daemon=True).start()
    else:
        print("⚠️  Set OLSERA_APP_ID dan OLSERA_SECRET_KEY di Railway!")
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
