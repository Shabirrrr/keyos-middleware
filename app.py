"""
app.py — Keyos Dashboard Middleware v6
Strategi:
1. Fetch Close Order List → dapat daftar order + order_id
2. Fetch Close Order Detail per order (batch, max 100) → dapat items + cost
3. Hitung profit dari: amount_item - total_cost
4. Deteksi Reseller dari harga item:
   - Premium 30ml (price < 80000) = reseller
   - Basic 30ml (price < 65000) = reseller
   - item_group == 'Reseller' = reseller
5. Basket size exclude: amount=0 dan order Reseller
"""

import os, requests, threading, time
from datetime import datetime, timedelta, date
from flask import Flask, jsonify, request, Response

app = Flask(__name__)

APP_ID     = os.environ.get('OLSERA_APP_ID', '')
SECRET_KEY = os.environ.get('OLSERA_SECRET_KEY', '')
BASE_URL_TOKEN = "https://api-open.olsera.co.id/api/open-api/v1/id"
BASE_URL_DATA  = "https://api-open.olsera.co.id/api/open-api/v1/en"

_cache = {
    "weekly": None, "monthly": None,
    "token": None, "token_saved_at": None,
    "fetching": False, "last_updated": None,
}

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
        r = requests.post(f"{BASE_URL_TOKEN}/token",
            json={"app_id": APP_ID, "secret_key": SECRET_KEY, "grant_type": "secret_key"},
            timeout=30)
        r.raise_for_status()
        d  = r.json()
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

# ── Fetch Close Order List ────────────────────────────
def fetch_close_orders(start_date, end_date):
    token = get_token()
    if not token: return []

    all_orders, page = [], 1
    print(f"[LIST] {start_date} → {end_date}")

    while True:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}"
        }
        params = {
            "start_date": str(start_date),
            "end_date":   str(end_date),
            "page":       page,
            "per_page":   100
        }
        try:
            r = requests.get(f"{BASE_URL_DATA}/order/closeorder",
                             headers=headers, params=params, timeout=30)
            if r.status_code == 401:
                token = _generate_token()
                if not token: break
                headers["Authorization"] = f"Bearer {token}"
                r = requests.get(f"{BASE_URL_DATA}/order/closeorder",
                                 headers=headers, params=params, timeout=30)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"[LIST] Error p{page}: {e}")
            break

        orders = []
        if isinstance(data, list):
            orders = data
        elif isinstance(data, dict):
            for k in ['data','orders','result','items']:
                if k in data and isinstance(data[k], list):
                    orders = data[k]; break
        if not orders: break

        all_orders.extend(orders)
        meta = data.get("meta", {}) if isinstance(data, dict) else {}
        last = int(meta.get("last_page") or meta.get("total_pages") or 1)
        if page >= last or len(orders) < 100: break
        page += 1

    print(f"[LIST] {len(all_orders)} orders")
    return all_orders

# ── Fetch Close Order Detail ──────────────────────────
def fetch_order_detail(order_id, token, headers):
    """Ambil detail 1 order → items dengan cost"""
    try:
        r = requests.get(
            f"{BASE_URL_DATA}/order/closeorder/detail",
            headers=headers,
            params={"id": order_id},
            timeout=15
        )
        if r.status_code == 200:
            d = r.json()
            # Debug sample pertama
            if not hasattr(fetch_order_detail, '_debugged'):
                fetch_order_detail._debugged = True
                print(f"[DETAIL DEBUG] Keys: {list(d.keys()) if isinstance(d, dict) else type(d)}")
                detail = d.get('data', d) if isinstance(d, dict) else d
                if isinstance(detail, dict):
                    print(f"[DETAIL DEBUG] Detail keys: {list(detail.keys())}")
                    items = (detail.get('items') or detail.get('order_items') or
                             detail.get('details') or detail.get('products') or [])
                    if items:
                        print(f"[DETAIL DEBUG] Item keys: {list(items[0].keys())}")
                        print(f"[DETAIL DEBUG] Item sample: {items[0]}")
            return d.get('data', d) if isinstance(d, dict) else d
    except Exception as e:
        print(f"[DETAIL] Error {order_id}: {e}")
    return None

def fetch_all_details(orders):
    """
    Fetch detail untuk semua order.
    Pakai threading untuk mempercepat.
    """
    token = get_token()
    if not token: return {}

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}"
    }

    details = {}
    total = len(orders)
    print(f"[DETAIL] Fetching details for {total} orders...")

    # Fetch dengan delay kecil untuk hindari rate limit
    for i, o in enumerate(orders):
        oid = str(o.get('id') or o.get('order_id') or '')
        if not oid:
            continue

        detail = fetch_order_detail(oid, token, headers)
        if detail:
            details[oid] = detail

        # Log progress tiap 20 order
        if (i + 1) % 20 == 0:
            print(f"[DETAIL] Progress: {i+1}/{total}")

        time.sleep(0.1)  # 100ms delay antar request

    print(f"[DETAIL] Done: {len(details)}/{total} fetched")
    return details

# ── Deteksi Reseller dari harga item ──────────────────
def detect_reseller_from_price(items, order_date=None):
    """
    Reseller = item dijual di bawah harga normal:
    - Premium 30ml: harga < Rp 80.000 (berlaku semua waktu)
      → reseller dapat potongan, harga normal retail tetap 80.000
    - Basic 30ml: harga < Rp 65.000
    - item_group == 'Reseller'

    Catatan: walaupun ada kenaikan harga ke 89.000 sejak Feb 2026,
    reseller tetap di bawah 80.000 sehingga threshold tidak berubah.
    """
    for item in items:
        igroup = str(item.get('item_group') or item.get('category') or '').strip().lower()
        iname  = str(item.get('item_name') or item.get('product_name') or item.get('name') or '').lower()
        price  = float(item.get('price') or item.get('unit_price') or 0)

        # Jika price tidak ada, coba hitung dari amount / qty
        if price == 0:
            qty   = float(item.get('qty') or item.get('quantity') or 1)
            iamt  = float(item.get('amount') or item.get('subtotal') or 0)
            if qty > 0 and iamt > 0:
                price = iamt / qty

        # Cek item_group Reseller
        if igroup == 'reseller':
            return True

        # Premium 30ml reseller: harga < 80.000 (kapanpun)
        is_basic   = 'basic' in iname
        is_premium = not is_basic  # semua non-basic dianggap premium untuk filter ini

        if is_basic and 0 < price < 65000:
            return True

        if is_premium and 0 < price < 80000:
            return True

    return False

# ── Process orders ─────────────────────────────────────
def process(orders, details_map=None):
    if not orders: return _empty()

    tot_rev = tot_prof = tot_txn = 0.0
    basket_rev = basket_txn = 0.0
    s1_rev = s1_prof = s1_txn = 0.0
    s2_rev = s2_prof = s2_txn = 0.0

    daily_tot = {}; daily_s1 = {}; daily_s2 = {}
    hourly_s1 = {}; hourly_s2 = {}
    produk_map = {}; payment_map = {}

    for o in orders:
        tgl = str(
            o.get('order_date') or o.get('trans_date') or
            o.get('created_at') or ''
        )[:10]
        if not tgl or tgl < '2020-01-01': continue

        dt_str  = str(o.get('modified_time') or o.get('order_date') or '')
        hour    = int(dt_str[11:13]) if len(dt_str) >= 13 else -1
        amount  = float(o.get('total_amount') or o.get('order_amount') or 0)
        payment = str(o.get('payment_type_name') or o.get('payment_type') or 'Lainnya').strip()

        oid_str = str(o.get('id') or o.get('order_id') or '')

        # Ambil item dari detail jika tersedia
        detail  = details_map.get(oid_str) if details_map else None
        items   = []
        if detail:
            items = (detail.get('items') or detail.get('order_items') or
                     detail.get('details') or detail.get('products') or [])

        # Hitung profit dari items
        order_profit     = 0.0
        order_is_s2      = False
        order_is_reseller = False

        for item in items:
            iname   = str(item.get('item_name') or item.get('product_name') or item.get('name') or '')
            igroup  = str(item.get('item_group') or item.get('category') or '')
            iqty    = float(item.get('qty') or item.get('quantity') or 0)
            iamt    = float(item.get('amount') or item.get('subtotal') or 0)
            icost   = float(
                item.get('total_cost') or item.get('cost_perunit') or
                item.get('hpp') or item.get('cost') or 0
            )
            iprofit = float(item.get('profit') or 0)

            # Hitung profit jika tidak ada field profit
            if iprofit == 0 and icost > 0 and iamt > 0:
                iprofit = iamt - icost
            order_profit += iprofit

            # Cek store
            if '2nd store' in iname.lower(): order_is_s2 = True

            # Cek reseller dari group
            if igroup.strip().lower() == 'reseller': order_is_reseller = True

            # Product tracking
            display = iname.replace(' 2nd Store','').replace(' 2nd store','').strip()
            if display:
                produk_map.setdefault(display, {'item_name': display, 'qty': 0.0, 'revenue': 0.0, 'profit': 0.0})
                produk_map[display]['qty']     += iqty
                produk_map[display]['revenue'] += iamt
                produk_map[display]['profit']  += iprofit

        # Fallback deteksi reseller dari harga jika ada items
        if items and not order_is_reseller:
            order_is_reseller = detect_reseller_from_price(items, tgl)

        # Totals
        tot_rev  += amount
        tot_prof += order_profit
        tot_txn  += 1

        # Basket size: exclude amount=0 dan Reseller
        if amount > 0 and not order_is_reseller:
            basket_rev += amount
            basket_txn += 1

        # Daily
        daily_tot.setdefault(tgl, {'tanggal': tgl, 'revenue': 0.0, 'profit': 0.0, 'transaksi': 0})
        daily_tot[tgl]['revenue']   += amount
        daily_tot[tgl]['profit']    += order_profit
        daily_tot[tgl]['transaksi'] += 1

        # Payment
        payment_map.setdefault(payment, {'payment_type': payment, 'amount': 0.0})
        payment_map[payment]['amount'] += amount

        # Store split
        if order_is_s2:
            s2_rev += amount; s2_prof += order_profit; s2_txn += 1
            daily_s2.setdefault(tgl, {'tanggal': tgl, 'revenue': 0.0, 'profit': 0.0, 'transaksi': 0})
            daily_s2[tgl]['revenue'] += amount; daily_s2[tgl]['profit'] += order_profit; daily_s2[tgl]['transaksi'] += 1
            if hour >= 0: hourly_s2[hour] = hourly_s2.get(hour, 0.0) + amount
        else:
            s1_rev += amount; s1_prof += order_profit; s1_txn += 1
            daily_s1.setdefault(tgl, {'tanggal': tgl, 'revenue': 0.0, 'profit': 0.0, 'transaksi': 0})
            daily_s1[tgl]['revenue'] += amount; daily_s1[tgl]['profit'] += order_profit; daily_s1[tgl]['transaksi'] += 1
            if hour >= 0: hourly_s1[hour] = hourly_s1.get(hour, 0.0) + amount

    # ── Build outputs ──────────────────────────────────
    daily_list = sorted(daily_tot.values(), key=lambda x: x['tanggal'])
    gpm = lambda p, r: round(p / r * 100, 1) if r else 0

    def hourly(hm):
        return [{'hour': f'{h:02d}:00', 'revenue': round(hm.get(h, 0), 0)} for h in range(8, 23)]

    mix_sorted  = sorted(produk_map.values(), key=lambda x: x['revenue'], reverse=True)
    top4        = mix_sorted[:4]
    others_rev  = sum(v['revenue'] for v in mix_sorted[4:])
    product_mix = [
        {'name': p['item_name'], 'revenue': round(p['revenue'], 0),
         'pct': round(p['revenue'] / tot_rev * 100, 1) if tot_rev else 0}
        for p in top4
    ]
    if others_rev > 0:
        product_mix.append({
            'name': 'Others', 'revenue': round(others_rev, 0),
            'pct': round(others_rev / tot_rev * 100, 1) if tot_rev else 0
        })

    return {
        'total_revenue':   round(tot_rev, 0),
        'total_profit':    round(tot_prof, 0),
        'total_transaksi': int(tot_txn),
        'basket_size':     round(basket_rev / basket_txn, 0) if basket_txn else 0,
        'gpm_total':       gpm(tot_prof, tot_rev),

        'store1': {'name': 'Jl. Yos Sudarso IV', 'revenue': round(s1_rev, 0),
                   'profit': round(s1_prof, 0), 'transaksi': int(s1_txn), 'gpm': gpm(s1_prof, s1_rev)},
        'store2': {'name': 'Jl. Diponegoro',      'revenue': round(s2_rev, 0),
                   'profit': round(s2_prof, 0), 'transaksi': int(s2_txn), 'gpm': gpm(s2_prof, s2_rev)},

        'daily_total':   [dict(d, revenue=round(d['revenue'],0), profit=round(d['profit'],0)) for d in daily_list],
        'daily_store1':  sorted(daily_s1.values(), key=lambda x: x['tanggal']),
        'daily_store2':  sorted(daily_s2.values(), key=lambda x: x['tanggal']),
        'hourly_store1': hourly(hourly_s1),
        'hourly_store2': hourly(hourly_s2),

        'product_mix':  product_mix,
        'top_products': [
            {'item_name': p['item_name'], 'qty': round(p['qty'],0),
             'revenue': round(p['revenue'],0), 'profit': round(p['profit'],0),
             'margin_pct': gpm(p['profit'], p['revenue'])}
            for p in mix_sorted[:10]
        ],
        'payment':    sorted(payment_map.values(), key=lambda x: x['amount'], reverse=True)[:6],
        'days_count': len(daily_list),
        'last_updated': datetime.now().strftime('%d %b %Y, %H:%M WIB'),
    }

def _empty():
    base = {'revenue': 0, 'profit': 0, 'transaksi': 0, 'gpm': 0}
    return {
        'total_revenue': 0, 'total_profit': 0, 'total_transaksi': 0,
        'basket_size': 0, 'gpm_total': 0,
        'store1': dict(base, name='Jl. Yos Sudarso IV'),
        'store2': dict(base, name='Jl. Diponegoro'),
        'daily_total': [], 'daily_store1': [], 'daily_store2': [],
        'hourly_store1': [], 'hourly_store2': [],
        'product_mix': [], 'top_products': [], 'payment': [],
        'days_count': 0, 'last_updated': '-'
    }

# ── Growth & Insights ─────────────────────────────────
def pct(curr, prev):
    if not prev: return None
    return round((curr - prev) / abs(prev) * 100, 1)

def build_response(curr, prev):
    if not curr: return {'status': 'loading'}
    result = dict(curr)
    if prev and prev.get('total_revenue', 0) > 0:
        result['growth'] = {
            'total_revenue':   pct(curr['total_revenue'],   prev['total_revenue']),
            'total_transaksi': pct(curr['total_transaksi'], prev['total_transaksi']),
            'basket_size':     pct(curr['basket_size'],     prev['basket_size']),
            'store1_revenue':  pct(curr['store1']['revenue'], prev['store1']['revenue']),
            'store2_revenue':  pct(curr['store2']['revenue'], prev['store2']['revenue']),
            'gpm_s1_pp': round(curr['store1']['gpm'] - prev['store1']['gpm'], 1) if prev['store1']['gpm'] else None,
            'gpm_s2_pp': round(curr['store2']['gpm'] - prev['store2']['gpm'], 1) if prev['store2']['gpm'] else None,
        }
        result['ai_insights'] = make_insights(curr, result['growth'])
    else:
        result['growth'] = {}; result['ai_insights'] = []
    return result

def make_insights(curr, growth):
    ins = []
    rev_g = growth.get('total_revenue')
    if rev_g is not None:
        ins.append({'type': 'positive' if rev_g >= 0 else 'warning',
                    'icon': 'trending_up' if rev_g >= 0 else 'trending_down',
                    'title': f"Revenue {'naik' if rev_g >= 0 else 'turun'} {abs(rev_g)}%",
                    'message': f"Total revenue {'tumbuh' if rev_g >= 0 else 'turun'} {abs(rev_g)}% dibanding periode sebelumnya."})

    if curr.get('product_mix'):
        top = curr['product_mix'][0]
        ins.append({'type': 'info', 'icon': 'star',
                    'title': f"Top product: {top['name']}",
                    'message': f"{top['name']} berkontribusi {top['pct']}% dari total revenue."})

    s1g = growth.get('store1_revenue'); s2g = growth.get('store2_revenue')
    if s1g is not None and s2g is not None:
        if s2g > s1g and s2g > 0:
            ins.append({'type': 'positive', 'icon': 'flash',
                        'title': 'Store 2 tumbuh lebih cepat',
                        'message': f'Jl. Diponegoro +{s2g}% vs Store 1 +{s1g}%.'})
        elif s1g > 0:
            ins.append({'type': 'positive', 'icon': 'flash',
                        'title': 'Store 1 leading',
                        'message': f'Jl. Yos Sudarso tumbuh {s1g}% periode ini.'})

    for store_name, diff_key in [('Store 1','gpm_s1_pp'),('Store 2','gpm_s2_pp')]:
        diff = growth.get(diff_key)
        if diff is not None and diff < -0.5:
            ins.append({'type': 'alert', 'icon': 'warning',
                        'title': f'Margin compression — {store_name}',
                        'message': f'GPM turun {abs(diff)}pp. Review COGS dan harga beli.'})
            break
    return ins[:4]

# ── Refresh ───────────────────────────────────────────
def refresh_period(start_date, end_date, fetch_details=True):
    """Fetch list + detail untuk satu periode"""
    orders = fetch_close_orders(start_date, end_date)
    if not orders:
        return process([])

    details = {}
    if fetch_details:
        details = fetch_all_details(orders)

    return process(orders, details)

def refresh_all():
    if _cache['fetching']: return
    _cache['fetching'] = True
    try:
        today = date.today()
        wday  = today.weekday()
        ws  = today - timedelta(days=wday); we  = today
        pws = ws - timedelta(days=7);       pwe = ws - timedelta(days=1)
        ms  = today.replace(day=1);         me  = today
        pms = (ms - timedelta(days=1)).replace(day=1); pme = ms - timedelta(days=1)

        print('[REFRESH] Starting full refresh with Close Order Detail...')

        # Periode saat ini: fetch detail lengkap
        cw = refresh_period(ws,  we,  fetch_details=True)
        pw = refresh_period(pws, pwe, fetch_details=True)
        cm = refresh_period(ms,  me,  fetch_details=True)
        pm = refresh_period(pms, pme, fetch_details=True)

        _cache['weekly']       = build_response(cw, pw)
        _cache['monthly']      = build_response(cm, pm)
        _cache['last_updated'] = datetime.now()

        wr = _cache['weekly'].get('total_revenue', 0)
        mr = _cache['monthly'].get('total_revenue', 0)
        wb = _cache['weekly'].get('basket_size', 0)
        wg = _cache['weekly'].get('gpm_total', 0)
        ws2 = _cache['weekly'].get('store2', {}).get('revenue', 0)
        print(f'[OK] Weekly: Rp {wr:,.0f} | Monthly: Rp {mr:,.0f}')
        print(f'     Basket: Rp {wb:,.0f} | GPM: {wg}% | Store2: Rp {ws2:,.0f}')
    except Exception as e:
        print(f'[ERROR] {e}')
        import traceback; traceback.print_exc()
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
        return jsonify({'status': 'loading',
                        'message': 'Mengambil data, coba lagi dalam 2-3 menit'}), 503
    return jsonify(data)

@app.route('/api/status')
def api_status():
    return jsonify({
        'status':       'online',
        'has_weekly':   _cache['weekly']  is not None,
        'has_monthly':  _cache['monthly'] is not None,
        'fetching':     _cache['fetching'],
        'config_ok':    bool(APP_ID and SECRET_KEY),
        'last_updated': str(_cache['last_updated']) if _cache['last_updated'] else None,
        'basket_size':  _cache['weekly'].get('basket_size') if _cache['weekly'] else None,
        'gpm_total':    _cache['weekly'].get('gpm_total')   if _cache['weekly'] else None,
        'store2_revenue': _cache['weekly'].get('store2', {}).get('revenue') if _cache['weekly'] else None,
    })

@app.route('/api/refresh')
def api_refresh():
    if not _cache['fetching']:
        threading.Thread(target=refresh_all, daemon=True).start()
    return jsonify({'status': 'ok', 'message': 'Refresh dimulai (~2-3 menit karena fetch detail)'})

@app.route('/')
def index():
    w = _cache['weekly']; m = _cache['monthly']
    return jsonify({
        'service': 'Keyos Dashboard — Olsera Middleware v6',
        'status':  'online',
        'weekly_revenue':  f"Rp {w['total_revenue']:,.0f}" if w else 'loading',
        'weekly_basket':   f"Rp {w['basket_size']:,.0f}"   if w else 'loading',
        'weekly_gpm':      f"{w['gpm_total']}%"             if w else 'loading',
        'store2_revenue':  f"Rp {w['store2']['revenue']:,.0f}" if w else 'loading',
        'monthly_revenue': f"Rp {m['total_revenue']:,.0f}" if m else 'loading',
    })

if __name__ == '__main__':
    if APP_ID and SECRET_KEY:
        print('🚀 Keyos Middleware v6 starting...')
        refresh_all()
        threading.Thread(target=background_loop, daemon=True).start()
    else:
        print('⚠️  Set OLSERA_APP_ID dan OLSERA_SECRET_KEY di Railway!')
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
