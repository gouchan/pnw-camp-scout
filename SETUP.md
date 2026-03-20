# PNW Camp Scout — Setup Guide

---

## 🏕️ For Campers (Just Want to Find a Spot)

You don't need to know anything about code. You just need Telegram.

**1. Download Telegram**
- iPhone: [App Store → search "Telegram"](https://apps.apple.com/app/telegram-messenger/id686449807)
- Android: [Google Play → search "Telegram"](https://play.google.com/store/apps/details?id=org.telegram.messenger)

**2. Open Telegram and search for the bot**
- Tap the search icon (magnifying glass)
- Type the bot name your family shared with you (e.g. `@PNWCampScoutBot`)
- Tap the result → tap **START**

**3. Answer 8 quick questions**

The bot will ask you things like:
- How many people?
- Bringing dogs?
- What kind of scenery?
- Want swimming nearby?

Just tap the buttons — no typing required.

**4. Get your spots**

You'll get up to 5 campsite cards with everything you need:
gem score, kid-friendly flag, dog info, nearest water, hiking trails, wildlife warnings, and a booking link.

**Shortcuts:**
- `/top` — see the highest-rated gems right now, no questions
- `/buzz` — what's trending on camping subreddits this week
- `/start` — start a new search

---

## 🔧 For Robinson (One-Time Setup)

You only need to do this once. Takes about 10 minutes.

### Step 1 — Get API keys

You need a few free API keys. Here's where to get each one:

| Key | Where | Free? |
|---|---|---|
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) | Pay-per-use (~$0.01/search) |
| `TELEGRAM_BOT_TOKEN` | Telegram → @BotFather → /newbot | Free |
| `RECREATION_GOV_API_KEY` | [ridb.recreation.gov](https://ridb.recreation.gov/docs#/Facilities) | Free |
| `CAMPFLARE_API_KEY` | [campflare.com/api](https://campflare.com/api) | Free for individuals |
| `REDDIT_CLIENT_ID` + `REDDIT_CLIENT_SECRET` | [reddit.com/prefs/apps](https://www.reddit.com/prefs/apps) | Free |
| `GOOGLE_PLACES_API_KEY` | [console.cloud.google.com](https://console.cloud.google.com) | Free tier |
| `YOUTUBE_DATA_API_KEY` | [console.cloud.google.com](https://console.cloud.google.com) | Free tier |

### Step 2 — Get your Telegram bot token (2 min)

1. Open Telegram → search `@BotFather`
2. Tap **START**
3. Type `/newbot`
4. Follow the prompts: pick a name (e.g. "PNW Camp Scout") and a username (e.g. `@PNWCampScoutBot`)
5. BotFather gives you a token that looks like: `1234567890:ABCDefGhIJKlmNoPQRsTUvwXYZ-example`
6. Copy it — you'll paste it in Step 4

### Step 3 — Install on your computer

Open Terminal (Mac: press `Cmd+Space`, type "Terminal") and run:

```bash
cd ~/pnw-camp-scout
pip install -r requirements.txt
```

### Step 4 — Add your API keys

1. In the project folder, find the file called `.env.example`
2. Make a copy and rename it `.env`
3. Open it in any text editor and fill in your keys:

```
ANTHROPIC_API_KEY=sk-ant-YOUR_KEY_HERE
TELEGRAM_BOT_TOKEN=YOUR_BOT_TOKEN_HERE
RECREATION_GOV_API_KEY=YOUR_KEY_HERE
REDDIT_CLIENT_ID=YOUR_CLIENT_ID_HERE
REDDIT_CLIENT_SECRET=YOUR_CLIENT_SECRET_HERE
REDDIT_USER_AGENT=pnw-camp-scout/1.0 (by /u/YOUR_REDDIT_USERNAME)
GOOGLE_PLACES_API_KEY=YOUR_KEY_HERE
YOUTUBE_DATA_API_KEY=YOUR_KEY_HERE
```

### Step 5 — Initialize the database and score the seed sites

```bash
python main.py --init        # sets up the database
python main.py --seed        # scores all 23 hand-picked gems with Claude (takes ~2 min)
```

You'll see it print scores as it goes. Anything 90+ is legendary.

### Step 6 — Start the bot

```bash
python bots/telegram_bot.py
```

You'll see:
```
🏕️  PNW Camp Scout bot starting...
    Bot is live. Ctrl+C to stop.
```

**Open Telegram, find your bot, send `/start` — it works.**

Share the bot link (`t.me/YourBotUsername`) with your family.

---

## ☁️ Run 24/7 (So the Bot Stays On When Your Computer Is Off)

**Option A — Railway (recommended, free tier)**

1. Go to [railway.app](https://railway.app) → sign up with GitHub
2. Click **New Project** → **Deploy from GitHub repo** → select `pnw-camp-scout`
3. In Railway dashboard → **Variables** tab → add all your `.env` keys
4. Railway will deploy automatically. Bot stays on forever.

**Option B — Keep your computer running**

Just leave the terminal window open with `python bots/telegram_bot.py` running.

---

## ❓ Troubleshooting

**"No gems scored yet"**
Run `python main.py --seed` to score the 23 seed campsites.

**Bot doesn't respond**
Make sure `python bots/telegram_bot.py` is running in your terminal.

**"TELEGRAM_BOT_TOKEN not set"**
Check your `.env` file — make sure you copied it from `.env.example` and filled in the token.

**Search returns nothing**
The filters might be too narrow. Try `/start` and pick "Either works" for camp style and hiking. Or just tap `/top` for instant results.

---

## 💬 What the Bot Actually Does

Under the hood, every time you search, PNW Camp Scout:

1. **Reads Reddit** — searches r/PNWCamping and 4 other subreddits for real camper experiences
2. **Checks Google** — pulls ratings and reviews for each site
3. **Scans YouTube** — finds high-view camping videos for each spot
4. **Uses Claude AI** — scores each campsite 0–100 (gem score) based on all that data
5. **Checks availability** — pulls live data from Recreation.gov and Campflare

The gem score combines:
- Community buzz (Reddit + Google sentiment)
- How "hidden" it is (low reviews + high rating = hidden gem bonus)
- Proximity to iconic PNW landmarks
- Unique features (old growth, volcanic, hot springs, oceanfront, dark skies)
- Practical factors (bathrooms, road conditions, wildlife)

Sites that score 90+ are once-in-a-lifetime PNW experiences. 75+ are bucket-list worthy.

---

*Built by Robinson. Powered by Claude.*
