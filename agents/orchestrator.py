"""Orchestrator — Claude-powered intent router and result aggregator.

Parses natural language camping queries → dispatches agents → formats results.
"""

import json
import os
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
        """Use Claude to parse a natural language query into structured intent."""
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
                "regions": [],
                "dates": {"start": None, "end": None, "flexible": True},
                "vibe": [],
                "constraints": {},
                "keywords": query.split(),
            }

    # ─── Candidate selection ─────────────────────────────────────────────────

    def _get_candidates(self, intent: dict) -> list[dict]:
        """Pull candidate campsites from DB matching intent regions."""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()

        regions = intent.get("regions", [])
        constraints = intent.get("constraints", {})

        if regions:
            placeholders = ",".join("?" * len(regions))
            query = f"SELECT id, name, region, lat, lon, facility_type, reservation_url, description FROM campsites WHERE region IN ({placeholders})"
            cur.execute(query, regions)
        else:
            cur.execute("SELECT id, name, region, lat, lon, facility_type, reservation_url, description FROM campsites")

        rows = cur.fetchall()
        conn.close()

        cols = ["id", "name", "region", "lat", "lon", "facility_type", "reservation_url", "description"]
        candidates = [dict(zip(cols, row)) for row in rows]

        # Filter: walk-in only or no-reservation vibes
        if constraints.get("no_reservations"):
            candidates = [c for c in candidates if c["facility_type"] == "dispersed"]

        return candidates

    # ─── Enrichment ──────────────────────────────────────────────────────────

    def _enrich_and_score(self, campsite: dict) -> dict:
        """Run social + content agents, then classify."""
        # Check if already scored recently
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT gem_score, why_its_special, updated_at FROM gem_profiles WHERE campsite_id = ?",
            (campsite["id"],)
        )
        row = cur.fetchone()
        conn.close()

        if row and row[2]:
            # Already classified — return cached profile
            return {"campsite_id": campsite["id"], "gem_score": row[0],
                    "why_its_special": row[1], "cached": True}

        # Run enrichment (social + content in parallel would be ideal with asyncio)
        social_result = self.social.enrich_campsite(campsite)
        content_result = self.content.enrich_campsite(campsite)

        # Classify
        social_data = self.social._get_social_data(campsite["id"]) if hasattr(self.social, '_get_social_data') else []
        profile = self.classifier.classify(
            campsite,
            social_data=social_data,
            youtube_data=content_result.get("youtube_videos", [])
        )
        self.classifier.save_profile(campsite["id"], profile)
        return profile

    # ─── Availability check ───────────────────────────────────────────────────

    def _check_availability(self, campsite: dict, intent: dict) -> dict:
        """Check availability for requested dates."""
        dates_intent = intent.get("dates", {})
        check_dates = []

        if dates_intent.get("this_weekend"):
            today = date.today()
            days_until_sat = (5 - today.weekday()) % 7
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
            # Default: next 30 days, check a few sample dates
            today = date.today()
            check_dates = [today + timedelta(days=i) for i in [7, 14, 21]]

        avail = {}
        # Only check if we have a RIDB ID (stored as part of description or can look up)
        # For now, use the Campflare name-based check as fallback
        avail["dates_checked"] = [str(d) for d in check_dates]
        avail["status"] = "check recreation.gov for live availability"
        return avail

    # ─── Response synthesis ───────────────────────────────────────────────────

    def _synthesize_response(self, query: str, results: list[dict]) -> str:
        """Use Claude to write a conversational, expert response."""
        if not results:
            return "No matching campsites found. Try broadening your search — 'PNW coastal camping' or 'eastern Oregon gems' for example."

        synthesis_prompt = self.prompts.get("classifier_synthesis", {}).get("system", "")
        if not synthesis_prompt:
            # Fallback: structured text
            return self._format_results_text(results)

        system = synthesis_prompt.replace("{query}", query)

        # Build context for Claude
        sites_text = []
        for i, r in enumerate(results[:5], 1):
            site_info = [
                f"{i}. {r['name']} — Gem Score: {r.get('gem_score', '?')}/100",
                f"   Region: {r.get('region', 'PNW')}",
                f"   Why it's special: {r.get('why_its_special', '')}",
                f"   Best season: {r.get('best_season', 'Unknown')}",
                f"   Kid-friendly: {r.get('kid_friendly', 'Unknown')}",
                f"   Bathrooms: {r.get('bathrooms', 'Unknown')}",
                f"   Nearest landmark: {r.get('nearest_landmark', '')} ({r.get('landmark_distance_miles', '?')} mi)",
                f"   Activities: {', '.join(r.get('activities', [])[:5])}",
                f"   Wildlife: {json.dumps(r.get('wildlife_risk', {}))}",
                f"   Road: {r.get('road_conditions', 'unknown')} | Cell: {r.get('cell_signal', 'unknown')}",
                f"   Book: {r.get('reservation_url', 'No reservation needed')}",
            ]
            sites_text.append("\n".join(site_info))

        user_msg = f"Query: {query}\n\nTop campsites:\n\n" + "\n\n".join(sites_text)

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
        """Plain text fallback formatter."""
        lines = ["TOP PNW CAMPING GEMS", "=" * 40]
        for i, r in enumerate(results[:5], 1):
            score = r.get("gem_score", "?")
            lines.append(f"\n{i}. {r['name']} — {score}/100")
            lines.append(f"   {r.get('why_its_special', '')}")
            if r.get("reservation_url"):
                lines.append(f"   Book: {r['reservation_url']}")
        return "\n".join(lines)

    # ─── Main query entrypoint ────────────────────────────────────────────────

    def query(self, user_query: str, enrich: bool = True) -> str:
        """
        Main entrypoint. Takes a natural language query, returns formatted response.

        Args:
            user_query: e.g., "find me a bucket-list coastal campsite this weekend"
            enrich: if True, run social/content/classifier agents (slower but richer)
        """
        print(f"[Orchestrator] Query: {user_query}")

        # 1. Parse intent
        intent = self.parse_intent(user_query)
        print(f"[Orchestrator] Intent: regions={intent.get('regions')}, vibe={intent.get('vibe')}")

        # 2. Get candidates from DB
        candidates = self._get_candidates(intent)
        if not candidates:
            # Fall back to all regions
            intent["regions"] = []
            candidates = self._get_candidates(intent)

        print(f"[Orchestrator] Found {len(candidates)} candidates")

        # 3. Enrich + score (or use cached scores)
        scored = []
        for campsite in candidates:
            if enrich:
                profile = self._enrich_and_score(campsite)
            else:
                # Use existing scores
                conn = sqlite3.connect(self.db_path)
                cur = conn.cursor()
                cur.execute(
                    """SELECT g.gem_score, g.kid_friendly, g.wildlife_risk_json,
                              g.activities_json, g.best_season, g.why_its_special,
                              g.hidden_gem, g.bucket_list_factor, g.nearest_landmark,
                              g.landmark_distance_miles, g.road_conditions, g.cell_signal
                       FROM gem_profiles g WHERE g.campsite_id = ?""",
                    (campsite["id"],)
                )
                row = cur.fetchone()
                conn.close()
                if row:
                    profile = dict(zip([
                        "gem_score", "kid_friendly", "wildlife_risk", "activities",
                        "best_season", "why_its_special", "hidden_gem", "bucket_list_factor",
                        "nearest_landmark", "landmark_distance_miles", "road_conditions", "cell_signal"
                    ], row))
                    profile["wildlife_risk"] = json.loads(profile["wildlife_risk"] or "{}")
                    profile["activities"] = json.loads(profile["activities"] or "[]")
                else:
                    profile = {"gem_score": 0}

            campsite.update(profile)
            if campsite.get("gem_score", 0) > 0:
                scored.append(campsite)

        # 4. Filter by constraints
        constraints = intent.get("constraints", {})
        if constraints.get("kid_friendly"):
            scored = [s for s in scored if s.get("kid_friendly") is not False]

        # 5. Sort by gem score
        scored.sort(key=lambda x: x.get("gem_score", 0), reverse=True)

        # 6. Add availability for top results
        for campsite in scored[:5]:
            campsite["availability_info"] = self._check_availability(campsite, intent)

        # 7. Synthesize response
        return self._synthesize_response(user_query, scored[:5])
