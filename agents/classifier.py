"""Classifier Agent — uses Claude to assign gem scores and extract structured profiles."""

import json
import os
import sqlite3
import math
import yaml
from datetime import datetime
from typing import Optional

import anthropic


MODEL = "claude-sonnet-4-6"
LANDMARKS_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "seeds", "landmarks.json")
PROMPTS_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "prompts.yaml")


def _load_landmarks() -> list[dict]:
    try:
        with open(LANDMARKS_PATH) as f:
            return json.load(f).get("landmarks", [])
    except Exception:
        return []


def _load_system_prompt() -> str:
    try:
        with open(PROMPTS_PATH) as f:
            prompts = yaml.safe_load(f)
        return prompts.get("classifier_gem_score", {}).get("system", "")
    except Exception:
        return ""


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 3958.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _nearest_landmark(lat: float, lon: float) -> tuple[str, float]:
    landmarks = _load_landmarks()
    if not landmarks or not lat or not lon:
        return ("Unknown", 999.0)
    best = min(landmarks, key=lambda l: _haversine_miles(lat, lon, l["lat"], l["lon"]))
    dist = _haversine_miles(lat, lon, best["lat"], best["lon"])
    return (best["name"], round(dist, 1))


# New columns — used for migration on existing DBs
NEW_COLUMNS = [
    ("pet_friendly", "BOOLEAN"),
    ("dogs_on_leash_ok", "BOOLEAN"),
    ("water_nearby_type", "TEXT"),
    ("water_swimmable", "BOOLEAN"),
    ("hiking_trails_nearby", "BOOLEAN"),
    ("hiking_trail_notes", "TEXT"),
    ("group_max_size", "INTEGER"),
    ("has_group_sites", "BOOLEAN"),
]


class ClassifierAgent:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
        self._run_migrations()

    def _db(self):
        return sqlite3.connect(self.db_path)

    def _run_migrations(self):
        """Add any missing columns to gem_profiles for existing databases."""
        conn = self._db()
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(gem_profiles)")
        existing = {row[1] for row in cur.fetchall()}
        for col_name, col_type in NEW_COLUMNS:
            if col_name not in existing:
                try:
                    conn.execute(f"ALTER TABLE gem_profiles ADD COLUMN {col_name} {col_type}")
                    conn.commit()
                except Exception:
                    pass
        conn.close()

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
        """Run Claude classification. Returns full gem profile dict."""
        if social_data is None:
            social_data = self._get_social_data(campsite["id"])

        lat = campsite.get("lat", 0)
        lon = campsite.get("lon", 0)
        nearest_lm, lm_dist = _nearest_landmark(lat, lon)

        context_parts = [
            f"CAMPSITE: {campsite['name']}",
            f"Region: {campsite.get('region', 'PNW')}",
            f"Type: {campsite.get('facility_type', 'unknown')}",
            f"Coordinates: {lat}, {lon}",
            f"Nearest landmark: {nearest_lm} ({lm_dist} miles)",
        ]

        if campsite.get("description"):
            context_parts.append(f"\nOFFICIAL DESCRIPTION:\n{campsite['description']}")

        # Seed-level known fields
        known = []
        if campsite.get("pet_friendly") is not None:
            known.append(f"Pet-friendly: {campsite['pet_friendly']}")
        if campsite.get("water_nearby"):
            known.append(f"Water nearby: {campsite['water_nearby']}")
            known.append(f"Swimmable: {campsite.get('water_swimmable', 'unknown')}")
        if campsite.get("hiking_trails") is not None:
            known.append(f"Hiking trails: {campsite['hiking_trails']}")
        if campsite.get("max_group_size"):
            known.append(f"Max group size: {campsite['max_group_size']}")
        if known:
            context_parts.append("\nKNOWN DETAILS:\n" + "\n".join(f"  {k}" for k in known))

        if campsite.get("unique_features"):
            context_parts.append(f"\nUNIQUE FEATURES: {', '.join(campsite['unique_features'])}")
        if campsite.get("bucket_list"):
            context_parts.append("CURATOR FLAG: bucket-list worthy")

        if social_data:
            reddit_posts = [s for s in social_data if s["source"] == "reddit"]
            google_reviews = [s for s in social_data if s["source"] == "google"]
            gem_count = sum(1 for s in social_data if s.get("gem_language"))
            if reddit_posts:
                context_parts.append(f"\nREDDIT ({len(reddit_posts)} posts):")
                for p in reddit_posts[:5]:
                    context_parts.append(f"  sentiment={p['sentiment']:.2f}: {p['text'][:300]}")
            if google_reviews:
                context_parts.append(f"\nGOOGLE REVIEWS ({len(google_reviews)}):")
                for r in google_reviews[:4]:
                    context_parts.append(f"  {r['text'][:200]}")
            if gem_count:
                context_parts.append(f"\nGEM LANGUAGE detected in {gem_count} posts")

        if youtube_data:
            context_parts.append(f"\nYOUTUBE: {len(youtube_data)} videos")
            for v in youtube_data[:3]:
                context_parts.append(f"  '{v['title']}' — {v['view_count']:,} views")

        system = _load_system_prompt()
        user_message = "\n".join(context_parts)

        try:
            message = self.client.messages.create(
                model=MODEL,
                max_tokens=1200,
                system=system,
                messages=[{"role": "user", "content": user_message}]
            )
            raw = message.content[0].text.strip()
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
        """Minimal profile when Claude is unavailable — uses seed data where possible."""
        return {
            "gem_score": 50,
            "kid_friendly": True,
            "bathrooms": "unknown",
            "wildlife_risk": {"bears": "low", "cougars": "low", "coyotes": "low",
                              "rattlesnakes": "low", "notes": ""},
            "activities": [],
            "best_season": campsite.get("best_season", "Unknown"),
            "why_its_special": campsite.get("notes", f"{campsite['name']} is a campsite in the PNW."),
            "hidden_gem": False,
            "bucket_list_factor": "medium",
            "road_conditions": "unknown",
            "cell_signal": "unknown",
            "pet_friendly": campsite.get("pet_friendly", True),
            "dogs_on_leash_ok": campsite.get("dogs_on_leash_ok", True),
            "water_nearby_type": campsite.get("water_nearby", "none"),
            "water_swimmable": campsite.get("water_swimmable", False),
            "hiking_trails_nearby": campsite.get("hiking_trails", False),
            "hiking_trail_notes": "",
            "group_max_size": campsite.get("max_group_size", 8),
            "has_group_sites": campsite.get("has_group_sites", False),
        }

    def save_profile(self, campsite_id: str, profile: dict):
        """Persist full gem profile to database."""
        conn = self._db()
        conn.execute(
            """INSERT OR REPLACE INTO gem_profiles
               (campsite_id, gem_score, kid_friendly, wildlife_risk_json, activities_json,
                best_season, why_its_special, hidden_gem, bucket_list_factor,
                nearest_landmark, landmark_distance_miles, road_conditions, cell_signal,
                pet_friendly, dogs_on_leash_ok, water_nearby_type, water_swimmable,
                hiking_trails_nearby, hiking_trail_notes, group_max_size, has_group_sites,
                updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                profile.get("pet_friendly"),
                profile.get("dogs_on_leash_ok"),
                profile.get("water_nearby_type"),
                profile.get("water_swimmable"),
                profile.get("hiking_trails_nearby"),
                profile.get("hiking_trail_notes", ""),
                profile.get("group_max_size"),
                profile.get("has_group_sites"),
                datetime.now().isoformat(),
            )
        )
        conn.commit()
        conn.close()

    def get_top_gems(self, region: Optional[str] = None, min_score: int = 70,
                     limit: int = 10, pet_friendly: Optional[bool] = None,
                     water_type: Optional[str] = None, needs_hiking: Optional[bool] = None,
                     min_group_size: Optional[int] = None) -> list[dict]:
        """Fetch top gem-scored campsites with optional filters."""
        conn = self._db()
        cur = conn.cursor()

        query = """
            SELECT c.id, c.name, c.region, c.lat, c.lon, c.facility_type,
                   c.reservation_url, g.gem_score, g.kid_friendly,
                   g.wildlife_risk_json, g.activities_json, g.best_season,
                   g.why_its_special, g.hidden_gem, g.bucket_list_factor,
                   g.nearest_landmark, g.landmark_distance_miles,
                   g.road_conditions, g.cell_signal,
                   g.pet_friendly, g.dogs_on_leash_ok, g.water_nearby_type,
                   g.water_swimmable, g.hiking_trails_nearby, g.hiking_trail_notes,
                   g.group_max_size, g.has_group_sites
            FROM campsites c
            JOIN gem_profiles g ON c.id = g.campsite_id
            WHERE g.gem_score >= ?
        """
        params: list = [min_score]

        if region:
            query += " AND c.region = ?"
            params.append(region)
        if pet_friendly:
            query += " AND g.pet_friendly = 1"
        if water_type:
            query += " AND g.water_nearby_type = ?"
            params.append(water_type)
        if needs_hiking:
            query += " AND g.hiking_trails_nearby = 1"
        if min_group_size and min_group_size > 6:
            query += " AND (g.group_max_size >= ? OR g.has_group_sites = 1)"
            params.append(min_group_size)

        query += " ORDER BY g.gem_score DESC LIMIT ?"
        params.append(limit)

        cur.execute(query, params)
        rows = cur.fetchall()
        conn.close()

        cols = [
            "id", "name", "region", "lat", "lon", "facility_type", "reservation_url",
            "gem_score", "kid_friendly", "wildlife_risk_json", "activities_json",
            "best_season", "why_its_special", "hidden_gem", "bucket_list_factor",
            "nearest_landmark", "landmark_distance_miles", "road_conditions", "cell_signal",
            "pet_friendly", "dogs_on_leash_ok", "water_nearby_type", "water_swimmable",
            "hiking_trails_nearby", "hiking_trail_notes", "group_max_size", "has_group_sites",
        ]

        results = []
        for row in rows:
            item = dict(zip(cols, row))
            item["wildlife_risk"] = json.loads(item.pop("wildlife_risk_json") or "{}")
            item["activities"] = json.loads(item.pop("activities_json") or "[]")
            results.append(item)
        return results

    def get_recently_buzzed(self, limit: int = 5) -> list[dict]:
        """Campsites with the most social activity in the last 7 days."""
        conn = self._db()
        cur = conn.cursor()
        cur.execute(
            """SELECT s.campsite_id, COUNT(*) as mention_count,
                      AVG(s.sentiment_score) as avg_sentiment
               FROM social_data s
               WHERE s.scraped_at > datetime('now', '-7 days')
               GROUP BY s.campsite_id
               ORDER BY mention_count DESC
               LIMIT ?""",
            (limit,)
        )
        buzz_rows = cur.fetchall()
        conn.close()

        if not buzz_rows:
            return []

        ids = [r[0] for r in buzz_rows]
        buzz_map = {r[0]: {"mentions": r[1], "sentiment": r[2]} for r in buzz_rows}

        placeholders = ",".join("?" * len(ids))
        conn = self._db()
        cur = conn.cursor()
        cur.execute(
            f"""SELECT c.id, c.name, c.region, c.reservation_url,
                       g.gem_score, g.why_its_special, g.best_season,
                       g.pet_friendly, g.water_nearby_type, g.hiking_trails_nearby
                FROM campsites c
                LEFT JOIN gem_profiles g ON c.id = g.campsite_id
                WHERE c.id IN ({placeholders})""",
            ids
        )
        rows = cur.fetchall()
        conn.close()

        cols = ["id", "name", "region", "reservation_url", "gem_score",
                "why_its_special", "best_season", "pet_friendly",
                "water_nearby_type", "hiking_trails_nearby"]
        results = []
        for row in rows:
            item = dict(zip(cols, row))
            item.update(buzz_map.get(item["id"], {}))
            results.append(item)

        results.sort(key=lambda x: x.get("mentions", 0), reverse=True)
        return results
