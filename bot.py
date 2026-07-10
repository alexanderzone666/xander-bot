import os
import io
import json
import random
import argparse
import datetime as dt
from io import BytesIO
from pathlib import Path

import requests
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

import anthropic
import tweepy

import config

for _k in ['ANTHROPIC_API_KEY', 'X_API_KEY', 'X_API_SECRET', 'X_ACCESS_TOKEN', 'X_ACCESS_SECRET', 'GOLD_API_KEY']:
    setattr(config, _k, str(getattr(config, _k, '')).strip())

HERE = Path(__file__).parent
PHOTOS_DIR = HERE / 'photos'
PERF_LOG = HERE / 'performance_log.json'


def get_bitcoin_data(days=30):
    url = 'https://api.coingecko.com/api/v3/coins/bitcoin/ohlc'
    r = requests.get(url, params={'vs_currency': 'usd', 'days': days}, timeout=30)
    r.raise_for_status()
    rows = r.json()
    df = pd.DataFrame(rows, columns=['ts', 'open', 'high', 'low', 'close'])
    df['date'] = pd.to_datetime(df['ts'], unit='ms')
    return df[['date', 'open', 'high', 'low', 'close']]


def _gold_spot():
    url = 'https://www.goldapi.io/api/XAU/USD'
    h = {'x-access-token': config.GOLD_API_KEY, 'Content-Type': 'application/json'}
    r = requests.get(url, headers=h, timeout=30)
    r.raise_for_status()
    j = r.json()
    return {
        'price': float(j['price']),
        'open': float(j.get('open_price', j['price'])),
        'high': float(j.get('high_price', j['price'])),
        'low': float(j.get('low_price', j['price'])),
        'prev_close': float(j.get('prev_close_price', j['price'])),
    }


def get_gold_data(days=30):
    rows = []
    today = dt.date.today()
    h = {'x-access-token': config.GOLD_API_KEY}
    for i in range(days, 0, -1):
        d = today - dt.timedelta(days=i)
        try:
            u = f"https://www.goldapi.io/api/XAU/USD/{d.strftime('%Y%m%d')}"
            r = requests.get(u, headers=h, timeout=20)
            if r.status_code != 200:
                continue
            j = r.json()
            p = j.get('price')
            if p:
                rows.append({
                    'date': pd.Timestamp(d),
                    'open': float(j.get('open_price', p)),
                    'high': float(j.get('high_price', p)),
                    'low': float(j.get('low_price', p)),
                    'close': float(p),
                })
        except Exception:
            continue
    spot = _gold_spot()
    rows.append({'date': pd.Timestamp(today), 'open': spot['open'], 'high': spot['high'], 'low': spot['low'], 'close': spot['price']})
    if len(rows) < 2:
        p = spot['price']
        rows = [{'date': pd.Timestamp(today - dt.timedelta(days=i)), 'open': p, 'high': p, 'low': p, 'close': p} for i in range(days, -1, -1)]
    return pd.DataFrame(rows)


def summarize(df, asset):
    latest = float(df['close'].iloc[-1])
    week_ago = float(df['close'].iloc[-6]) if len(df) > 6 else float(df['close'].iloc[0])
    month_ago = float(df['close'].iloc[0])
    prev = float(df['close'].iloc[-2]) if len(df) > 1 else latest
    return {
        'asset': asset,
        'latest': round(latest, 2),
        'change_1d_pct': round((latest - prev) / prev * 100, 2),
        'change_7d_pct': round((latest - week_ago) / week_ago * 100, 2),
        'change_30d_pct': round((latest - month_ago) / month_ago * 100, 2),
        'high_30d': round(float(df['high'].max()), 2),
        'low_30d': round(float(df['low'].min()), 2),
    }


def round_levels(price):
    step = 100 if price >= 1000 else (10 if price >= 100 else 1)
    below = (int(price) // step) * step
    return below, below + step


def make_chart(df, stats):
    d = df.tail(30).copy().reset_index(drop=True)
    fig, ax = plt.subplots(figsize=(10, 5.6), dpi=160)
    fig.patch.set_facecolor('#0d0d0f')
    ax.set_facecolor('#0d0d0f')
    x = mdates.date2num(d['date'].dt.to_pydatetime())
    w = (x[1] - x[0]) * 0.6 if len(x) > 1 else 0.6
    for xi, o, h, l, c in zip(x, d['open'], d['high'], d['low'], d['close']):
        up = c >= o
        col = '#22c55e' if up else '#ef4444'
        ax.plot([xi, xi], [l, h], color=col, linewidth=1.0, zorder=2)
        lo, hi = (o, c) if up else (c, o)
        ax.add_patch(plt.Rectangle((xi - w / 2, lo), w, max(hi - lo, 0.01), facecolor=col, edgecolor=col, zorder=3))
    ax.axhline(stats['high_30d'], color='#9ca3af', ls='--', lw=0.8, alpha=0.5)
    ax.axhline(stats['low_30d'], color='#9ca3af', ls='--', lw=0.8, alpha=0.5)
    below, above = round_levels(stats['latest'])
    for lv in (below, above):
        ax.axhline(lv, color='#3b82f6', ls=':', lw=0.8, alpha=0.5)
    sign = '+' if stats['change_30d_pct'] >= 0 else ''
    ax.set_title(f"{stats['asset']}  -  ${stats['latest']:,.2f}  ({sign}{stats['change_30d_pct']}% / 30d)", color='white', fontsize=15, fontweight='bold', loc='left', pad=14)
    ax.text(1.0, 1.02, config.CHART_WATERMARK, transform=ax.transAxes, color='#9ca3af', fontsize=11, fontweight='bold', ha='right')
    ax.tick_params(colors='#9ca3af', labelsize=9)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.grid(color='#1f2937', lw=0.4, alpha=0.5)
    ax.xaxis_date()
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%d %b'))
    buf = BytesIO()
    plt.tight_layout()
    plt.savefig(buf, format='png', facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf


def make_quote_card(hook):
    import textwrap
    fig, ax = plt.subplots(figsize=(10, 5.6), dpi=160)
    fig.patch.set_facecolor('#0d0d0f')
    ax.set_facecolor('#0d0d0f')
    ax.axis('off')
    ax.text(0.5, 0.55, '\n'.join(textwrap.wrap(hook, width=34)), ha='center', va='center', color='white', fontsize=22, fontweight='bold', linespacing=1.6)
    ax.text(0.5, 0.08, config.CHART_WATERMARK, ha='center', color='#9ca3af', fontsize=12, fontweight='bold')
    ax.plot([0.42, 0.58], [0.22, 0.22], color='#22c55e', lw=2, transform=ax.transAxes)
    buf = BytesIO()
    plt.tight_layout()
    plt.savefig(buf, format='png', facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf


def pick_story_image(hook):
    if PHOTOS_DIR.exists():
        ph = [p for p in PHOTOS_DIR.iterdir() if p.suffix.lower() in ('.jpg', '.jpeg', '.png')]
        if ph:
            return open(ph[dt.date.today().toordinal() % len(ph)], 'rb')
    return make_quote_card(hook)


claude = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

PERSONA = (
    'You write X posts for Alexander (@xanderzone), a trader and entrepreneur. '
    'Voice: confident, direct, a little provocative, reflective. Hooks hard in line one. '
    'Style: short punchy lines, line breaks between thoughts, no hashtags, max 1 emoji. '
    'HARD RULES: Everything is HIS PERSONAL VIEW (my read, the way I see it, I am watching). '
    'NEVER give advice, never buy/sell/you should/get in/dont miss. '
    'NEVER state predictions as fact; use scenarios (if X holds, I am watching Y). '
    'No fake claims about results or wealth.'
)

TONE = (
    'Example tone for hot-takes: This might sound crazy, but there are guys that spent all year '
    'telling you the four year cycle for Bitcoin was dumb, and when they were proven wrong, they did not '
    'admit it, they just kept saying the market is wrong, and they are right. '
    'Calling out narratives, ego, crowd psychology with a knowing smirk.'
)


def _ask(prompt, use_search=False):
    kwargs = dict(model='claude-sonnet-4-6', max_tokens=800, system=PERSONA, messages=[{'role': 'user', 'content': prompt}])
    if use_search:
        kwargs['tools'] = [{'type': 'web_search_20250305', 'name': 'web_search'}]
    m = claude.messages.create(**kwargs)
    txt = '\n'.join([b.text for b in m.content if getattr(b, 'type', '') == 'text']).strip()
    return txt.strip('"')


def gen_market_post(s):
    below, above = round_levels(s['latest'])
    return _ask(f"Live {s['asset']} spot. Price now ${s['latest']:,.2f}, 7d {s['change_7d_pct']}%, 30d {s['change_30d_pct']}%. 30d high ${s['high_30d']:,.0f} low ${s['low_30d']:,.0f}. Round levels ${below:,.0f} below, ${above:,.0f} above. Write ONE X post: hooking personal read, reference the real price and one round level, scenario thinking only, under 260 chars, end EXACTLY: Not financial advice. Return ONLY the post text.")


def gen_weekly_recap(s):
    return _ask(f"Sunday recap for {s['asset']} live spot. Now ${s['latest']:,.2f}, 7d {s['change_7d_pct']}%, 30d high ${s['high_30d']:,.0f} low ${s['low_30d']:,.0f}. Write ONE X post recapping the weeks move and key level tested/held, scenario for week ahead, under 270 chars, end EXACTLY: Not financial advice. Return ONLY the post text.")


def gen_thread(s):
    below, above = round_levels(s['latest'])
    raw = _ask(f"{s['asset']} just moved {s['change_1d_pct']}% today to ${s['latest']:,.2f}. 30d high ${s['high_30d']:,.0f} low ${s['low_30d']:,.0f}. Round levels ${below:,.0f}/${above:,.0f}. Write a 3-tweet X THREAD breaking down the move (personal read, scenarios, what you are watching). Separate the 3 tweets with a line containing only three dashes. Each tweet under 270 chars. Last tweet ends EXACTLY: Not financial advice. Return ONLY the tweets.")
    parts = [p.strip() for p in raw.split('---') if p.strip()]
    return parts[:3] if parts else [raw]


def gen_story(theme):
    return _ask(f'{TONE} Write ONE X post in that tone on: {theme}. Strong hook line 1. No price calls, no advice. Under 270 chars. Return ONLY the post.')


def gen_motivation(theme):
    return _ask(f'Write ONE X post: motivational about markets, money, discipline, the builders journey. Theme: {theme}. Warm but strong, story-flavored. No advice. Under 270 chars. Return ONLY the post.')


def gen_trending():
    return _ask('Search the web for what is trending in trading, gold, bitcoin, or macro markets in the last 24 hours. Pick the single most talked-about topic. Then write ONE classy, hooking X post giving MY personal take on it in the persona voice - opinionated but elegant, crowd-psychology angle welcome. Reference the topic clearly so readers know what I am talking about. No links, no hashtags. Under 270 chars. If the take involves markets, end EXACTLY: Not financial advice. Return ONLY the post text.', use_search=True)


def log_levels(s):
    try:
        data = json.loads(PERF_LOG.read_text()) if PERF_LOG.exists() else []
    except Exception:
        data = []
    below, above = round_levels(s['latest'])
    data.append({'date': dt.date.today().isoformat(), 'asset': s['asset'], 'price': s['latest'], 'watch_below': below, 'watch_above': above, 'resolved': None})
    for e in data:
        if e['resolved'] is None and e['asset'] == s['asset'] and e['date'] != dt.date.today().isoformat():
            if s['latest'] >= e['watch_above']:
                e['resolved'] = 'hit_above'
            elif s['latest'] <= e['watch_below']:
                e['resolved'] = 'hit_below'
    try:
        PERF_LOG.write_text(json.dumps(data[-200:], indent=2))
    except Exception:
        pass


def _client():
    return tweepy.Client(consumer_key=config.X_API_KEY, consumer_secret=config.X_API_SECRET, access_token=config.X_ACCESS_TOKEN, access_token_secret=config.X_ACCESS_SECRET)


def upload_image(buf):
    data = buf.read() if hasattr(buf, 'read') else buf
    try:
        auth = tweepy.OAuth1UserHandler(config.X_API_KEY, config.X_API_SECRET, config.X_ACCESS_TOKEN, config.X_ACCESS_SECRET)
        api = tweepy.API(auth)
        media = api.media_upload(filename='image.png', file=io.BytesIO(data))
        return media.media_id
    except Exception as e:
        print(f'v1.1 upload failed: {e}')
    from requests_oauthlib import OAuth1
    a = OAuth1(config.X_API_KEY, config.X_API_SECRET, config.X_ACCESS_TOKEN, config.X_ACCESS_SECRET)
    r = requests.post('https://api.x.com/2/media/upload', auth=a, files={'media': ('image.png', data, 'image/png')})
    r.raise_for_status()
    j = r.json()
    return (j.get('data') or {}).get('id') or j.get('media_id_string')


def post_to_x(text, image_buf=None, reply_to=None):
    client = _client()
    media_ids = None
    if image_buf is not None:
        try:
            mid = upload_image(image_buf)
            if mid:
                media_ids = [str(mid)]
        except Exception as e:
            print(f'Image upload failed, text-only: {e}')
    resp = client.create_tweet(text=text, media_ids=media_ids, in_reply_to_tweet_id=reply_to)
    print('POST PUBLISHED OK')
    return resp.data['id']


def post_thread(tweets, image_buf=None):
    last = None
    for i, t in enumerate(tweets):
        last = post_to_x(t, image_buf=image_buf if i == 0 else None, reply_to=last)
    return last


def slot_1_market():
    if dt.date.today().toordinal() % 2 == 0:
        asset, df = 'Gold', get_gold_data()
    else:
        asset, df = 'Bitcoin', get_bitcoin_data()
    s = summarize(df, asset)
    log_levels(s)
    chart = make_chart(df, s)
    if abs(s['change_1d_pct']) >= 2.0:
        tweets = gen_thread(s)
        tid = post_thread(tweets, image_buf=chart)
        print(f"[slot1 THREAD {asset}] {tid}")
    elif dt.date.today().weekday() == 6:
        tid = post_to_x(gen_weekly_recap(s), chart)
        print(f"[slot1 recap {asset}] {tid}")
    else:
        tid = post_to_x(gen_market_post(s), chart)
        print(f"[slot1 market {asset}] {tid}")


def slot_2_story():
    if random.random() < 0.4:
        try:
            text = gen_trending()
            tid = post_to_x(text, pick_story_image(text.split('\n')[0]))
            print(f"[slot2 trending] {tid}")
            print(text)
            return
        except Exception as e:
            print(f'trending failed, falling back to story: {e}')
    text = gen_story(random.choice(config.STORY_THEMES))
    tid = post_to_x(text, pick_story_image(text.split('\n')[0]))
    print(f"[slot2 story] {tid}")
    print(text)


def slot_3_motivation():
    text = gen_motivation(random.choice(config.MOTIVATION_THEMES))
    tid = post_to_x(text)
    print(f"[slot3 motivation] {tid}")
    print(text)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--slot', type=int, choices=[1, 2, 3])
    a = p.parse_args()
    slot = a.slot
    if slot is None:
        h = dt.datetime.now(dt.timezone.utc).hour
        slot = 1 if h < 12 else (2 if h < 17 else 3)
    {1: slot_1_market, 2: slot_2_story, 3: slot_3_motivation}[slot]()


if __name__ == '__main__':
    main()
