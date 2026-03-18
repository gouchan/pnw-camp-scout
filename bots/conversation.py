"""Wizard state machine — 8-step guided filter conversation.

Each step presents inline keyboard buttons. State is stored per user_id in memory.
After step 8, filters are passed to the orchestrator.
"""

from dataclasses import dataclass, field
from typing import Optional
from datetime import date, timedelta


# ─── Step definitions ────────────────────────────────────────────────────────

STEPS = [
    "group_size",
    "dogs",
    "when",
    "scenery",   # includes "surprise me" shortcut
    "water",
    "hiking",
    "camp_style",
    "kids",
]

STEP_QUESTIONS = {
    "group_size": "🏕️ Welcome to PNW Camp Scout!\n\nLet's find your perfect camping spot. First — *how many people total?*",
    "dogs":       "🐾 *Bringing any dogs or pets?*",
    "when":       "📅 *When are you thinking?*",
    "scenery":    "🌄 *What kind of scenery calls to you?*\n_(Tap 🎲 to skip filters and get a surprise pick!)_",
    "water":      "💧 *Any preference on water nearby?*",
    "hiking":     "🥾 *Want hiking trails nearby?*",
    "camp_style": "⛺ *What's your camping style?*",
    "kids":       "👶 *Kids in the group?*",
}

STEP_BUTTONS = {
    "group_size": [
        [("👫 Just us (1–2)", "gs_2"), ("👨‍👩‍👧 Small group (3–6)", "gs_6")],
        [("👨‍👩‍👧‍👦 Large family (7–15)", "gs_15"), ("🏘️ Big reunion (15+)", "gs_20")],
    ],
    "dogs": [
        [("🐕 Yes, dogs coming!", "dogs_yes"), ("🚫 No pets", "dogs_no")],
    ],
    "when": [
        [("📅 This weekend", "when_weekend"), ("📆 Next 2 weeks", "when_2w")],
        [("🗓️ Next month", "when_1m"), ("✨ I'm flexible", "when_flex")],
    ],
    "scenery": [
        [("🌊 Beach / Ocean", "scene_ocean"), ("🌲 Old Growth Forest", "scene_forest")],
        [("🏔️ Mountains / Alpine", "scene_alpine"), ("🏜️ High Desert", "scene_desert")],
        [("🎲 Surprise me!", "scene_lucky")],
    ],
    "water": [
        [("🏊 Swimming lake", "water_lake"), ("🌊 Ocean beach", "water_ocean")],
        [("🚣 River / creek", "water_river"), ("🔥 No preference", "water_none")],
    ],
    "hiking": [
        [("🥾 Yes, trails please!", "hike_yes"), ("😌 Doesn't matter", "hike_no")],
    ],
    "camp_style": [
        [("🚽 Full campground\n(bathrooms + fire rings)", "style_full")],
        [("🔥 Primitive / dispersed\n(raw, no facilities)", "style_dispersed")],
        [("🤷 Either works for me", "style_any")],
    ],
    "kids": [
        [("👶 Yes — kid-friendly please", "kids_yes"), ("🧑 No kids", "kids_no")],
        [("👍 Either is fine", "kids_any")],
    ],
}


# ─── Session state ────────────────────────────────────────────────────────────

@dataclass
class CampSession:
    user_id: int
    state: str = "group_size"
    filters: dict = field(default_factory=dict)
    wizard_message_id: Optional[int] = None  # for edit-in-place
    done: bool = False


# In-memory session store (keyed by user_id)
_sessions: dict[int, CampSession] = {}


def get_session(user_id: int) -> CampSession:
    if user_id not in _sessions:
        _sessions[user_id] = CampSession(user_id=user_id)
    return _sessions[user_id]


def reset_session(user_id: int) -> CampSession:
    _sessions[user_id] = CampSession(user_id=user_id)
    return _sessions[user_id]


# ─── Answer handling ─────────────────────────────────────────────────────────

def handle_answer(session: CampSession, callback_data: str) -> tuple[str, bool]:
    """
    Process a button press. Updates session.filters and advances state.
    Returns (next_step_or_done, is_lucky_roll).
    """
    step = session.state
    is_lucky = False

    if step == "group_size":
        size_map = {"gs_2": 2, "gs_6": 6, "gs_15": 15, "gs_20": 20}
        session.filters["group_size"] = size_map.get(callback_data, 4)
        session.filters["large_group"] = session.filters["group_size"] >= 7

    elif step == "dogs":
        session.filters["pet_friendly"] = callback_data == "dogs_yes"

    elif step == "when":
        today = date.today()
        if callback_data == "when_weekend":
            days_until_sat = (5 - today.weekday()) % 7 or 7
            sat = today + timedelta(days=days_until_sat)
            session.filters["dates"] = {
                "start": str(sat), "end": str(sat + timedelta(days=1)),
                "this_weekend": True, "flexible": False,
            }
        elif callback_data == "when_2w":
            session.filters["dates"] = {
                "start": str(today + timedelta(days=7)),
                "end": str(today + timedelta(days=14)),
                "this_weekend": False, "flexible": False,
            }
        elif callback_data == "when_1m":
            session.filters["dates"] = {
                "start": str(today + timedelta(days=30)),
                "end": str(today + timedelta(days=37)),
                "this_weekend": False, "flexible": False,
            }
        else:
            session.filters["dates"] = {"flexible": True}

    elif step == "scenery":
        if callback_data == "scene_lucky":
            is_lucky = True
            session.done = True
            return "done", True
        vibe_map = {
            "scene_ocean": ["beach", "coastal"],
            "scene_forest": ["forest", "old_growth"],
            "scene_alpine": ["alpine", "mountains"],
            "scene_desert": ["desert", "eastern"],
        }
        session.filters["vibe"] = vibe_map.get(callback_data, [])
        region_map = {
            "scene_ocean": ["coastal_wa", "coastal_or", "olympic_peninsula"],
            "scene_forest": ["olympic_peninsula", "cascades_wa", "cascades_or"],
            "scene_alpine": ["cascades_wa", "cascades_or", "mt_hood"],
            "scene_desert": ["eastern_wa", "eastern_or"],
        }
        session.filters["regions"] = region_map.get(callback_data, [])

    elif step == "water":
        water_map = {
            "water_lake": "lake",
            "water_ocean": "ocean",
            "water_river": "river",
            "water_none": None,
        }
        session.filters["water_type"] = water_map.get(callback_data)

    elif step == "hiking":
        session.filters["needs_hiking"] = callback_data == "hike_yes"

    elif step == "camp_style":
        session.filters["dispersed_only"] = callback_data == "style_dispersed"
        session.filters["camp_style"] = callback_data

    elif step == "kids":
        if callback_data == "kids_yes":
            session.filters["kid_friendly"] = True
        elif callback_data == "kids_no":
            session.filters["kid_friendly"] = False
        else:
            session.filters["kid_friendly"] = None

    # Advance to next step
    current_index = STEPS.index(step)
    if current_index + 1 >= len(STEPS):
        session.done = True
        session.state = "done"
        return "done", False
    else:
        session.state = STEPS[current_index + 1]
        return session.state, False


# ─── Natural language query builder ─────────────────────────────────────────

def build_query(filters: dict) -> str:
    """Convert wizard filters dict → natural language query string for orchestrator."""
    parts = []

    group = filters.get("group_size", 2)
    if group >= 15:
        parts.append(f"large family reunion of {group}+ people")
    elif group >= 7:
        parts.append(f"large family group of {group} people")
    elif group >= 3:
        parts.append(f"small group of {group}")
    else:
        parts.append("couple or solo")

    if filters.get("pet_friendly"):
        parts.append("dog-friendly")

    vibe = filters.get("vibe", [])
    if "beach" in vibe or "coastal" in vibe:
        parts.append("coastal beach camping")
    elif "forest" in vibe or "old_growth" in vibe:
        parts.append("old growth forest camping")
    elif "alpine" in vibe or "mountains" in vibe:
        parts.append("mountain alpine camping")
    elif "desert" in vibe or "eastern" in vibe:
        parts.append("eastern high desert camping")

    water = filters.get("water_type")
    if water == "lake":
        parts.append("with swimming lake")
    elif water == "ocean":
        parts.append("on the ocean")
    elif water == "river":
        parts.append("near river or creek")

    if filters.get("needs_hiking"):
        parts.append("hiking trails nearby")

    if filters.get("kid_friendly"):
        parts.append("kid-friendly")
    elif filters.get("kid_friendly") is False:
        parts.append("adults only")

    if filters.get("dispersed_only"):
        parts.append("dispersed primitive camping")

    dates = filters.get("dates", {})
    if dates.get("this_weekend"):
        parts.append("this weekend")

    return " ".join(parts) if parts else "best PNW camping gems"
