"""Orchestrator — Claude-powered intent router and result aggregator."""

import json
import os
import random
import sqlite3
import yaml
from datetime import date, timedelta
from typing import Optional

import anthropic

from .scout import ScoutAgent
from .social import SocialAgent
from .content import ContentAgent
from .classifier import ClassifierAgent


MODEL = "claude-sonnet-4-6"
PROMPTS_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "prompts.yaml")
REGIONS_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "regions.yaml")

# Full profile columns pulled from a JOIN query
PROFILE_COLS = [
    "gem_score", "kid_friendly", "wildlife_risk_json", "activities_json",
    "best_season", "why_its_special", "hidden_gem", "bucket_list_factor",
    "nearest_landmark", "landmark_distance_miles", "road_conditions", "cell_signal",
    "pet_friendly", "dogs_on_leash_ok", "water_nearby_type", "water_swimmable",
    "hiking_trails_nearby", "hiking_trail_notes", "group_max_size", "has_group_sites",
]


def _load_yaml(path: str) -> dict:
    try:
        with open(path) as f:
            return yaml.safe_load(f)
    except Exception:
        return {}


class Orchestrator:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
        self.scout = ScoutAgent(db_path)
        self.social = SocialAgent(db_path)
        self.content = ContentAgent()
        self.classifier = ClassifierAgent(db_path)
        self.prompts = _load_yaml(PROMPTS_PATH)
        self.regions = _load_yaml(REGIONS_PATH).get("regions", {})

    # ─── Intent parsing ───────────────────────────────────────────────────────

    def parse_intent(self, query: str) -> dict:
        system = self.prompts.get("orchestrator_intent", {}).get("system", "")
        if not system:
            return {"regions": [], "dates": {}, "vibe": [], "constraints": {}, "keywords": []}

        try:
            msg = self.client.messages.create(
                model=MODEL,
                max_tokens=512,
                system=system,
                messages=[{"role": "user", "content": query}]
            )
            raw = msg.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            return json.loads(raw)
        except Exception as e:
            print(f"[Orchestrator] Intent parse error: {e}")
            return {
                "regions": [], "dates": {"flexible": True},
                "vibe": [], "constraints": {}, "keywords": query.split(),
            }

    # ─── Candidate selection ─────────────────────────────────────────────────

    def _get_candidates(self, intent: dict) -> list[dict]:
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()

        regions = intent.get("regions", [])
        constraints = intent.get("constraints", {})

        base_query = """
            SELECT c.id, c.name, c.region, c.lat, c.lon, c.facility_type,
                   c.reservation_url, c.description
            FROM campsites c
        """
        where_clauses = []
        params: list = []

        if regions:
            placeholders = ",".join("?" * len(regions))
            where_clauses.append(f"c.region IN ({placeholders})")
            params.extend(regions)

        if constraints.get("no_reservations") or constraints.get("dispersed_only"):
            where_clauses.append("c.facility_type = 'dispersed'")

        if where_clauses:
            base_query += " WHERE " + " AND ".join(where_clauses)

        cur.execute(base_query, params)
        rows = cur.fetchall()
        conn.close()

        cols = ["id", "name", "region", "lat", "lon", "facility_type", "reservation_url", "description"]
        return [dict(zip(cols, row)) for row in rows]

    # ─── Load cached profile ──────────────────────────────────────────────────

    def _load_cached_profile(self, campsite_id: str) -> Optional[dict]:
        """Return existing gem profile from DB, or None if not scored yet."""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        select_cols = ", ".join(f"g.{c}" for c in PROFILE_COLS)
        cur.execute(
            f"SELECT {select_cols} FROM gem_profiles g WHERE g.campsite_id = ?",
            (campsite_id,)
        )
        row = cur.fetchone()
        conn.close()
        if not row:
            return None
        profile = dict(zip(PROFILE_COLS, row))
        profile["wildlife_risk"] = json.loads(profile.pop("wildlife_risk_json") or "{}")
        profile["activities"] = json.loads(profile.pop("activities_json") or "[]")
        return profile

    # ─── Enrichment ──────────────────────────────────────────────────────────

    def _enrich_and_score(self, campsite: dict) -> dict:
        """Social + content → classify. Returns profile dict."""
        cached = self._load_cached_profile(campsite["id"])
        if cached:
            return cached

        self.social.enrich_campsite(campsite)
        content_result = self.content.enrich_campsite(campsite)
        social_data = self.classifier._get_social_data(campsite["id"])
        profile = self.classifier.classify(
            campsite,
            social_data=social_data,
            youtube_data=content_result.get("youtube_videos", [])
        )
        self.classifier.save_profile(campsite["id"], profile)
        return profile

    # ─── Availability ─────────────────────────────────────────────────────────

    def _check_availability(self, campsite: dict, intent: dict) -> dict:
        dates_intent = intent.get("dates", {})
        check_dates = []

        if dates_intent.get("this_weekend"):
            today = date.today()
            days_until_sat = (5 - today.weekday()) % 7 or 7
            sat = today + timedelta(days=days_until_sat)
            check_dates = [sat, sat + timedelta(days=1)]
        elif dates_intent.get("start"):
            try:
                start = date.fromisoformat(dates_intent["start"])
                end = date.fromisoformat(dates_intent.get("end", dates_intent["start"]))
                delta = (end - start).days + 1
                check_dates = [start + timedelta(days=i) for i in range(min(delta, 7))]
            except Exception:
                pass

        if not check_dates:
            today = date.today()
            check_dates = [today + timedelta(days=i) for i in [7, 14, 21]]

        return {
            "dates_checked": [str(d) for d in check_dates],
            "status": "check recreation.gov for live availability",
            "url": campsite.get("reservation_url", ""),
        }

    # ─── Post-score filtering ─────────────────────────────────────────────────

    def _apply_filters(self, scored: list[dict], constraints: dict) -> list[dict]:
        """Filter scored results by hard constraints."""
        results = scored

        if constraints.get("pet_friendly"):
            results = [s for s in results if s.get("pet_friendly") is not False]

        if constraints.get("kid_friendly"):
            results = [s for s in results if s.get("kid_friendly") is not False]

        water_type = constraints.get("water_type")
        if water_type:
            results = [s for s in results if s.get("water_nearby_type") == water_type]

        if constraints.get("needs_hiking"):
            results = [s for s in results if s.get("hiking_trails_nearby") is not False]

        group_size = constraints.get("group_size") or 0
        if group_size >= 7:
            results = [
                s for s in results
                if (s.get("group_max_size") or 0) >= group_size or s.get("has_group_sites")
            ]

        return results

    # ─── Response synthesis ───────────────────────────────────────────────────

    def _synthesize_response(self, query: str, results: list[dict]) -> str:
        if not results:
            return (
                "No exact matches — but here's what I'd try: broaden the region or drop one filter. "
                "The PNW has something for everyone. Try: 'dog-friendly lake camping PNW' or 'large group coastal Washington'."
            )

        synthesis_prompt = self.prompts.get("classifier_synthesis", {}).get("system", "")
        if not synthesis_prompt:
            return self._format_results_text(results)

        system = synthesis_prompt.replace("{query}", query)

        sites_text = []
        for i, r in enumerate(results[:5], 1):
            dogs = "✅" if r.get("pet_friendly") else "❌"
            swim = "✅" if r.get("water_swimmable") else "—"
            hike = "✅" if r.get("hiking_trails_nearby") else "—"
            lines = [
                f"{i}. {r['name']} — {r.get('gem_score','?')}/100 | {r.get('bucket_list_factor','').upper()}",
                f"   {r.get('why_its_special', '')}",
                f"   Season: {r.get('best_season','?')} | Road: {r.get('road_conditions','?')} | Cell: {r.get('cell_signal','?')}",
                f"   Dogs: {dogs} | Water: {r.get('water_nearby_type','none')} (swim: {swim}) | Hiking: {hike}",
                f"   Group max: {r.get('group_max_size','?')} | Group sites: {'Yes' if r.get('has_group_sites') else 'No'}",
                f"   Wildlife: {json.dumps(r.get('wildlife_risk', {}))}",
                f"   Trail notes: {r.get('hiking_trail_notes', '—')}",
                f"   Book: {r.get('reservation_url', 'No reservation needed')}",
            ]
            sites_text.append("\n".join(lines))

        user_msg = f"Query: {query}\n\nTop results:\n\n" + "\n\n".join(sites_text)

        try:
            msg = self.client.messages.create(
                model=MODEL,
                max_tokens=2048,
                system=system,
                messages=[{"role": "user", "content": user_msg}]
            )
            return msg.content[0].text.strip()
        except Exception as e:
            print(f"[Orchestrator] Synthesis error: {e}")
            return self._format_results_text(results)

    def _format_results_text(self, results: list[dict]) -> str:
        lines = ["🏕️ TOP PNW CAMPING GEMS", "─" * 40]
        for i, r in enumerate(results[:5], 1):
            lines.append(f"\n{i}. {r['name']} — {r.get('gem_score','?')}/100")
            lines.append(f"   {r.get('why_its_special', '')}")
            if r.get("reservation_url"):
                lines.append(f"   Book: {r['reservation_url']}")
        return "\n".join(lines)

    # ─── Feeling lucky ────────────────────────────────────────────────────────

    def feeling_lucky(self, filters: Optional[dict] = None) -> Optional[dict]:
        """Return one random top-scored gem matching filters."""
        gems = self.classifier.get_top_gems(
            min_score=75,
            limit=30,
            pet_friendly=filters.get("pet_friendly") if filters else None,
            min_group_size=filters.get("group_size") if filters else None,
        )
        if not gems:
            gems = self.classifier.get_top_gems(min_score=60, limit=20)
        if not gems:
            return None
        # Weighted random by gem score
        weights = [g["gem_score"] for g in gems]
        return random.choices(gems, weights=weights, k=1)[0]

    # ─── Main query ───────────────────────────────────────────────────────────

    def query(self, user_query: str, enrich: bool = True,
              structured_filters: Optional[dict] = None) -> str:
        """
        Main entrypoint. Natural language query → ranked campsite response.

        Args:
            user_query: e.g., "dog-friendly coastal campsite this weekend"
            enrich: run social/content/classifier agents (slower, richer)
            structured_filters: pre-parsed filters from wizard (skips Claude intent parse)
        """
        print(f"[Orchestrator] Query: {user_query}")

        # Intent: use structured filters from wizard OR parse from text
        if structured_filters:
            intent = {
                "regions": structured_filters.get("regions", []),
                "dates": structured_filters.get("dates", {}),
                "vibe": structured_filters.get("vibe", []),
                "constraints": {
                    "pet_friendly": structured_filters.get("pet_friendly"),
                    "kid_friendly": structured_filters.get("kid_friendly"),
                    "water_type": structured_filters.get("water_type"),
                    "needs_hiking": structured_filters.get("needs_hiking"),
                    "group_size": structured_filters.get("group_size"),
                    "no_reservations": structured_filters.get("no_reservations", False),
                },
            }
        else:
            intent = self.parse_intent(user_query)

        print(f"[Orchestrator] Intent: regions={intent.get('regions')}, "
              f"constraints={intent.get('constraints')}")

        # Candidates
        candidates = self._get_candidates(intent)
        if not candidates:
            intent["regions"] = []
            candidates = self._get_candidates(intent)

        print(f"[Orchestrator] {len(candidates)} candidates")

        # Score
        scored = []
        for campsite in candidates:
            if enrich:
                profile = self._enrich_and_score(campsite)
            else:
                profile = self._load_cached_profile(campsite["id"]) or {"gem_score": 0}
            campsite.update(profile)
            if campsite.get("gem_score", 0) > 0:
                scored.append(campsite)

        # Filter
        constraints = intent.get("constraints", {})
        scored = self._apply_filters(scored, constraints)
        scored.sort(key=lambda x: x.get("gem_score", 0), reverse=True)

        # Availability for top 5
        for campsite in scored[:5]:
            campsite["availability_info"] = self._check_availability(campsite, intent)

        return self._synthesize_response(user_query, scored[:5])
