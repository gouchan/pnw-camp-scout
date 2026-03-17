"""Classifier Agent — uses Claude to assign gem scores and extract structured profiles.

Takes raw campsite data + social intelligence and returns a structured gem profile.
"""

import json
import os
import sqlite3
import math
from datetime import datetime
from typing import Optional

import anthropic


MODEL = "claude-sonnet-4-6"
LANDMARKS_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "seeds", "landmarks.json")


def _load_landmarks() -> list[dict]:
    try:
        with open(LANDMARKS_PATH) as f:
            return json.load(f).get("landmarks", [])
    except Exception:
        return []


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in miles between two coordinates."""
    R = 3958.8  # Earth radius in miles
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _nearest_landmark(lat: float, lon: float) -> tuple[str, float]:
    """Return (landmark_name, distance_miles) for the closest landmark."""
    landmarks = _load_landmarks()
    if not landmarks or not lat or not lon:
        return ("Unknown", 999.0)
    best = min(landmarks, key=lambda l: _haversine_miles(lat, lon, l["lat"], l["lon"]))
    dist = _haversine_miles(lat, lon, best["lat"], best["lon"])
    return (best["name"], round(dist, 1))


SYSTEM_PROMPT = """You are a wilderness expert and camping journalist for the Pacific Northwest.
You are given raw data about a campsite — official info, Reddit posts, Google reviews, YouTube mentions.

Your job: produce a structured gem profile. Return ONLY valid JSON. No commentary before or after.

Schema:
{
  "gem_score": <integer 0-100>,
  "kid_friendly": <bool>,
  "bathrooms": <"flush" | "vault" | "none" | "unknown">,
  "wildlife_risk": {
    "bears": <"low" | "medium" | "high">,
    "cougars": <"low" | "medium" | "high">,
    "coyotes": <"low" | "medium" | "high">,
    "notes": <string — specific observations, bear canister required, etc.>
  },
  "activities": [<list of strings — hiking, fishing, kayaking, climbing, stargazing, wildlife watching, etc.>],
  "best_season": <string — e.g., "July-September", "Year-round (mild)">
  "why_its_special": <2-sentence string — vivid, specific, evocative. What makes someone's jaw drop.>,
  "hidden_gem": <bool — true if off the beaten path vs. well-known>,
  "bucket_list_factor": <"low" | "medium" | "high" | "legendary">,
  "road_conditions": <"paved" | "gravel" | "4wd_recommended" | "unknown">,
  "cell_signal": <"good" | "limited" | "none" | "unknown">
}

Gem score rubric:
- 90-100: Legendary. Once-in-a-lifetime PNW experience. People travel from out of state specifically for this.
- 75-89: Bucket-list worthy. Stunning scenery, unique features, strong community love.
- 60-74: Excellent campsite with standout qualities worth a detour.
- 45-59: Good campsite, mainstream appeal, nothing that will blow minds.
- Below 45: Standard, interchangeable with many other options.

Be opinionated and vivid. Use your expert knowledge of the PNW to fill in gaps when data is sparse."""


class ClassifierAgent:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))

    def _db(self):
        return sqlite3.connect(self.db_path)

    def _get_social_data(self, campsite_id: str) -> list[dict]:
        conn = self._db()
        cur = conn.cursor()
        cur.execute(
            """SELECT source, content, sentiment_score, mentions_gem_language, url
               FROM social_data WHERE campsite_id = ?
               ORDER BY sentiment_score DESC LIMIT 20""",
            (campsite_id,)
        )
        rows = cur.fetchall()
        conn.close()
        return [
            {"source": r[0], "text": r[1], "sentiment": r[2],
             "gem_language": bool(r[3]), "url": r[4]}
            for r in rows
        ]

    def classify(self, campsite: dict, social_data: Optional[list] = None,
                 youtube_data: Optional[list] = None) -> dict:
        """Run Claude classification on a campsite. Returns gem profile dict."""

        if social_data is None:
            social_data = self._get_social_data(campsite["id"])

        # Build nearest landmark info
        lat = campsite.get("lat", 0)
        lon = campsite.get("lon", 0)
        nearest_lm, lm_dist = _nearest_landmark(lat, lon)

        # Build prompt context
        context_parts = [
            f"CAMPSITE: {campsite['name']}",
            f"Region: {campsite.get('region', 'PNW')}",
            f"Type: {campsite.get('facility_type', 'unknown')}",
            f"Lat/Lon: {lat}, {lon}",
            f"Nearest landmark: {nearest_lm} ({lm_dist} miles)",
        ]

        if campsite.get("description"):
            context_parts.append(f"\nOFFICIAL DESCRIPTION:\n{campsite['description']}")

        if social_data:
            gem_posts = [s for s in social_data if s.get("gem_language")]
            reddit_posts = [s for s in social_data if s["source"] == "reddit"]
            google_reviews = [s for s in social_data if s["source"] == "google"]

            if reddit_posts:
                context_parts.append(f"\nREDDIT MENTIONS ({len(reddit_posts)} posts found):")
                for p in reddit_posts[:5]:
                    context_parts.append(f"  [{p['source']}] sentiment={p['sentiment']:.2f}: {p['text'][:300]}")

            if google_reviews:
                context_parts.append(f"\nGOOGLE REVIEWS ({len(google_reviews)} found):")
                for r in google_reviews[:5]:
                    context_parts.append(f"  {r['text'][:200]}")

            if gem_posts:
                context_parts.append(f"\nGEM LANGUAGE DETECTED in {len(gem_posts)} posts")

        if youtube_data:
            context_parts.append(f"\nYOUTUBE: {len(youtube_data)} videos found")
            for v in youtube_data[:3]:
                context_parts.append(f"  '{v['title']}' — {v['view_count']:,} views")

        # Seed data unique features
        if campsite.get("unique_features"):
            context_parts.append(f"\nKNOWN UNIQUE FEATURES: {', '.join(campsite.get('unique_features', []))}")
        if campsite.get("bucket_list"):
            context_parts.append("FLAGGED AS BUCKET-LIST by curator")

        user_message = "\n".join(context_parts)

        try:
            message = self.client.messages.create(
                model=MODEL,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}]
            )
            raw = message.content[0].text.strip()
            # Strip markdown code fences if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            profile = json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"[Classifier] JSON parse error for {campsite['name']}: {e}")
            profile = self._fallback_profile(campsite)
        except Exception as e:
            print(f"[Classifier] Claude error for {campsite['name']}: {e}")
            profile = self._fallback_profile(campsite)

        profile["nearest_landmark"] = nearest_lm
        profile["landmark_distance_miles"] = lm_dist

        return profile

    def _fallback_profile(self, campsite: dict) -> dict:
        """Minimal profile when Claude is unavailable."""
        return {
            "gem_score": 50,
            "kid_friendly": None,
            "bathrooms": "unknown",
            "wildlife_risk": {"bears": "unknown", "cougars": "unknown",
                              "coyotes": "unknown", "notes": ""},
            "activities": [],
            "best_season": "Unknown",
            "why_its_special": f"{campsite['name']} is a campsite in the Pacific Northwest.",
            "hidden_gem": False,
            "bucket_list_factor": "medium",
            "road_conditions": "unknown",
            "cell_signal": "unknown",
        }

    def save_profile(self, campsite_id: str, profile: dict):
        """Persist gem profile to database."""
        conn = self._db()
        conn.execute(
            """INSERT OR REPLACE INTO gem_profiles
               (campsite_id, gem_score, kid_friendly, wildlife_risk_json, activities_json,
                best_season, why_its_special, hidden_gem, bucket_list_factor,
                nearest_landmark, landmark_distance_miles, road_conditions, cell_signal, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                campsite_id,
                profile.get("gem_score"),
                profile.get("kid_friendly"),
                json.dumps(profile.get("wildlife_risk", {})),
                json.dumps(profile.get("activities", [])),
                profile.get("best_season"),
                profile.get("why_its_special"),
                profile.get("hidden_gem"),
                profile.get("bucket_list_factor"),
                profile.get("nearest_landmark"),
                profile.get("landmark_distance_miles"),
                profile.get("road_conditions"),
                profile.get("cell_signal"),
                datetime.now().isoformat(),
            )
        )
        conn.commit()
        conn.close()

    def get_top_gems(self, region: Optional[str] = None,
                     min_score: int = 70, limit: int = 10) -> list[dict]:
        """Fetch top gem-scored campsites from the database."""
        conn = self._db()
        cur = conn.cursor()

        query = """
            SELECT c.id, c.name, c.region, c.lat, c.lon, c.facility_type,
                   c.reservation_url, g.gem_score, g.kid_friendly,
                   g.wildlife_risk_json, g.activities_json, g.best_season,
                   g.why_its_special, g.hidden_gem, g.bucket_list_factor,
                   g.nearest_landmark, g.landmark_distance_miles,
                   g.road_conditions, g.cell_signal
            FROM campsites c
            JOIN gem_profiles g ON c.id = g.campsite_id
            WHERE g.gem_score >= ?
        """
        params = [min_score]

        if region:
            query += " AND c.region = ?"
            params.append(region)

        query += " ORDER BY g.gem_score DESC LIMIT ?"
        params.append(limit)

        cur.execute(query, params)
        rows = cur.fetchall()
        conn.close()

        cols = ["id", "name", "region", "lat", "lon", "facility_type", "reservation_url",
                "gem_score", "kid_friendly", "wildlife_risk_json", "activities_json",
                "best_season", "why_its_special", "hidden_gem", "bucket_list_factor",
                "nearest_landmark", "landmark_distance_miles", "road_conditions", "cell_signal"]

        results = []
        for row in rows:
            item = dict(zip(cols, row))
            item["wildlife_risk"] = json.loads(item.pop("wildlife_risk_json") or "{}")
            item["activities"] = json.loads(item.pop("activities_json") or "[]")
            results.append(item)
        return results
