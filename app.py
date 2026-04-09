"""
app.py — Keyos Dashboard Middleware v7
Field mapping CONFIRMED dari API Olsera (hasil browser test):

Close Order List:
  order_date, total_amount, payment_type_name, id

Close Order Detail:
  data.orderitems[] → {product_name, price, qty, amount, cost_price, cost_amount}
  (NO product_group_name di level item!)

Report Product Sales By SKU:
  product_name, product_group_name ("2nd Store" = Store 2),
  total_amount, total_profit, total_qty

Strategi:
1. Close Order List → revenue, transaksi harian, hourly, payment
2. Report SKU (from/to) → profit per produk, product mix, store split by group
3. Close Order Detail (sample) → deteksi reseller via price threshold
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

def _headers():
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {get_token()}"
    }

# ── 1. Fetch Close Order List ─────────────────────────
def fetch_close_orders(start_date, end_date):
    all_orders, page = [], 1
    print(f"[LIST] {start_date} → {end_date}")
    while True:
        try:
            r = requests.get(f"{BASE_URL_DATA}/order/closeorder",
                headers=_headers(),
                params={"start_date": str(start_date), "end_date": str(end_date),
                        "page": page, "per_page": 100},
                timeout=30)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"[LIST] Error p{page}: {e}"); break

        orders = data if isinstance(data, list) else next(
            (data[k] for k in ['data','orders','result','items'] if isinstance(data.get(k), list)), [])
        if not orders: break
        all_orders.extend(orders)

        meta = data.get("meta", {}) if isinstance(data, dict) else {}
        last = int(meta.get("last_page") or meta.get("total_pages") or 1)
        if page >= last or len(orders) < 100: break
        page += 1

    print(f"[LIST] {len(all_orders)} orders")
    return all_orders

# ── 2. Fetch Report SKU (profit + product mix) ────────
def fetch_report_sku(start_date, end_date):
    """
    Endpoint: /report/productsalesbysku
    Params: from, to, per_page, page
    Returns: product_name, product_group_name, total_amount, total_profit, total_qty
    """
    all_items, page = [], 1
    print(f"[SKU] {start_date} → {end_date}")
    while True:
        try:
            r = requests.get(f"{BASE_URL_DATA}/report/productsalesbysku",
                headers=_headers(),
                params={"from": str(start_date), "to": str(end_date),
                        "page": page, "per_page": 100},
                timeout=30)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"[SKU] Error p{page}: {e}"); break

        items = data.get('data', []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        if not items: break
        all_items.extend(items)

        meta = data.get("meta", {}) if isinstance(data, dict) else {}
        last = int(meta.get("last_page") or meta.get("total_pages") or 1)
        if page >= last or len(items) < 100: break
        page += 1

    print(f"[SKU] {len(all_items)} produk")
    return all_items

# ── 3. Fetch Close Order Detail (untuk reseller check) ─
def fetch_order_detail(order_id):
    try:
        r = requests.get(f"{BASE_URL_DATA}/order/closeorder/detail",
            headers=_headers(), params={"id": order_id}, timeout=15)
        if r.status_code == 200:
            d = r.json()
            return d.get('data', d)
    except Exception as e:
        print(f"[DETAIL] Error {order_id}: {e}")
    return None

def is_reseller_order(order_id):
    """
    Ambil detail order, cek apakah ada item dengan harga di bawah threshold reseller:
    - harga < 80.000 = reseller (semua produk, kapanpun)
    - kecuali Basic 30ml yang memang 65.000
    """
    detail = fetch_order_detail(order_id)
    if not detail:
        return False
    items = detail.get('orderitems', [])
    for item in items:
        price = float(item.get('price') or 0)
        iname = str(item.get('product_name') or '').lower()
        is_basic = 'basic' in iname
        if is_basic and 0 < price < 65000:
            return True
        if not is_basic and 0 < price < 80000:
            return True
    return False

# ── Process data ──────────────────────────────────────
def process(orders, sku_items):
    if not orders:
        return _empty()

    # ── Build store split dari SKU report ─────────────
    # product_group_name == "2nd Store" → Store 2
    s2_products = set()
    sku_revenue = {}    # product_name → {revenue, profit, qty, is_s2}
    tot_sku_rev  = 0.0
    tot_sku_prof = 0.0
    s1_rev_sku = s1_prof_sku = 0.0
    s2_rev_sku = s2_prof_sku = 0.0

    for item in sku_items:
        pname  = str(item.get('product_name') or 'Unknown')
        pgroup = str(item.get('product_group_name') or '')
        irev   = float(item.get('total_amount') or 0)
        iprof  = float(item.get('total_profit') or 0)
        iqty   = float(item.get('total_qty') or 0)
        is_s2  = pgroup.strip().lower() == '2nd store' or '2nd store' in pname.lower()

        # Nama display bersih
        display = pname.replace(' 2nd Store', '').replace(' 2nd store', '').strip()

        sku_revenue.setdefault(display, {'revenue': 0.0, 'profit': 0.0, 'qty': 0.0, 'is_s2': is_s2})
        sku_revenue[display]['revenue'] += irev
        sku_revenue[display]['profit']  += iprof
        sku_revenue[display]['qty']     += iqty

        tot_sku_rev  += irev
        tot_sku_prof += iprof
        if is_s2:
            s2_rev_sku  += irev
            s2_prof_sku += iprof
            s2_products.add(display)
        else:
            s1_rev_sku  += irev
            s1_prof_sku += iprof

    # ── Process order list untuk transaksi, daily, hourly ─
    tot_rev = tot_txn = 0.0
    basket_rev = basket_txn = 0.0
    s1_txn = s2_txn = 0.0
    daily_tot = {}; daily_s1 = {}; daily_s2 = {}
    hourly_s1 = {}; hourly_s2 = {}
    payment_map = {}

    # Batch check reseller: sample 20 order terkecil (kemungkinan reseller)
    sorted_small = sorted(orders, key=lambda x: float(x.get('total_amount') or 0))[:20]
    reseller_ids = set()
    for o in sorted_small:
        oid = str(o.get('id') or '')
        amt = float(o.get('total_amount') or 0)
        # Hanya cek detail untuk order dengan amount rendah (kemungkinan reseller)
        if 0 < amt < 80000 and oid:
            if is_reseller_order(oid):
                reseller_ids.add(oid)
            time.sleep(0.05)

    print(f"[RESELLER] Detected {len(reseller_ids)} reseller orders from sample")

    for o in orders:
        tgl = str(o.get('order_date') or o.get('created_at') or '')[:10]
        if not tgl or tgl < '2020-01-01': continue

        dt_str  = str(o.get('order_time') or o.get('modified_time') or o.get('order_date') or '')
        hour    = int(dt_str[11:13]) if len(dt_str) >= 13 else -1
        amount  = float(o.get('total_amount') or o.get('order_amount') or 0)
        payment = str(o.get('payment_type_name') or o.get('payment_type') or 'Lainnya').strip()
        oid     = str(o.get('id') or '')

        # Tentukan store: gunakan rasio dari SKU report
        # Jika SKU data tersedia, s2 adalah proporsi s2_rev/tot dari SKU
        # Untuk transaksi individual: heuristik amount, untuk sekarang default s1
        # (store split revenue lebih akurat dari SKU report)
        is_reseller = oid in reseller_ids

        tot_rev += amount
        tot_txn += 1

        if amount > 0 and not is_reseller:
            basket_rev += amount
            basket_txn += 1

        # Daily total
        daily_tot.setdefault(tgl, {'tanggal': tgl, 'revenue': 0.0, 'profit': 0.0, 'transaksi': 0})
        daily_tot[tgl]['revenue']   += amount
        daily_tot[tgl]['transaksi'] += 1

        # Payment
        payment_map.setdefault(payment, {'payment_type': payment, 'amount': 0.0})
        payment_map[payment]['amount'] += amount

        # Hourly — split proportional berdasarkan ratio SKU
        s2_ratio = s2_rev_sku / tot_sku_rev if tot_sku_rev > 0 else 0
        if hour >= 0:
            hourly_s1[hour] = hourly_s1.get(hour, 0.0) + amount * (1 - s2_ratio)
            hourly_s2[hour] = hourly_s2.get(hour, 0.0) + amount * s2_ratio

    # ── Revenue per store dari SKU report (lebih akurat) ─
    # Scale ke total revenue order jika SKU tidak cover semua
    scale = tot_rev / tot_sku_rev if tot_sku_rev > 0 else 1

    # ── Daily store split (proportional) ─────────────
    s2_ratio_daily = s2_rev_sku / tot_sku_rev if tot_sku_rev > 0 else 0
    for tgl, d in daily_tot.items():
        s1_rev_d = d['revenue'] * (1 - s2_ratio_daily)
        s2_rev_d = d['revenue'] * s2_ratio_daily
        daily_s1[tgl] = {'tanggal': tgl, 'revenue': round(s1_rev_d, 0), 'transaksi': 0}
        daily_s2[tgl] = {'tanggal': tgl, 'revenue': round(s2_rev_d, 0), 'transaksi': 0}

    # ── Product mix ────────────────────────────────────
    mix_sorted  = sorted(sku_revenue.values(), key=lambda x: x['revenue'], reverse=True)
    top4        = sorted(sku_revenue.items(), key=lambda x: x[1]['revenue'], reverse=True)[:4]
    others_rev  = sum(v['revenue'] for k, v in sorted(sku_revenue.items(),
                      key=lambda x: x[1]['revenue'], reverse=True)[4:])
    product_mix = [
        {'name': k, 'revenue': round(v['revenue'], 0),
         'pct': round(v['revenue'] / tot_sku_rev * 100, 1) if tot_sku_rev else 0}
        for k, v in top4
    ]
    if others_rev > 0:
        product_mix.append({'name': 'Others', 'revenue': round(others_rev, 0),
                            'pct': round(others_rev / tot_sku_rev * 100, 1) if tot_sku_rev else 0})

    gpm = lambda p, r: round(p / r * 100, 1) if r else 0

    def hourly_list(hm):
        return [{'hour': f'{h:02d}:00', 'revenue': round(hm.get(h, 0), 0)} for h in range(8, 23)]

    basket_size = round(basket_rev / basket_txn, 0) if basket_txn else 0

    # Revenue store menggunakan SKU report (paling akurat) di-scale ke total order
    s1_rev_final  = s1_rev_sku * scale
    s2_rev_final  = s2_rev_sku * scale
    s1_prof_final = s1_prof_sku * scale
    s2_prof_final = s2_prof_sku * scale

    return {
        'total_revenue':   round(tot_rev, 0),
        'total_profit':    round(tot_sku_prof * scale, 0),
        'total_transaksi': int(tot_txn),
        'basket_size':     basket_size,
        'gpm_total':       gpm(tot_sku_prof, tot_sku_rev),

        'store1': {'name': 'Jl. Yos Sudarso IV',
                   'revenue': round(s1_rev_final, 0), 'profit': round(s1_prof_final, 0),
                   'transaksi': int(tot_txn - int(s2_txn)), 'gpm': gpm(s1_prof_sku, s1_rev_sku)},
        'store2': {'name': 'Jl. Diponegoro',
                   'revenue': round(s2_rev_final, 0), 'profit': round(s2_prof_final, 0),
                   'transaksi': int(s2_txn), 'gpm': gpm(s2_prof_sku, s2_rev_sku)},

        'daily_total':   sorted([dict(d, revenue=round(d['revenue'],0)) for d in daily_tot.values()],
                                key=lambda x: x['tanggal']),
        'daily_store1':  sorted(daily_s1.values(), key=lambda x: x['tanggal']),
        'daily_store2':  sorted(daily_s2.values(), key=lambda x: x['tanggal']),
        'hourly_store1': hourly_list(hourly_s1),
        'hourly_store2': hourly_list(hourly_s2),

        'product_mix':  product_mix,
        'top_products': [
            {'item_name': k, 'qty': round(v['qty'],0), 'revenue': round(v['revenue'],0),
             'profit': round(v['profit'],0), 'margin_pct': gpm(v['profit'], v['revenue']),
             'store': '2' if v['is_s2'] else '1'}
            for k, v in sorted(sku_revenue.items(), key=lambda x: x[1]['revenue'], reverse=True)[:10]
        ],
        'payment':    sorted(payment_map.values(), key=lambda x: x['amount'], reverse=True)[:6],
        'days_count': len(daily_tot),
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
                    'message': f"Total revenue {'tumbuh' if rev_g >= 0 else 'turun'} {abs(rev_g)}% vs periode sebelumnya."})
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
    gpm_diff = growth.get('gpm_s1_pp') or growth.get('gpm_s2_pp')
    if gpm_diff is not None and gpm_diff < -0.5:
        ins.append({'type': 'alert', 'icon': 'warning',
                    'title': 'Margin compression terdeteksi',
                    'message': f'GPM turun {abs(gpm_diff)}pp. Review COGS dan harga beli.'})
    return ins[:4]

# ── Refresh ───────────────────────────────────────────
def refresh_period(start_date, end_date):
    orders   = fetch_close_orders(start_date, end_date)
    sku      = fetch_report_sku(start_date, end_date)
    return process(orders, sku)

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

        print('[REFRESH] Starting v7 refresh...')
        cw = refresh_period(ws,  we)
        pw = refresh_period(pws, pwe)
        cm = refresh_period(ms,  me)
        pm = refresh_period(pms, pme)

        _cache['weekly']       = build_response(cw, pw)
        _cache['monthly']      = build_response(cm, pm)
        _cache['last_updated'] = datetime.now()

        wr  = _cache['weekly'].get('total_revenue', 0)
        mr  = _cache['monthly'].get('total_revenue', 0)
        wb  = _cache['weekly'].get('basket_size', 0)
        wg  = _cache['weekly'].get('gpm_total', 0)
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
        return jsonify({'status': 'loading', 'message': 'Mengambil data, coba lagi dalam 60 detik'}), 503
    return jsonify(data)

@app.route('/api/status')
def api_status():
    return jsonify({
        'status':         'online',
        'has_weekly':     _cache['weekly']  is not None,
        'has_monthly':    _cache['monthly'] is not None,
        'fetching':       _cache['fetching'],
        'config_ok':      bool(APP_ID and SECRET_KEY),
        'last_updated':   str(_cache['last_updated']) if _cache['last_updated'] else None,
        'basket_size':    _cache['weekly'].get('basket_size')             if _cache['weekly'] else None,
        'gpm_total':      _cache['weekly'].get('gpm_total')               if _cache['weekly'] else None,
        'store2_revenue': _cache['weekly'].get('store2', {}).get('revenue') if _cache['weekly'] else None,
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
        'service': 'Keyos Dashboard — Olsera Middleware v7',
        'status':  'online',
        'weekly_revenue':  f"Rp {w['total_revenue']:,.0f}" if w else 'loading',
        'weekly_basket':   f"Rp {w['basket_size']:,.0f}"   if w else 'loading',
        'weekly_gpm':      f"{w['gpm_total']}%"             if w else 'loading',
        'store2_revenue':  f"Rp {w['store2']['revenue']:,.0f}" if w else 'loading',
        'monthly_revenue': f"Rp {m['total_revenue']:,.0f}" if m else 'loading',
    })

if __name__ == '__main__':
    if APP_ID and SECRET_KEY:
        print('🚀 Keyos Middleware v7 starting...')
        refresh_all()
        threading.Thread(target=background_loop, daemon=True).start()
    else:
        print('⚠️  Set OLSERA_APP_ID dan OLSERA_SECRET_KEY di Railway!')
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
