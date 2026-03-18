"""PNW Camp Scout — Telegram Bot

Commands:
  /start  — launches the 8-step guided wizard
  /top    — instant top gems, no questions asked
  /buzz   — what's buzzing on Reddit this week
  /help   — quick guide

Run: python bots/telegram_bot.py
"""

import os
import sys
import asyncio
import logging
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from telegram.constants import ParseMode

from bots.conversation import (
    CampSession, get_session, reset_session,
    STEPS, STEP_QUESTIONS, STEP_BUTTONS,
    handle_answer, build_query,
)
from agents.orchestrator import Orchestrator
from agents.classifier import ClassifierAgent


logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

DB_PATH = str(Path(__file__).parent.parent / "data" / "campsites.db")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")


# ─── Shared instances ─────────────────────────────────────────────────────────

_orchestrator: Orchestrator | None = None
_classifier: ClassifierAgent | None = None


def get_orchestrator() -> Orchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = Orchestrator(DB_PATH)
    return _orchestrator


def get_classifier() -> ClassifierAgent:
    global _classifier
    if _classifier is None:
        _classifier = ClassifierAgent(DB_PATH)
    return _classifier


# ─── Keyboard builder ─────────────────────────────────────────────────────────

def build_keyboard(step: str) -> InlineKeyboardMarkup:
    rows = STEP_BUTTONS.get(step, [])
    keyboard = [
        [InlineKeyboardButton(label, callback_data=f"{step}:{data}") for label, data in row]
        for row in rows
    ]
    return InlineKeyboardMarkup(keyboard)


def step_text(step: str) -> str:
    step_num = STEPS.index(step) + 1 if step in STEPS else 0
    question = STEP_QUESTIONS.get(step, "")
    return f"*Step {step_num} of {len(STEPS)}*\n\n{question}"


# ─── Result card formatter ────────────────────────────────────────────────────

def format_camp_card(camp: dict, rank: int) -> str:
    score = camp.get("gem_score", "?")
    factor = camp.get("bucket_list_factor", "").upper()
    badge = {"LEGENDARY": "🏆 LEGENDARY", "HIGH": "🌟 BUCKET LIST", "MEDIUM": "✨ EXCELLENT"}.get(factor, "⭐ GOOD")

    name = camp.get("name", "Unknown")
    why = camp.get("why_its_special", "")
    season = camp.get("best_season", "?")
    kid = "✅" if camp.get("kid_friendly") else ("❌" if camp.get("kid_friendly") is False else "—")
    dogs = "✅" if camp.get("pet_friendly") else "❌"
    dogs_trail = " (on leash)" if camp.get("dogs_on_leash_ok") else ""

    bathrooms = camp.get("bathrooms", "unknown")
    road = camp.get("road_conditions", "?")
    cell = camp.get("cell_signal", "?")

    water_type = camp.get("water_nearby_type") or "none"
    swimmable = "✅ swimming!" if camp.get("water_swimmable") else "(no swimming)"
    water_line = f"💧 *Water:* {water_type.replace('_', ' ').title()} {swimmable}" if water_type != "none" else "💧 *Water:* None nearby"

    hike = camp.get("hiking_trails_nearby")
    trail_notes = camp.get("hiking_trail_notes", "")
    hike_line = f"🥾 *Hiking:* ✅ {trail_notes}" if hike else "🥾 *Hiking:* Not nearby"

    group_max = camp.get("group_max_size")
    group_sites = camp.get("has_group_sites")
    group_line = f"👥 *Group max:* {group_max or '?'}" + (" | Group sites: ✅" if group_sites else "")

    wildlife = camp.get("wildlife_risk", {})
    bear = wildlife.get("bears", "unknown")
    bear_emoji = {"high": "🐻 HIGH", "medium": "🐻 medium", "low": "✅ low"}.get(bear, "—")
    wildlife_notes = wildlife.get("notes", "")
    wildlife_line = f"🐻 *Wildlife:* Bears {bear_emoji}"
    if wildlife_notes:
        wildlife_line += f"\n   _{wildlife_notes}_"

    landmark = camp.get("nearest_landmark")
    lm_dist = camp.get("landmark_distance_miles")
    landmark_line = f"📍 *Near:* {landmark} ({lm_dist} mi away)" if landmark and landmark != "Unknown" else ""

    book_url = camp.get("reservation_url", "")
    book_line = f"🔗 [Book / Reserve]({book_url})" if book_url else "🔗 No reservation needed (first-come)"

    region_display = (camp.get("region") or "").replace("_", " ").title()

    lines = [
        f"🏕️ *#{rank} — {name}*",
        f"⭐ Gem Score: *{score}/100* | {badge}",
        f"📍 {region_display}",
        "",
        why,
        "",
        f"📅 *Best time:* {season}",
        f"👨‍👩‍👧 *Kid-friendly:* {kid} | 🐕 *Dogs:* {dogs}{dogs_trail}",
        f"🚽 *Bathrooms:* {bathrooms} | 🛣️ *Road:* {road} | 📶 *Cell:* {cell}",
        water_line,
        hike_line,
        group_line,
        wildlife_line,
    ]
    if landmark_line:
        lines.append(landmark_line)
    lines += ["", book_line]

    return "\n".join(lines)


def format_lucky_card(camp: dict) -> str:
    return (
        "🎲 *Your surprise pick:*\n\n"
        + format_camp_card(camp, 1)
        + "\n\n_Type /start to search again or /top for more gems._"
    )


# ─── /start ───────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = reset_session(user_id)

    msg = await update.message.reply_text(
        step_text("group_size"),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=build_keyboard("group_size"),
    )
    session.wizard_message_id = msg.message_id


# ─── /top ────────────────────────────────────────────────────────────────────

async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Pulling top PNW gems right now...")
    classifier = get_classifier()
    gems = classifier.get_top_gems(min_score=70, limit=5)

    if not gems:
        await update.message.reply_text(
            "No gems scored yet. Ask Robinson to run: python main.py --seed"
        )
        return

    await update.message.reply_text(
        f"🏆 *Top {len(gems)} PNW Camping Gems*",
        parse_mode=ParseMode.MARKDOWN,
    )
    for i, gem in enumerate(gems, 1):
        try:
            await update.message.reply_text(
                format_camp_card(gem, i),
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True,
            )
        except Exception as e:
            logger.warning(f"Card send error: {e}")
            await update.message.reply_text(f"#{i} {gem['name']} — {gem.get('gem_score')}/100")


# ─── /buzz ───────────────────────────────────────────────────────────────────

async def cmd_buzz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📡 Checking what's buzzing on Reddit this week...")
    classifier = get_classifier()
    buzzing = classifier.get_recently_buzzed(limit=5)

    if not buzzing:
        await update.message.reply_text(
            "🦗 Quiet week on the camping subreddits — check back soon!\n\n"
            "Try /top for the all-time highest rated spots."
        )
        return

    await update.message.reply_text(
        "🔥 *Trending this week on r/PNWCamping and friends:*",
        parse_mode=ParseMode.MARKDOWN,
    )
    for i, spot in enumerate(buzzing, 1):
        mentions = spot.get("mentions", 0)
        sentiment = spot.get("sentiment", 0)
        mood = "🔥 hot" if sentiment > 0.3 else ("😐 mixed" if sentiment > -0.1 else "⚠️ cautious")
        score = spot.get("gem_score", "?")
        text = (
            f"*{i}. {spot['name']}*\n"
            f"   💬 {mentions} mentions this week | mood: {mood} | gem: {score}/100\n"
            f"   {spot.get('why_its_special', '')[:120]}...\n"
        )
        if spot.get("reservation_url"):
            text += f"   🔗 [Book]({spot['reservation_url']})"
        await update.message.reply_text(
            text, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True
        )


# ─── /help ───────────────────────────────────────────────────────────────────

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🏕️ *PNW Camp Scout* — your AI camping concierge\n\n"
        "I know every bucket-list campsite in Washington and Oregon. "
        "I've read every Reddit thread, watched every YouTube video, and I keep tabs on what's actually available.\n\n"
        "*Commands:*\n"
        "• /start — answer 8 quick questions → get your top 5 spots\n"
        "• /top — see the highest-rated gems right now\n"
        "• /buzz — what's trending on camping subreddits this week\n"
        "• /help — this message\n\n"
        "*What I filter for:*\n"
        "Group size · Dogs · Dates · Scenery · Water (swimming!) · Hiking · Camp style · Kids\n\n"
        "_Built by Robinson. Powered by Claude._",
        parse_mode=ParseMode.MARKDOWN,
    )


# ─── Wizard button handler ────────────────────────────────────────────────────

async def handle_wizard_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    session = get_session(user_id)

    # Parse callback: "step:answer"
    raw = query.data
    if ":" not in raw:
        return
    step_key, answer_data = raw.split(":", 1)

    # Ignore buttons from a previous wizard step
    if session.done or step_key != session.state:
        return

    next_state, is_lucky = handle_answer(session, answer_data)

    # 🎲 Feeling lucky
    if is_lucky:
        await query.edit_message_text("🎲 Rolling the dice on a gem for you...")
        orch = get_orchestrator()
        pick = orch.feeling_lucky(filters=session.filters)
        if pick:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=format_lucky_card(pick),
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True,
            )
        else:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="🤔 Couldn't roll a pick right now. Try /top for the best gems!",
            )
        return

    # Done — run search
    if next_state == "done":
        await query.edit_message_text("🔍 *Searching...*", parse_mode=ParseMode.MARKDOWN)

        filters = session.filters
        nl_query = build_query(filters)

        try:
            orch = get_orchestrator()
            # Use structured filters for accuracy; enrich=False for speed
            results_text = orch.query(
                nl_query,
                enrich=False,
                structured_filters=filters,
            )

            # The orchestrator returns synthesized text — also pull raw gems for cards
            gems = orch.classifier.get_top_gems(
                region=filters.get("regions", [None])[0] if filters.get("regions") else None,
                min_score=50,
                limit=5,
                pet_friendly=filters.get("pet_friendly"),
                water_type=filters.get("water_type"),
                needs_hiking=filters.get("needs_hiking"),
                min_group_size=filters.get("group_size") if filters.get("large_group") else None,
            )

            await query.edit_message_text(
                f"✅ *Found {len(gems)} spots for you!*",
                parse_mode=ParseMode.MARKDOWN,
            )

            if gems:
                # Send individual cards
                for i, gem in enumerate(gems[:5], 1):
                    if filters.get("kid_friendly") and not gem.get("kid_friendly"):
                        continue
                    try:
                        await context.bot.send_message(
                            chat_id=update.effective_chat.id,
                            text=format_camp_card(gem, i),
                            parse_mode=ParseMode.MARKDOWN,
                            disable_web_page_preview=True,
                        )
                    except Exception as e:
                        logger.warning(f"Card send error #{i}: {e}")
                        await context.bot.send_message(
                            chat_id=update.effective_chat.id,
                            text=f"#{i} *{gem['name']}* — {gem.get('gem_score')}/100\n{gem.get('why_its_special', '')}",
                            parse_mode=ParseMode.MARKDOWN,
                        )
            else:
                # Fallback: send the synthesized text response
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=results_text or "No matches found. Try /start with different filters!",
                )

            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="🔄 Search again? Tap /start\n📈 See all-time top gems? Tap /top",
            )

        except Exception as e:
            logger.error(f"Search error: {e}")
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="⚠️ Something went wrong. Try /start again or /top for instant results.",
            )
        return

    # Still in wizard — edit message in place with next question
    try:
        await query.edit_message_text(
            step_text(next_state),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=build_keyboard(next_state),
        )
    except Exception as e:
        logger.warning(f"Edit message error: {e}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=step_text(next_state),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=build_keyboard(next_state),
        )


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    if not TELEGRAM_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN not set in .env")
        print("Get one from @BotFather on Telegram.")
        sys.exit(1)

    if not os.path.exists(DB_PATH):
        print(f"ERROR: Database not found at {DB_PATH}")
        print("Run: python main.py --init && python main.py --seed")
        sys.exit(1)

    print("🏕️  PNW Camp Scout bot starting...")
    print(f"    DB: {DB_PATH}")
    print("    Send /start in Telegram to begin.")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("top", cmd_top))
    app.add_handler(CommandHandler("buzz", cmd_buzz))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CallbackQueryHandler(handle_wizard_button))

    print("    Bot is live. Ctrl+C to stop.\n")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
