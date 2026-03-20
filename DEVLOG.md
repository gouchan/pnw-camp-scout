# PNW Camp Scout — Devlog

Build log for the PNW camping AI swarm. Newest entries at the top.

---

## v0.4 — Security fix + repo hygiene
*March 2026*

**What happened:**
GitHub's secret scanner flagged a realistic-looking example Telegram bot token in `SETUP.md`. The string matched the token pattern exactly — even though it was never a real credential, GitHub can't distinguish intent from format.

**What we fixed:**
- Replaced all example tokens/keys in `SETUP.md` with clearly fake `YOUR_KEY_HERE` style placeholders
- Updated `.env.example` to include `TELEGRAM_BOT_TOKEN` (was missing) with safe placeholder values
- Added proper `.gitignore` — covers `.env`, `__pycache__`, `.db` files, `outputs/`, `.DS_Store`
- Added `README.md` — project overview, gem score explanation, quickstart, structure

**Lesson:**
Example credentials in docs should be visually unambiguous fakes. Not realistic-format strings that happen to be "safe" — any string matching a scanner pattern will fire the alert regardless of intent.

---

## v0.3 — Telegram bot + 8-step wizard
*March 2026*

**Built:**
- `bots/telegram_bot.py` — async bot using python-telegram-bot ≥ 20.0, inline keyboard buttons
- `bots/conversation.py` — `CampSession` state machine with 8 wizard steps
- Commands: `/start` (wizard), `/top` (ranked gems), `/buzz` (Reddit trending), `/help`
- `format_camp_card()` — rich campsite card with gem score badge, flags, booking link

**Wizard flow (8 steps):**
1. Group size (solo / couple / small family / large family)
2. Dogs?
3. Kids + ages
4. Travel dates
5. Scenery (forest / water / mountain / mix)
6. Camp style (developed / primitive / either)
7. Water (swimming / fishing / hiking water / none)
8. Hiking intensity (chill / moderate / strenuous)

Wizard outputs a `structured_filters` dict that routes directly to `orchestrator.py`'s `_apply_filters()` — no LLM needed for structured input.

**Also works without Telegram** — the wizard questions map 1:1 to a conversational Claude chat flow. Demonstrated live with Robinson's family use case.

---

## v0.2 — Extended data model + filters
*March 2026*

**New fields on gem_profiles:**
- `pet_friendly` — dogs allowed?
- `water_nearby` + `water_swimmable` — water access type
- `hiking_trails` — is there hiking directly from camp?
- `max_group_size` + `has_group_sites` — group planning
- `kid_friendly` + `wildlife_note` — family safety

**All 23 seed gems updated** in `data/seeds/pnw_gems.json` with the new fields.

**Schema migration** — `classifier.py` auto-adds columns if upgrading from v0.1 DB (no manual migration needed).

**Filter logic** in `orchestrator.py:_apply_filters()` — applies pet/water/hiking/group_size constraints before ranking by gem score.

**Classifier prompt updated** to return the new fields. Prompts live in `config/prompts.yaml` for easy tuning without touching Python.

---

## v0.1 — Initial swarm + CLI
*March 2026*

**Architecture decision:**
Multi-agent pipeline over a single monolith. Each agent owns a data source — Scout (availability), Social (Reddit/Google), Content (YouTube), Classifier (gem scoring). Orchestrator handles query → filter → rank → respond.

SQLite for local storage — good enough for a personal-scale tool, easy to inspect, no infra required.

**Seed data:** 23 hand-curated PNW campsites — the ones locals actually talk about on Reddit. Not Recreation.gov's top 10. Hidden gems like Diablo Lake, Iron Creek, Newberry/Paulina Lake, Quinault Rain Forest.

**Gem score formula (Claude-powered):**
- Hidden gem bonus (low review count + high rating)
- Reddit "gem language" signal mentions
- YouTube high-engagement video count
- Unique PNW features (old growth, volcanic, hot springs, coastal, dark skies)
- Practical factors (road access, bathrooms, wildlife)

**CLI commands:**
```bash
python main.py --init          # init DB
python main.py --seed          # score 23 seeds
python main.py --top           # ranked leaderboard
python main.py "query string"  # natural language search
```

**Agents pipeline:** `scout → social → content → classifier`
Full pipeline runs weekly (Monday 2am). Availability refreshes hourly. Social refreshes daily.

---

## Live search sessions

**Session 1 — Robinson's family (updated results)**
- Group: 2 adults + 5 kids (4 under 11, 1 at 15) + 2 dogs
- Dates: June–July, flexible
- Request: less obvious, more PNW charm, no state park feel

Top results:
1. **Quinault Rain Forest Campground** — temperate rainforest, elk, old growth, dog-friendly
2. **Newberry / Paulina Lake** — volcanic caldera, Oregon, two lakes, obsidian trail
3. **Lost Lake** — Mt. Hood reflection, waterfall hike, kayak rentals, dog-friendly
4. **Diablo Lake** — glacier-fed turquoise water, North Cascades, boat-in sites
5. **Iron Creek** — Mt. St. Helens base, lava fields, kid-safe flat sites
