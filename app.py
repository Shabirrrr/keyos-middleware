"""
app.py — Olsera Middleware untuk Keyos Financial Dashboard v5
Strategi baru: gunakan Transaction List (inextrans) yang punya
start_trans_date/end_trans_date dan kemungkinan ada item + profit

Endpoints:
  GET /api/dashboard?period=weekly
  GET /api/dashboard?period=monthly
  GET /api/status
  GET /api/refresh
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

# ── Fetch Transaction List ────────────────────────────
def fetch_transactions(start_date, end_date):
    """
    Pakai endpoint Transaction (inextrans) yang punya
    start_trans_date/end_trans_date
    """
    token = get_token()
    if not token:
        return []

    all_trans, page = [], 1
    print(f"[TRANS] {start_date} → {end_date}")

    while True:
        headers = {
            "Accept":        "application/json",
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {token}"
        }
        params = {
            "start_trans_date": str(start_date),
            "end_trans_date":   str(end_date),
            "page":             page,
            "per_page":         100
        }
        try:
            r = requests.get(f"{BASE_URL_DATA}/transaction/inextrans",
                             headers=headers, params=params, timeout=30)
            if r.status_code == 401:
                token = _generate_token()
                if not token: break
                headers["Authorization"] = f"Bearer {token}"
                r = requests.get(f"{BASE_URL_DATA}/transaction/inextrans",
                                 headers=headers, params=params, timeout=30)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"[TRANS] Error p{page}: {e}")
            break

        # Debug sample pertama kali
        if page == 1:
            print(f"[TRANS DEBUG] Response type: {type(data)}")
            if isinstance(data, dict):
                print(f"[TRANS DEBUG] Top keys: {list(data.keys())[:10]}")
            if isinstance(data, list) and data:
                print(f"[TRANS DEBUG] Sample keys: {list(data[0].keys())}")
                print(f"[TRANS DEBUG] Sample: {data[0]}")
            elif isinstance(data, dict):
                for k in ['data', 'transactions', 'result', 'items']:
                    if k in data and isinstance(data[k], list) and data[k]:
                        sample = data[k][0]
                        print(f"[TRANS DEBUG] data['{k}'][0] keys: {list(sample.keys())}")
                        print(f"[TRANS DEBUG] data['{k}'][0]: {sample}")
                        # Cek apakah ada items/detail di dalam transaksi
                        for sub in ['items','details','order_items','products']:
                            if sub in sample and sample[sub]:
                                print(f"[TRANS DEBUG] Item keys: {list(sample[sub][0].keys())}")
                                print(f"[TRANS DEBUG] Item sample: {sample[sub][0]}")
                        break

        orders = []
        if isinstance(data, list):
            orders = data
        elif isinstance(data, dict):
            for k in ['data', 'transactions', 'result', 'items']:
                if k in data and isinstance(data[k], list):
                    orders = data[k]
                    break
        if not orders:
            print(f"[TRANS] No orders found in response")
            break

        all_trans.extend(orders)
        meta = data.get("meta", {}) if isinstance(data, dict) else {}
        last = int(meta.get("last_page") or meta.get("total_pages") or 1)
        if page >= last or len(orders) < 100:
            break
        page += 1

    print(f"[TRANS] Total: {len(all_trans)} transactions")
    return all_trans

# ── Fallback: Fetch Close Order List ─────────────────
def fetch_close_orders(start_date, end_date):
    """Fallback jika Transaction tidak tersedia"""
    token = get_token()
    if not token:
        return []

    all_orders, page = [], 1
    print(f"[CLOSE] {start_date} → {end_date}")

    while True:
        headers = {
            "Accept":        "application/json",
            "Content-Type":  "application/json",
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
            print(f"[CLOSE] Error p{page}: {e}")
            break

        orders = []
        if isinstance(data, list):
            orders = data
        elif isinstance(data, dict):
            for k in ['data', 'orders', 'result', 'items']:
                if k in data and isinstance(data[k], list):
                    orders = data[k]
                    break
        if not orders:
            break

        all_orders.extend(orders)
        meta = data.get("meta", {}) if isinstance(data, dict) else {}
        last = int(meta.get("last_page") or meta.get("total_pages") or 1)
        if page >= last or len(orders) < 100:
            break
        page += 1

    print(f"[CLOSE] {len(all_orders)} orders")
    return all_orders

# ── Helpers ───────────────────────────────────────────
def is_store2(item_name):
    return "2nd store" in str(item_name).lower()

def is_reseller(item_group):
    return str(item_group).strip().lower() == "reseller"

# ── Process transactions ──────────────────────────────
def process(orders):
    if not orders:
        return _empty()

    tot_rev = tot_prof = tot_txn = 0.0
    basket_rev = basket_txn = 0.0
    s1_rev = s1_prof = s1_txn = 0.0
    s2_rev = s2_prof = s2_txn = 0.0

    daily_tot = {}; daily_s1 = {}; daily_s2 = {}
    hourly_s1 = {}; hourly_s2 = {}
    produk_map = {}; payment_map = {}

    for o in orders:
        # Coba semua kemungkinan field name untuk tanggal
        tgl = str(
            o.get('order_date') or o.get('trans_date') or o.get('transaction_date') or
            o.get('created_at') or o.get('date') or ''
        )[:10]
        if not tgl or tgl < '2020-01-01':
            continue

        dt_str = str(o.get('modified_time') or o.get('order_date') or o.get('trans_date') or '')
        hour   = int(dt_str[11:13]) if len(dt_str) >= 13 else -1

        # Coba semua kemungkinan field name untuk amount
        amount = float(
            o.get('total_amount') or o.get('order_amount') or o.get('grand_total') or
            o.get('amount') or o.get('total') or 0
        )

        # Profit — coba berbagai field name
        profit = float(
            o.get('profit') or o.get('gross_profit') or o.get('net_profit') or
            o.get('laba') or 0
        )

        # Payment
        payment = str(
            o.get('payment_type_name') or o.get('payment_type') or
            o.get('payment_method') or 'Lainnya'
        ).strip()

        # Items — coba berbagai field name
        items = (
            o.get('items') or o.get('order_items') or o.get('details') or
            o.get('products') or o.get('transaction_items') or []
        )

        # Tentukan store dari items
        order_is_s2      = False
        order_is_reseller = False
        item_profit      = 0.0

        for item in items:
            iname  = str(item.get('item_name') or item.get('product_name') or item.get('name') or '')
            igroup = str(item.get('item_group') or item.get('category') or item.get('group') or '')
            iqty   = float(item.get('qty') or item.get('quantity') or 0)
            iamt   = float(item.get('amount') or item.get('subtotal') or item.get('total') or 0)
            iprof  = float(
                item.get('profit') or item.get('gross_profit') or
                item.get('laba') or 0
            )
            icost  = float(
                item.get('cost_price') or item.get('hpp') or item.get('cost') or
                item.get('cost_perunit') or item.get('total_cost') or 0
            )

            if is_store2(iname): order_is_s2 = True
            if is_reseller(igroup): order_is_reseller = True

            # Hitung profit dari cost jika profit field kosong
            if iprof == 0 and icost > 0 and iamt > 0:
                iprof = iamt - icost

            item_profit += iprof

            display = iname.replace(' 2nd Store', '').replace(' 2nd store', '').strip()
            if display:
                produk_map.setdefault(display, {'item_name': display, 'qty': 0.0, 'revenue': 0.0, 'profit': 0.0})
                produk_map[display]['qty']     += iqty
                produk_map[display]['revenue'] += iamt
                produk_map[display]['profit']  += iprof

        # Pakai profit dari items jika profit order = 0
        if profit == 0 and item_profit > 0:
            profit = item_profit

        # Totals
        tot_rev  += amount
        tot_prof += profit
        tot_txn  += 1

        # Basket size: exclude amount=0 dan Reseller
        if amount > 0 and not order_is_reseller:
            basket_rev += amount
            basket_txn += 1

        # Daily
        daily_tot.setdefault(tgl, {'tanggal': tgl, 'revenue': 0.0, 'profit': 0.0, 'transaksi': 0})
        daily_tot[tgl]['revenue']   += amount
        daily_tot[tgl]['profit']    += profit
        daily_tot[tgl]['transaksi'] += 1

        # Payment
        payment_map.setdefault(payment, {'payment_type': payment, 'amount': 0.0})
        payment_map[payment]['amount'] += amount

        # Store split
        if order_is_s2:
            s2_rev  += amount; s2_prof += profit; s2_txn += 1
            daily_s2.setdefault(tgl, {'tanggal': tgl, 'revenue': 0.0, 'profit': 0.0, 'transaksi': 0})
            daily_s2[tgl]['revenue']   += amount
            daily_s2[tgl]['profit']    += profit
            daily_s2[tgl]['transaksi'] += 1
            if hour >= 0: hourly_s2[hour] = hourly_s2.get(hour, 0.0) + amount
        else:
            s1_rev  += amount; s1_prof += profit; s1_txn += 1
            daily_s1.setdefault(tgl, {'tanggal': tgl, 'revenue': 0.0, 'profit': 0.0, 'transaksi': 0})
            daily_s1[tgl]['revenue']   += amount
            daily_s1[tgl]['profit']    += profit
            daily_s1[tgl]['transaksi'] += 1
            if hour >= 0: hourly_s1[hour] = hourly_s1.get(hour, 0.0) + amount

    # ── Outputs ───────────────────────────────────────
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

    basket_size = round(basket_rev / basket_txn, 0) if basket_txn else 0

    return {
        'total_revenue':   round(tot_rev, 0),
        'total_profit':    round(tot_prof, 0),
        'total_transaksi': int(tot_txn),
        'basket_size':     basket_size,
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
    if not curr:
        return {'status': 'loading'}
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
        if rev_g >= 0:
            ins.append({'type': 'positive', 'icon': 'trending_up',
                        'title': f'Revenue naik {rev_g}%',
                        'message': f'Total revenue tumbuh {rev_g}% dibanding periode sebelumnya.'})
        else:
            ins.append({'type': 'warning', 'icon': 'trending_down',
                        'title': f'Revenue turun {abs(rev_g)}%',
                        'message': f'Revenue turun {abs(rev_g)}% dibanding periode sebelumnya.'})

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
                        'message': f'GPM turun {abs(diff)}pp. Review COGS.'})
            break
    return ins[:4]

# ── Refresh ───────────────────────────────────────────
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

        print('[REFRESH] Fetching 4 periods via Transaction API...')

        # Coba Transaction endpoint dulu, fallback ke Close Order
        cw_raw = fetch_transactions(ws,  we)
        if not cw_raw:
            print('[REFRESH] Transaction empty, fallback to Close Order')
            cw_raw = fetch_close_orders(ws, we)

        pw_raw = fetch_transactions(pws, pwe)
        if not pw_raw: pw_raw = fetch_close_orders(pws, pwe)

        cm_raw = fetch_transactions(ms,  me)
        if not cm_raw: cm_raw = fetch_close_orders(ms, me)

        pm_raw = fetch_transactions(pms, pme)
        if not pm_raw: pm_raw = fetch_close_orders(pms, pme)

        cw = process(cw_raw); pw = process(pw_raw)
        cm = process(cm_raw); pm = process(pm_raw)

        _cache['weekly']       = build_response(cw, pw)
        _cache['monthly']      = build_response(cm, pm)
        _cache['last_updated'] = datetime.now()

        wr = _cache['weekly'].get('total_revenue', 0)
        mr = _cache['monthly'].get('total_revenue', 0)
        wb = _cache['weekly'].get('basket_size', 0)
        wg = _cache['weekly'].get('gpm_total', 0)
        print(f'[OK] Weekly: Rp {wr:,.0f} | Monthly: Rp {mr:,.0f} | Basket: Rp {wb:,.0f} | GPM: {wg}%')
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
        return jsonify({'status': 'loading', 'message': 'Mengambil data, coba lagi dalam 60 detik'}), 503
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
    })

@app.route('/api/refresh')
def api_refresh():
    if not _cache['fetching']:
        threading.Thread(target=refresh_all, daemon=True).start()
    return jsonify({'status': 'ok', 'message': 'Refresh dimulai (~60 detik)'})

@app.route('/')
def index():
    w = _cache['weekly']; m = _cache['monthly']
    return jsonify({
        'service': 'Keyos Dashboard — Olsera Middleware v5',
        'status':  'online',
        'weekly_revenue':  f"Rp {w['total_revenue']:,.0f}" if w else 'loading',
        'weekly_basket':   f"Rp {w['basket_size']:,.0f}"   if w else 'loading',
        'weekly_gpm':      f"{w['gpm_total']}%"             if w else 'loading',
        'monthly_revenue': f"Rp {m['total_revenue']:,.0f}" if m else 'loading',
    })

if __name__ == '__main__':
    if APP_ID and SECRET_KEY:
        print('🚀 Keyos Middleware v5 starting...')
        refresh_all()
        threading.Thread(target=background_loop, daemon=True).start()
    else:
        print('⚠️  Set OLSERA_APP_ID dan OLSERA_SECRET_KEY di Railway!')
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
