# 🏕️ PNW Camp Scout

An AI agent swarm that scours the Pacific Northwest for bucket-list campsites — then scores, ranks, and delivers them to your family via a Telegram bot.

Not just "available campsites." The ones locals call hidden gems.

---

## What It Does

PNW Camp Scout runs 4 specialized AI agents in a pipeline:

| Agent | Job |
|---|---|
| **Scout** | Pulls campsite data + live availability from Recreation.gov and Campflare |
| **Social** | Scrapes Reddit (r/PNWCamping, r/Oregon, r/Washington, etc.) + Google Places reviews |
| **Content** | Finds high-engagement YouTube camping videos for each site |
| **Classifier** | Uses Claude to assign a **gem score (0–100)** based on all the above |

The result: a ranked list of PNW campsites with real social proof, pet info, kid-friendliness, water access, hiking trails, and live availability — delivered via an 8-step Telegram wizard.

---

## Gem Score

The gem score is how the Classifier judges each campsite on a 0–100 scale:

- **Hidden gem bonus** — low review count + high rating = underrated spot
- **Reddit signal** — mentions of "bucket list", "hidden gem", "most people don't know about this"
- **YouTube engagement** — high-view videos = real-world validation
- **Unique PNW features** — old growth, volcanic, hot springs, coastal cliffs, dark skies
- **Practical factors** — bathrooms, road conditions, wildlife, group-friendliness

**90+** = once-in-a-lifetime PNW. **75+** = bucket-list worthy.

---

## Telegram Bot

The bot walks your family through 8 quick questions — no code, no typing. Just buttons.

```
/start   — 8-step filter wizard (group size, dogs, kids, scenery, water, hiking, dates, vibe)
/top     — top gems by score right now, no questions
/buzz    — what's trending on r/PNWCamping this week
/help    — show commands
```

Each result is a campsite card with: name, gem score, kid/dog flags, water access, hiking, wildlife note, and a booking link.

---

## Quickstart

**1. Clone and install**
```bash
git clone https://github.com/gouchan/pnw-camp-scout.git
cd pnw-camp-scout
pip install -r requirements.txt
```

**2. Set up environment**
```bash
cp .env.example .env
# Fill in your API keys — see SETUP.md for where to get each one
```

**3. Initialize and seed**
```bash
python main.py --init   # sets up SQLite database
python main.py --seed   # scores all 23 hand-curated PNW gems with Claude (~2 min)
```

**4. Run**
```bash
# Telegram bot
python bots/telegram_bot.py

# CLI search
python main.py "find me a pet-friendly coastal campsite in Washington, June"

# Show top gems
python main.py --top
```

→ See `SETUP.md` for full setup including API key registration, Railway deployment, and troubleshooting.

---

## Project Structure

```
pnw-camp-scout/
├── agents/
│   ├── scout.py        # Recreation.gov + Campflare availability
│   ├── social.py       # Reddit + Google Places scraping
│   ├── content.py      # YouTube Data API
│   ├── classifier.py   # Claude gem scoring
│   └── orchestrator.py # Query parsing + filter routing
├── bots/
│   ├── telegram_bot.py # Bot handler + card formatter
│   └── conversation.py # 8-step wizard state machine
├── config/
│   └── prompts.yaml    # Claude prompt templates
├── data/
│   ├── schema.sql      # SQLite schema
│   └── seeds/
│       └── pnw_gems.json  # 23 hand-curated PNW gems
├── main.py             # CLI entrypoint
├── agents.yaml         # Agent pipeline config
├── .env.example        # Key template — copy to .env
└── SETUP.md            # Full setup guide
```

---

## Seed Data

23 hand-picked PNW gems pre-loaded — from Diablo Lake to Iron Creek to Quinault Rain Forest. All tagged with:
- `pet_friendly`, `water_nearby`, `water_swimmable`
- `hiking_trails`, `max_group_size`, `has_group_sites`
- `kid_friendly`, `wildlife_note`

Run `--seed` to score them all with Claude and get a ranked leaderboard.

---

## Stack

- **Claude** (claude-sonnet-4-6) — gem scoring + query understanding
- **Recreation.gov RIDB API** — federal campground data + availability
- **Campflare API** — aggregated availability across booking systems
- **PRAW** — Reddit scraping
- **Google Places API** — ratings + reviews
- **YouTube Data API v3** — video discovery
- **python-telegram-bot ≥ 20.0** — async Telegram bot
- **SQLite** — local data store

---

## Requirements

- Python 3.9+
- API keys: Anthropic, Telegram BotFather, Recreation.gov, Reddit (free), optionally Google Places + YouTube

See `.env.example` for the full list.

---

*Built by [Robinson](https://github.com/gouchan). Powered by Claude.*
