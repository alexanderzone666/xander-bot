"""
Xander Zone - X Content Engine  (3 posts / day)
------------------------------------------------
Slot 1 (morning)  -> Live market analysis (Gold alternates with Bitcoin daily)
                     + auto chart + "Not financial advice" disclaimer
Slot 2 (midday)   -> Hot-take / storytelling post in an opinionated tone
                     + image (your own photo from photos/ OR auto quote-card)
Slot 3 (evening)  -> Motivational post about markets, money and mindset

Run manually:      python bot.py --slot 1
GitHub Actions runs each slot at its scheduled hour automatically.

SAFETY FRAMING (hardcoded in every prompt):
  - All market content is personal opinion / journal style
  - Never "buy", "sell", "you should" - scenarios, not predictions-as-fact
  - Slot 1 always ends with "Not financial advice." style line
"""

import os
import sys
import random
import argparse
import datetime as dt
from io import BytesIO
from pathlib import Path

import requests
import yfinance as yf
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import textwrap

import anthropic
import tweepy

import config

PHOTOS_DIR = Path(__file__).parent / "photos"   # drop your own jpg/png photos here

# ----------------------------------------------------------------------
# 1. MARKET DATA
# ----------------------------------------------------------------------

def get_bitcoin_data(days=30):
    url = "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart"
    r = requests.get(url, params={"vs_currency": "usd", "days": days, "interval": "daily"}, timeout=30)
    r.raise_for_status()
    df = pd.DataFrame(r.json()["prices"], columns=["ts", "close"])
    df["date"] = pd.to_datetime(df["ts"], unit="ms")
    return df[["date", "close"]]


def get_gold_data(days=30):
    df = yf.download("GC=F", period=f"{days}d", interval="1d", progress=False)
    df = df.reset_index()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    df = df.rename(columns={"Date": "date", "Close": "close"})
    return df[["date", "close"]].dropna()


def summarize(df, asset_name):
    latest = float(df["close"].iloc[-1])
    week_ago = float(df["close"].iloc[-6]) if len(df) > 6 else float(df["close"].iloc[0])
    month_ago = float(df["close"].iloc[0])
    return {
        "asset": asset_name,
        "latest": round(latest, 2),
        "change_7d_pct": round((latest - week_ago) / week_ago * 100, 2),
        "change_30d_pct": round((latest - month_ago) / month_ago * 100, 2),
        "high_30d": round(float(df["close"].max()), 2),
        "low_30d": round(float(df["close"].min()), 2),
    }

# ----------------------------------------------------------------------
# 2. IMAGES
# ----------------------------------------------------------------------

def make_chart(df, stats):
    """Dark branded price chart with 30d high/low marked."""
    fig, ax = plt.subplots(figsize=(10, 5.6), dpi=160)
    fig.patch.set_facecolor("#0d0d0f")
    ax.set_facecolor("#0d0d0f")

    up = stats["change_30d_pct"] >= 0
    line_color = "#22c55e" if up else "#ef4444"

    ax.plot(df["date"], df["close"], color=line_color, linewidth=2.2)
    ax.fill_between(df["date"], df["close"], df["close"].min(), color=line_color, alpha=0.08)

    ax.axhline(stats["high_30d"], color="#9ca3af", ls="--", lw=0.9, alpha=0.6)
    ax.axhline(stats["low_30d"], color="#9ca3af", ls="--", lw=0.9, alpha=0.6)
    ax.text(df["date"].iloc[0], stats["high_30d"], f'  {stats["high_30d"]:,}', color="#d1d5db", fontsize=9, va="bottom")
    ax.text(df["date"].iloc[0], stats["low_30d"], f'  {stats["low_30d"]:,}', color="#d1d5db", fontsize=9, va="top")

    sign = "+" if up else ""
    ax.set_title(f'{stats["asset"]}  -  ${stats["latest"]:,}  ({sign}{stats["change_30d_pct"]}% / 30d)',
                 color="white", fontsize=15, fontweight="bold", loc="left", pad=14)
    ax.text(1.0, 1.02, config.CHART_WATERMARK, transform=ax.transAxes, color="#6b7280", fontsize=10, ha="right")

    ax.tick_params(colors="#9ca3af", labelsize=9)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.grid(color="#1f2937", lw=0.5, alpha=0.6)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))

    buf = BytesIO()
    plt.tight_layout()
    plt.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf


def make_quote_card(hook_line):
    """Clean dark typography card with the post's hook line - always safe to use."""
    fig, ax = plt.subplots(figsize=(10, 5.6), dpi=160)
    fig.patch.set_facecolor("#0d0d0f")
    ax.set_facecolor("#0d0d0f")
    ax.axis("off")

    wrapped = "\n".join(textwrap.wrap(hook_line, width=34))
    ax.text(0.5, 0.55, wrapped, ha="center", va="center",
            color="white", fontsize=22, fontweight="bold", linespacing=1.6)
    ax.text(0.5, 0.08, config.CHART_WATERMARK, ha="center",
            color="#6b7280", fontsize=12)
    # subtle accent line
    ax.plot([0.42, 0.58], [0.22, 0.22], color="#22c55e", lw=2,
            transform=ax.transAxes)

    buf = BytesIO()
    plt.tight_layout()
    plt.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf


def pick_story_image(hook_line):
    """Use one of YOUR photos if the photos/ folder has any, else generate a quote card.
    Using your own lifestyle photos (cars, travel, setups) performs best and is
    copyright-safe. Never auto-download images from the internet."""
    if PHOTOS_DIR.exists():
        photos = [p for p in PHOTOS_DIR.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png")]
        if photos:
            # rotate by day so the same photo doesn't repeat back-to-back
            photo = photos[dt.date.today().toordinal() % len(photos)]
            return open(photo, "rb")
    return make_quote_card(hook_line)

# ----------------------------------------------------------------------
# 3. CONTENT (Claude)
# ----------------------------------------------------------------------

claude = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

PERSONA = """You write X posts for Alexander (@xanderzone), a trader and entrepreneur.
Voice: confident, direct, a little provocative, reflective. Hooks hard in line one.
Style: short punchy lines, line breaks between thoughts, no hashtags, max 1 emoji.

HARD RULES - never break these:
- Everything is HIS PERSONAL VIEW. Use "my read", "the way I see it", "I'm watching".
- NEVER give advice. Never write "buy", "sell", "you should", "get in", "don't miss".
- NEVER state predictions as fact. Use scenarios: "if X holds, I'm watching Y".
- No fake claims about his results, wealth, or track record."""

TONE_EXAMPLE = """Example of the exact tone wanted for story/hot-take posts:
"This might sound crazy, but there are guys that spent all year telling you the
four year cycle for Bitcoin was dumb, and when they were proven wrong, they did
not admit it, they just kept saying that the market is wrong, and they are right."
- calling out market narratives, ego, and crowd psychology with a knowing smirk."""


def gen_market_post(stats):
    prompt = f"""Live data for {stats['asset']}:
- Price now: ${stats['latest']:,}
- 7-day change: {stats['change_7d_pct']}%
- 30-day change: {stats['change_30d_pct']}%
- 30-day high: ${stats['high_30d']:,} / low: ${stats['low_30d']:,}

Write ONE X post: a hooking, catchy personal analysis of this data.
Reference the real numbers. Frame high/low as levels you're watching.
Scenario thinking only. Under 260 characters.
MUST end with exactly this line: "Not financial advice."
Return ONLY the post text."""
    return _ask(prompt)


def gen_story_post(theme):
    prompt = f"""{TONE_EXAMPLE}

Write ONE X post in exactly that tone on this theme: "{theme}".
Opinionated storytelling about market psychology, narratives, or the trading
journey. Strong hook first line. No specific price calls, no advice.
Under 270 characters. Return ONLY the post text."""
    return _ask(prompt)


def gen_motivation_post(theme):
    prompt = f"""Write ONE X post: motivational, about markets, money, discipline
and the builder's journey. Theme: "{theme}".
Warm but strong. Story-flavored, not preachy. No advice, no price talk.
Under 270 characters. Return ONLY the post text."""
    return _ask(prompt)


def _ask(prompt):
    msg = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        system=PERSONA,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip().strip('"')

# ----------------------------------------------------------------------
# 4. POST TO X
# ----------------------------------------------------------------------

def post_to_x(text, image_buf=None):
    client = tweepy.Client(
        consumer_key=config.X_API_KEY,
        consumer_secret=config.X_API_SECRET,
        access_token=config.X_ACCESS_TOKEN,
        access_token_secret=config.X_ACCESS_SECRET,
    )
    media_ids = None
    if image_buf is not None:
        auth = tweepy.OAuth1UserHandler(
            config.X_API_KEY, config.X_API_SECRET,
            config.X_ACCESS_TOKEN, config.X_ACCESS_SECRET)
        api_v1 = tweepy.API(auth)
        media = api_v1.media_upload(filename="image.png", file=image_buf)
        media_ids = [media.media_id]
    resp = client.create_tweet(text=text, media_ids=media_ids)
    return resp.data["id"]

# ----------------------------------------------------------------------
# 5. THE 3 DAILY SLOTS
# ----------------------------------------------------------------------

def slot_1_market():
    # Gold on even days, Bitcoin on odd days - guaranteed variety
    asset = "Gold" if dt.date.today().toordinal() % 2 == 0 else "Bitcoin"
    df = get_gold_data() if asset == "Gold" else get_bitcoin_data()
    stats = summarize(df, asset)
    text = gen_market_post(stats)
    chart = make_chart(df, stats)
    tweet_id = post_to_x(text, chart)
    print(f"[slot1 market:{asset}] {tweet_id}\n{text}")


def slot_2_story():
    theme = random.choice(config.STORY_THEMES)
    text = gen_story_post(theme)
    hook = text.split("\n")[0]
    image = pick_story_image(hook)
    tweet_id = post_to_x(text, image)
    print(f"[slot2 story] {tweet_id}\n{text}")


def slot_3_motivation():
    theme = random.choice(config.MOTIVATION_THEMES)
    text = gen_motivation_post(theme)
    tweet_id = post_to_x(text)
    print(f"[slot3 motivation] {tweet_id}\n{text}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--slot", type=int, choices=[1, 2, 3],
                        help="1=market, 2=story+image, 3=motivation. If omitted, picked by current UTC hour.")
    args = parser.parse_args()

    slot = args.slot
    if slot is None:
        hour = dt.datetime.utcnow().hour
        slot = 1 if hour < 12 else (2 if hour < 17 else 3)

    {1: slot_1_market, 2: slot_2_story, 3: slot_3_motivation}[slot]()


if __name__ == "__main__":
    main()
