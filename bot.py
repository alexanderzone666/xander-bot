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
NEWS_LOG = HERE / 'news_log.json'
ARTICLES_DIR = HERE / 'articles'

REPLY_BAIT = [
    'What level are you watching here?',
    'Am I wrong on this?',
    'Where are you leaning right now?',
    'Curious how you are reading this one.',
    'What is your take?',
    'Are you buying the move or fading it?',
]

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
    return {'price': float(j['price']), 'open': float(j.get('open_price', j['price'])), 'high': float(j.get('high_price', j['price'])), 'low': float(j.get('low_price', j['price'])), 'prev_close': float(j.get('prev_close_price', j['price']))}


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
                rows.append({'date': pd.Timestamp(d), 'open': float(j.get('open_price', p)), 'high': float(j.get('high_price', p)), 'low': float(j.get('low_price', p)), 'close': float(p)})
        except Exception:
            continue
    spot = _gold_spot()
    rows.append({'date': pd.Timestamp(today), 'open': spot['open'], 'high': spot['high'], 'low': spot['low'], 'close': spot['price']})
    if len(rows) < 2:
        p = spot['price']
        rows = [{'date': pd.Timestamp(today - dt.timedelta(days=i)), 'open': p, 'high': p, 'low': p, 'close': p} for i in range(days, -1, -1)]
    return pd.DataFrame(rows)


def pick_primary_asset():
    if dt.date.today().toordinal() % 3 == 0:
        return 'Bitcoin', get_bitcoin_data()
    return 'Gold', get_gold_data()


def pick_secondary_asset():
    if dt.date.today().toordinal() % 3 == 0:
        return 'Gold', get_gold_data()
    return 'Bitcoin', get_bitcoin_data()


def summarize(df, asset):
    latest = float(df['close'].iloc[-1])
    week_ago = float(df['close'].iloc[-6]) if len(df) > 6 else float(df['close'].iloc[0])
    month_ago = float(df['close'].iloc[0])
    prev = float(df['close'].iloc[-2]) if len(df) > 1 else latest
    return {'asset': asset, 'latest': round(latest, 2), 'change_1d_pct': round((latest - prev) / prev * 100, 2), 'change_7d_pct': round((latest - week_ago) / week_ago * 100, 2), 'change_30d_pct': round((latest - month_ago) / month_ago * 100, 2), 'high_30d': round(float(df['high'].max()), 2), 'low_30d': round(float(df['low'].min()), 2)}


def round_levels(price):
    step = 100 if price >= 1000 else (10 if price >= 100 else 1)
    below = (int(price) // step) * step
    return below, below + step


UP_C = '#16c784'
DN_C = '#ea3943'
BG_C = '#0b0e13'


def make_chart(df, stats, scenario=False):
    d = df.tail(30).copy().reset_index(drop=True)
    fig = plt.figure(figsize=(10, 5.8), dpi=170)
    fig.patch.set_facecolor(BG_C)
    ax = fig.add_axes([0.07, 0.11, 0.86, 0.64])
    ax.set_facecolor(BG_C)
    x = mdates.date2num(d['date'].dt.to_pydatetime())
    w = (x[1] - x[0]) * 0.55 if len(x) > 1 else 0.55
    for xi, o, h, l, c in zip(x, d['open'], d['high'], d['low'], d['close']):
        up = c >= o
        col = UP_C if up else DN_C
        ax.plot([xi, xi], [l, h], color=col, lw=0.9, alpha=0.95, zorder=2, solid_capstyle='round')
        lo, hi = (o, c) if up else (c, o)
        ax.add_patch(plt.Rectangle((xi - w / 2, lo), w, max(hi - lo, 0.01), facecolor=col, edgecolor='none', zorder=3))
    below, above = round_levels(stats['latest'])
    if scenario:
        span = max(stats['latest'] * 0.004, (stats['high_30d'] - stats['low_30d']) * 0.02)
        ax.axhspan(below - span, below + span, color=UP_C, alpha=0.10, zorder=1)
        ax.axhspan(above - span, above + span, color=DN_C, alpha=0.10, zorder=1)
        ax.axhline(below, color=UP_C, lw=1.0, alpha=0.75)
        ax.axhline(above, color=DN_C, lw=1.0, alpha=0.75)
        ax.text(x[0], below, f' {below:,.0f}  support watch ', color=BG_C, fontsize=8.5, fontweight='bold', va='center', zorder=6, bbox=dict(boxstyle='round,pad=0.32', fc=UP_C, ec='none'))
        ax.text(x[0], above, f' {above:,.0f}  resistance watch ', color='white', fontsize=8.5, fontweight='bold', va='center', zorder=6, bbox=dict(boxstyle='round,pad=0.32', fc=DN_C, ec='none'))
    lp = stats['latest']
    ax.axhline(lp, color='#8b93a7', ls=(0, (2, 3)), lw=0.8, alpha=0.7)
    ax.text(x[-1], lp, f' {lp:,.2f} ', color=BG_C, fontsize=9, fontweight='bold', va='center', ha='left', zorder=6, bbox=dict(boxstyle='round,pad=0.28', fc='#e8eaf0', ec='none'))
    ax.text(0.5, 0.5, config.CHART_WATERMARK, transform=ax.transAxes, color='white', alpha=0.06, fontsize=46, fontweight='bold', ha='center', va='center', zorder=1)
    ax.tick_params(colors='#6b7280', labelsize=8.5, length=0)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.grid(color='#161b25', lw=0.6)
    ax.set_axisbelow(True)
    ax.xaxis_date()
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%d %b'))
    if stats['asset'] == 'Gold':
        sym, badge_c, tick = 'Au', '#f5c542', 'XAUUSD'
    else:
        sym, badge_c, tick = '\u20bf', '#f7931a', 'BTCUSD'
    fig.text(0.075, 0.895, ' ' + sym + ' ', fontsize=14, fontweight='bold', color=BG_C, bbox=dict(boxstyle='circle,pad=0.34', fc=badge_c, ec='none'))
    fig.text(0.135, 0.915, stats['asset'], fontsize=16, fontweight='bold', color='white')
    fig.text(0.135, 0.878, tick + '  \u00b7  live spot', fontsize=9, color='#8b93a7')
    sign = '+' if stats['change_1d_pct'] >= 0 else ''
    pill_c = UP_C if stats['change_1d_pct'] >= 0 else DN_C
    fig.text(0.93, 0.915, f"${stats['latest']:,.2f}", fontsize=17, fontweight='bold', color='white', ha='right')
    fig.text(0.93, 0.872, f" {sign}{stats['change_1d_pct']}% today ", fontsize=9.5, fontweight='bold', color='white', ha='right', bbox=dict(boxstyle='round,pad=0.3', fc=pill_c, ec='none'))
    fig.text(0.93, 0.035, config.CHART_WATERMARK, fontsize=10.5, fontweight='bold', color='#8b93a7', ha='right')
    fig.text(0.07, 0.035, dt.datetime.now(dt.timezone.utc).strftime('%d %b %Y \u00b7 %H:%M UTC'), fontsize=8.5, color='#5b6270')
    buf = BytesIO()
    plt.savefig(buf, format='png', facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf


def make_quote_card(hook):
    import textwrap
    fig, ax = plt.subplots(figsize=(10, 5.6), dpi=160)
    fig.patch.set_facecolor(BG_C)
    ax.set_facecolor(BG_C)
    ax.axis('off')
    ax.text(0.5, 0.55, '\n'.join(textwrap.wrap(hook, width=34)), ha='center', va='center', color='white', fontsize=22, fontweight='bold', linespacing=1.6)
    ax.text(0.5, 0.08, config.CHART_WATERMARK, ha='center', color='#8b93a7', fontsize=12, fontweight='bold')
    ax.plot([0.42, 0.58], [0.22, 0.22], color=UP_C, lw=2, transform=ax.transAxes)
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

PERSONA = ('You write X posts for Alexander (@xanderzone), a trader and entrepreneur. Voice: confident, direct, a little provocative, reflective. The FIRST LINE must be a scroll-stopping hook - a bold claim, a sharp question, or a pattern-interrupt that makes people stop and read. Style: short punchy lines, line breaks between thoughts, no hashtags, max 1 emoji. HARD RULES: Everything is HIS PERSONAL VIEW (my read, the way I see it, I am watching). NEVER give advice, never buy/sell/you should/get in/dont miss. NEVER give entries, stop losses, take profits, or position sizes. NEVER state predictions as fact; use scenarios. No fake claims about results or wealth. NEVER use hashtags - they reduce reach.')

TONE = ('Example tone for hot-takes: This might sound crazy, but there are guys that spent all year telling you the four year cycle for Bitcoin was dumb, and when they were proven wrong, they did not admit it, they just kept saying the market is wrong, and they are right. Calling out narratives, ego, crowd psychology with a knowing smirk.')

ESSAY_VOICE = ('Write in an immersive, personal-essay voice. Very short paragraphs, often one sentence. Intentional white space between lines. A hook that pulls the reader in immediately. Build quietly, then land a calm, resonant insight at the end. Reflective, confident, never preachy, never salesy. No hashtags, no emojis, no advice, no fabricated personal trades or fake numbers.')


def _ask(prompt, use_search=False, max_tokens=900, last_only=False):
    kwargs = dict(model='claude-sonnet-4-6', max_tokens=max_tokens, system=PERSONA, messages=[{'role': 'user', 'content': prompt}])
    if use_search:
        kwargs['tools'] = [{'type': 'web_search_20250305', 'name': 'web_search'}]
    m = claude.messages.create(**kwargs)
    blocks = [b.text for b in m.content if getattr(b, 'type', '') == 'text']
    if last_only:
        txt = (blocks[-1] if blocks else '').strip()
    else:
        txt = '\n'.join(blocks).strip()
    return txt.strip('"')


def maybe_reply_bait(text):
    if 'Not financial advice.' in text:
        if random.random() < 0.30:
            q = random.choice(REPLY_BAIT)
            return text.replace('Not financial advice.', q + '\n\nNot financial advice.')
        return text
    if random.random() < 0.30:
        return text + '\n\n' + random.choice(REPLY_BAIT)
    return text


REASONING_PHRASES = ['let me', "i'll", 'i will', 'searching', 'search for', 'checking', 'check for', 'applying the', 'strict bar', 'markets are closed', 'no breaking', 'does not meet', 'doesn', 'based on my', 'the last 3 hours', 'qualifies', 'sources confirm', 'here is the post', "here's the post", 'kitco confirms']


def check_news():
    if dt.datetime.now(dt.timezone.utc).weekday() >= 5:
        print('news check: weekend, markets closed, skipping')
        return
    try:
        seen = json.loads(NEWS_LOG.read_text()) if NEWS_LOG.exists() else []
    except Exception:
        seen = []
    seen_topics = [e.get('topic','') for e in seen]
    spot = None
    try:
        spot = _gold_spot()['price']
    except Exception:
        pass
    price_line = f' Live gold spot right now: ${spot:,.2f}.' if spot else ''
    raw = _ask(f"Search the web for BREAKING news from the last 3 hours that directly impacts GOLD (XAUUSD) or BITCOIN prices - Fed decisions, CPI surprises, war escalation, major ETF flows, exchange failures, huge liquidations, central bank gold buying.{price_line} Topics already covered, do NOT repeat: {seen_topics[-25:]}. YOU ARE A SILENT FILTER WITH EXACTLY TWO ALLOWED OUTPUTS AND NOTHING ELSE. OUTPUT 1 (when nothing from the last 3 hours meets a genuinely HIGH-impact market-moving bar): the single token NO_NEWS alone. No explanation, no reasoning, no commentary about why, not one extra word. OUTPUT 2 (only when real breaking news qualifies): first line exactly 'TOPIC: <4-6 word unique key>', then a blank line, then ONE X post - casual insider reaction, not news-channel style: what happened in one tight line, then 'my read:' on how it likely hits gold or BTC in scenario language. No links, no hashtags. Under 270 chars. End EXACTLY: Not financial advice. NEVER output your thinking, your search process, or any commentary in either case.", use_search=True, max_tokens=800, last_only=True).strip()
    up = raw.upper()
    if 'NO_NEWS' in up:
        print('news check: nothing major')
        return
    lns = raw.split('\n')
    if not lns or not lns[0].strip().upper().startswith('TOPIC:'):
        print('news check: malformed output (no TOPIC line), skipping')
        return
    topic = lns[0].split(':', 1)[1].strip()[:80]
    body = '\n'.join(lns[1:]).strip()
    bl = body.lower()
    if any(p in bl for p in REASONING_PHRASES):
        print('news check: reasoning detected in body, skipping')
        return
    if len(body) < 30 or len(body) > 279:
        print(f'news check: bad length {len(body)}, skipping')
        return
    if 'Not financial advice.' not in body:
        print('news check: missing disclaimer, skipping')
        return
    if topic and any(topic.lower() == t.lower() for t in seen_topics):
        print(f'news check: duplicate topic {topic}, skipping')
        return
    tid = post_to_x(body)
    print(f'[NEWS] {topic} -> {tid}')
    print(body)
    seen.append({'date': dt.datetime.now(dt.timezone.utc).isoformat(), 'topic': topic or body[:60]})
    try:
        NEWS_LOG.write_text(json.dumps(seen[-60:], indent=2))
    except Exception:
        pass


def gen_scenario_post(s):
    below, above = round_levels(s['latest'])
    return _ask(f"Live {s['asset']} spot: ${s['latest']:,.2f}. Support zone I marked: ${below:,.0f}. Resistance zone I marked: ${above:,.0f}. 30d high ${s['high_30d']:,.0f} low ${s['low_30d']:,.0f}, 1d move {s['change_1d_pct']}%. Write ONE X post in this exact structure: scroll-stopping hook line about {s['asset']} at this price. Then MY two-sided scenario read: if the ${below:,.0f} zone holds, what path opens; if it breaks, where the flush likely goes. Frame ONLY as levels I am watching, explicitly say these are levels I am watching, not trades I am giving. NO entries, NO stop loss, NO take profit, NO advice. Under 270 chars. End EXACTLY: Not financial advice. Return ONLY the post text.")


def gen_followup_post(entry, s):
    outcome = 'held and the upper zone got tagged' if entry['resolved'] == 'hit_above' else 'broke and price flushed lower'
    return _ask(f"On {entry['date']} I publicly marked {entry['asset']} levels: watching ${entry['watch_below']:,.0f} below and ${entry['watch_above']:,.0f} above, price then ${entry['price']:,.2f}. Since then the level {outcome}. Price now ${s['latest']:,.2f}. Write ONE honest, classy X post reflecting on how that public read aged - confident if it played out, gracefully honest if it did not. The tone: the level did the work, I just watched it. No gloating, no fake wins, no advice. Under 260 chars. End EXACTLY: Not financial advice. Return ONLY the post text.")


def gen_weekly_recap(s):
    return _ask(f"Sunday recap for {s['asset']} live spot. Now ${s['latest']:,.2f}, 7d {s['change_7d_pct']}%, 30d high ${s['high_30d']:,.0f} low ${s['low_30d']:,.0f}. Write ONE X post recapping the weeks move and key level tested/held, scenario for week ahead, strong hook first line, under 260 chars, end EXACTLY: Not financial advice. Return ONLY the post text.")


def gen_thread(s):
    below, above = round_levels(s['latest'])
    raw = _ask(f"{s['asset']} just moved {s['change_1d_pct']}% today to ${s['latest']:,.2f}. 30d high ${s['high_30d']:,.0f} low ${s['low_30d']:,.0f}. Zones I marked: ${below:,.0f}/${above:,.0f}. Write a 3-tweet X THREAD breaking down the move (scroll-stopping hook, personal read, two-sided scenarios around my marked zones, what I am watching next). NO entries, stops, or targets - only levels I am watching. Separate the 3 tweets with a line containing only three dashes. Each tweet under 270 chars. Last tweet ends EXACTLY: Not financial advice. Return ONLY the tweets.")
    parts = [p.strip() for p in raw.split('---') if p.strip()]
    return parts[:3] if parts else [raw]


def gen_story(theme):
    return _ask(f'{TONE} Write ONE X post in that tone on: {theme}. Scroll-stopping hook line 1. No price calls, no advice. Under 260 chars. Return ONLY the post.')


def gen_motivation(theme):
    return _ask(f'Write ONE X post: motivational about markets, money, discipline, the builders journey. Theme: {theme}. Strong hook first line, warm but strong, story-flavored. No advice. Under 260 chars. Return ONLY the post.')


def gen_trending():
    raw = _ask('Search the web for what is trending RIGHT NOW in forex, crypto, and trading lifestyle in the last 24 hours. Pick the single most talked-about topic. Then output ONLY one classy, hooking X post giving MY personal take in the persona voice - scroll-stopping hook first line, opinionated but elegant. Reference the topic clearly. No links, no hashtags. Under 265 chars. If it touches markets, end EXACTLY: Not financial advice. OUTPUT ONLY THE POST TEXT - no preamble, no topic explanation, no commentary about your choice.', use_search=True, last_only=True).strip()
    if '---' in raw:
        raw = raw.split('---')[-1].strip()
    bl = raw.lower()
    if any(p in bl for p in REASONING_PHRASES) or len(raw) < 30 or len(raw) > 279:
        raise Exception('trending output malformed, falling back')
    return raw


def gen_essay(topic):
    return _ask(f'{ESSAY_VOICE} Topic: {topic}. Write ONE long-form X post (an immersive essay) in that voice. Use short paragraphs with blank lines between them. Open with a scroll-stopping hook, build quietly, end on a resonant one-line insight. 150 to 320 words. No hashtags, no emojis, no advice. Return ONLY the essay text.', max_tokens=1200)


def gen_article(topic):
    return _ask(f'{ESSAY_VOICE} Topic: {topic}. Write a full long-form X ARTICLE (a personal essay, 500 to 800 words) in that immersive voice. Give it a short, magnetic title on the very first line, then a blank line, then the essay with short paragraphs and section breaks (a single line with three dashes between sections). End on a quiet, resonant insight. No hashtags, no emojis, no advice, no fabricated trades or fake numbers. Return ONLY title and body.', max_tokens=2500)


def save_article(topic):
    try:
        ARTICLES_DIR.mkdir(exist_ok=True)
        text = gen_article(topic)
        fname = ARTICLES_DIR / f"{dt.date.today().isoformat()}.md"
        fname.write_text(text)
        print(f'ARTICLE DRAFT SAVED: {fname.name}')
        return str(fname)
    except Exception as e:
        print(f'article save failed: {e}')
        return None


def log_levels(s):
    try:
        data = json.loads(PERF_LOG.read_text()) if PERF_LOG.exists() else []
    except Exception:
        data = []
    below, above = round_levels(s['latest'])
    newly_resolved = []
    for e in data:
        if e.get('resolved') is None and e['asset'] == s['asset'] and e['date'] != dt.date.today().isoformat():
            if s['latest'] >= e['watch_above']:
                e['resolved'] = 'hit_above'
                if not e.get('announced'):
                    newly_resolved.append(e)
            elif s['latest'] <= e['watch_below']:
                e['resolved'] = 'hit_below'
                if not e.get('announced'):
                    newly_resolved.append(e)
    data.append({'date': dt.date.today().isoformat(), 'asset': s['asset'], 'price': s['latest'], 'watch_below': below, 'watch_above': above, 'resolved': None, 'announced': False})
    try:
        PERF_LOG.write_text(json.dumps(data[-200:], indent=2))
    except Exception:
        pass
    return newly_resolved


def mark_announced(entry):
    try:
        data = json.loads(PERF_LOG.read_text()) if PERF_LOG.exists() else []
        for e in data:
            if e['date'] == entry['date'] and e['asset'] == entry['asset'] and e['price'] == entry['price']:
                e['announced'] = True
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
    asset, df = pick_primary_asset()
    s = summarize(df, asset)
    resolved = log_levels(s)
    if resolved:
        entry = resolved[0]
        try:
            fu = gen_followup_post(entry, s)
            chart_fu = make_chart(df, s, scenario=True)
            tid = post_to_x(fu, chart_fu)
            mark_announced(entry)
            print(f"[slot1 followup {asset}] {tid}")
            print(fu)
        except Exception as e:
            print(f'followup failed: {e}')
    chart = make_chart(df, s, scenario=True)
    if abs(s['change_1d_pct']) >= 2.0:
        tweets = gen_thread(s)
        tid = post_thread(tweets, image_buf=chart)
        print(f"[slot1 THREAD {asset}] {tid}")
    elif dt.date.today().weekday() == 6:
        tid = post_to_x(maybe_reply_bait(gen_weekly_recap(s)), chart)
        print(f"[slot1 recap {asset}] {tid}")
    else:
        tid = post_to_x(maybe_reply_bait(gen_scenario_post(s)), chart)
        print(f"[slot1 scenario {asset}] {tid}")
    if dt.date.today().weekday() == 6:
        save_article(random.choice(config.ESSAY_TOPICS))


def slot_2_story():
    if random.random() < 0.5:
        try:
            asset, df = pick_secondary_asset()
            s = summarize(df, asset)
            chart = make_chart(df, s, scenario=True)
            tid = post_to_x(maybe_reply_bait(gen_scenario_post(s)), chart)
            print(f"[slot2 scenario {asset}] {tid}")
            return
        except Exception as e:
            print(f'second scenario failed, falling back: {e}')
    if random.random() < 0.5:
        try:
            text = gen_trending()
            tid = post_to_x(maybe_reply_bait(text), pick_story_image(text.split('\n')[0]))
            print(f"[slot2 trending] {tid}")
            print(text)
            return
        except Exception as e:
            print(f'trending failed, falling back to story: {e}')
    text = gen_story(random.choice(config.STORY_THEMES))
    tid = post_to_x(maybe_reply_bait(text), pick_story_image(text.split('\n')[0]))
    print(f"[slot2 story] {tid}")
    print(text)


def slot_3_motivation():
    if dt.date.today().weekday() in (1, 4):
        topic = random.choice(config.ESSAY_TOPICS)
        text = gen_essay(topic)
        tid = post_to_x(text)
        print(f"[slot3 essay] {tid}")
        print(text)
        return
    text = gen_motivation(random.choice(config.MOTIVATION_THEMES))
    tid = post_to_x(text, pick_story_image(text.split('\n')[0]))
    print(f"[slot3 motivation] {tid}")
    print(text)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--slot', type=int, choices=[1, 2, 3])
    p.add_argument('--news', action='store_true')
    a = p.parse_args()
    if a.news:
        check_news()
        return
    slot = a.slot
    if slot is None:
        h = dt.datetime.now(dt.timezone.utc).hour
        slot = 1 if h < 12 else (2 if h < 17 else 3)
    {1: slot_1_market, 2: slot_2_story, 3: slot_3_motivation}[slot]()


if __name__ == '__main__':
    main()
