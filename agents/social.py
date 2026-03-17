"""Social Agent — extracts community intelligence from Reddit and Google Places.

Sources:
  - Reddit (PRAW): r/PNWCamping, r/Oregon, r/Washington, r/WildernessBackpacking, r/CampingandHiking
  - Google Places API: ratings, reviews, busy times
  - Basic blog scraping via web search
"""

import os
import re
import sqlite3
import requests
from datetime import datetime
from typing import Optional
from bs4 import BeautifulSoup

try:
    import praw
    PRAW_AVAILABLE = True
except ImportError:
    PRAW_AVAILABLE = False


PNW_SUBREDDITS = [
    "PNWCamping",
    "Oregon",
    "Washington",
    "WildernessBackpacking",
    "CampingandHiking",
    "hiking",
    "PacificCrestTrail",
]

GEM_LANGUAGE = [
    "hidden gem", "underrated", "secret spot", "bucket list",
    "magical", "off the beaten path", "most people don't know",
    "locals only", "go before it gets popular", "blew my mind",
    "best campsite", "absolutely stunning", "highly recommend",
    "can't believe more people", "well-kept secret",
]

GOOGLE_PLACES_BASE = "https://maps.googleapis.com/maps/api/place"


class SocialAgent:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.google_key = os.getenv("GOOGLE_PLACES_API_KEY", "")
        self._reddit = None

    def _db(self):
        return sqlite3.connect(self.db_path)

    def _get_reddit(self):
        if not PRAW_AVAILABLE:
            return None
        if self._reddit is None:
            client_id = os.getenv("REDDIT_CLIENT_ID", "")
            client_secret = os.getenv("REDDIT_CLIENT_SECRET", "")
            user_agent = os.getenv("REDDIT_USER_AGENT", "pnw-camp-scout/1.0")
            if not client_id or not client_secret:
                return None
            self._reddit = praw.Reddit(
                client_id=client_id,
                client_secret=client_secret,
                user_agent=user_agent,
            )
        return self._reddit

    # ─── Sentiment ───────────────────────────────────────────────────────────

    def _sentiment_score(self, text: str) -> float:
        """Simple keyword-based sentiment (-1 to +1). No ML dependency."""
        text_lower = text.lower()
        pos_words = ["amazing", "beautiful", "stunning", "gorgeous", "incredible",
                     "perfect", "loved", "recommend", "favorite", "breathtaking",
                     "magical", "wonderful", "fantastic", "awesome", "paradise"]
        neg_words = ["terrible", "awful", "bad", "dirty", "crowded", "dangerous",
                     "worst", "disappointing", "avoid", "trash", "loud", "noisy"]
        score = 0
        for w in pos_words:
            if w in text_lower:
                score += 0.1
        for w in neg_words:
            if w in text_lower:
                score -= 0.15
        return max(-1.0, min(1.0, score))

    def _has_gem_language(self, text: str) -> bool:
        text_lower = text.lower()
        return any(phrase in text_lower for phrase in GEM_LANGUAGE)

    # ─── Reddit scraping ─────────────────────────────────────────────────────

    def search_reddit(self, campsite_name: str, limit: int = 25) -> list[dict]:
        """Search PNW subreddits for posts mentioning the campsite."""
        reddit = self._get_reddit()
        if not reddit:
            return []

        results = []
        query = f'"{campsite_name}"'

        for subreddit_name in PNW_SUBREDDITS:
            try:
                sub = reddit.subreddit(subreddit_name)
                for post in sub.search(query, limit=limit, sort="relevance", time_filter="all"):
                    text = f"{post.title} {post.selftext}"
                    # Pull top 5 comments for more signal
                    post.comments.replace_more(limit=0)
                    top_comments = [c.body for c in post.comments.list()[:5]]
                    full_text = text + " " + " ".join(top_comments)

                    results.append({
                        "source": "reddit",
                        "subreddit": subreddit_name,
                        "title": post.title,
                        "url": f"https://reddit.com{post.permalink}",
                        "score": post.score,
                        "text": full_text[:2000],
                        "sentiment": self._sentiment_score(full_text),
                        "gem_language": self._has_gem_language(full_text),
                        "created_utc": post.created_utc,
                    })
            except Exception as e:
                print(f"[Social] Reddit {subreddit_name} error: {e}")

        return results

    def get_pnw_top_camping_posts(self, limit: int = 50) -> list[dict]:
        """Pull top all-time posts from r/PNWCamping for gem discovery."""
        reddit = self._get_reddit()
        if not reddit:
            return []

        results = []
        try:
            sub = reddit.subreddit("PNWCamping")
            for post in sub.top(time_filter="all", limit=limit):
                if post.score < 50:
                    continue
                text = f"{post.title} {post.selftext}"
                results.append({
                    "source": "reddit",
                    "subreddit": "PNWCamping",
                    "title": post.title,
                    "url": f"https://reddit.com{post.permalink}",
                    "score": post.score,
                    "text": text[:1500],
                    "sentiment": self._sentiment_score(text),
                    "gem_language": self._has_gem_language(text),
                })
        except Exception as e:
            print(f"[Social] PNWCamping top posts error: {e}")

        return results

    # ─── Google Places ───────────────────────────────────────────────────────

    def find_place(self, name: str, lat: float, lon: float) -> Optional[str]:
        """Find Google Place ID for a campsite by name + coordinates."""
        if not self.google_key:
            return None
        try:
            resp = requests.get(
                f"{GOOGLE_PLACES_BASE}/findplacefromtext/json",
                params={
                    "input": name,
                    "inputtype": "textquery",
                    "locationbias": f"point:{lat},{lon}",
                    "fields": "place_id,name,rating",
                    "key": self.google_key,
                },
                timeout=10
            )
            resp.raise_for_status()
            data = resp.json()
            candidates = data.get("candidates", [])
            if candidates:
                return candidates[0].get("place_id")
        except Exception as e:
            print(f"[Social] Google find_place error: {e}")
        return None

    def get_place_reviews(self, place_id: str) -> dict:
        """Fetch reviews and details for a Google Place ID."""
        if not self.google_key:
            return {}
        try:
            resp = requests.get(
                f"{GOOGLE_PLACES_BASE}/details/json",
                params={
                    "place_id": place_id,
                    "fields": "name,rating,user_ratings_total,reviews,opening_hours",
                    "key": self.google_key,
                },
                timeout=10
            )
            resp.raise_for_status()
            return resp.json().get("result", {})
        except Exception as e:
            print(f"[Social] Google reviews error: {e}")
        return {}

    # ─── Blog scraping ───────────────────────────────────────────────────────

    def scrape_blog_mentions(self, campsite_name: str, region: str) -> list[dict]:
        """
        Lightweight: search Google for blog posts about the campsite
        and scrape the first paragraph of each result.
        """
        if not self.google_key:
            return []

        query = f'"{campsite_name}" camping {region} site:*.com -site:recreation.gov -site:google.com'
        try:
            resp = requests.get(
                "https://www.googleapis.com/customsearch/v1",
                params={
                    "q": query,
                    "key": self.google_key,
                    "num": 5,
                    "cx": "017576662512468239146:omuauf10dwe",  # placeholder CSE ID
                },
                timeout=10
            )
            if resp.status_code != 200:
                return []
            results = resp.json().get("items", [])
            mentions = []
            for item in results:
                snippet = item.get("snippet", "")
                mentions.append({
                    "source": "blog",
                    "title": item.get("title"),
                    "url": item.get("link"),
                    "text": snippet,
                    "sentiment": self._sentiment_score(snippet),
                    "gem_language": self._has_gem_language(snippet),
                })
            return mentions
        except Exception as e:
            print(f"[Social] Blog scrape error: {e}")
            return []

    # ─── Save to DB ──────────────────────────────────────────────────────────

    def save_social_data(self, campsite_id: str, items: list[dict]):
        """Persist social intelligence to the database."""
        conn = self._db()
        for item in items:
            conn.execute(
                """INSERT INTO social_data
                   (campsite_id, source, content, sentiment_score, mentions_gem_language, url, scraped_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    campsite_id,
                    item.get("source", "unknown"),
                    item.get("text", "")[:3000],
                    item.get("sentiment", 0.0),
                    item.get("gem_language", False),
                    item.get("url"),
                    datetime.now().isoformat(),
                )
            )
        conn.commit()
        conn.close()

    # ─── Main run ────────────────────────────────────────────────────────────

    def enrich_campsite(self, campsite: dict) -> dict:
        """Run all social enrichment for a single campsite. Returns enriched data."""
        name = campsite["name"]
        lat = campsite.get("lat", 0)
        lon = campsite.get("lon", 0)
        region = campsite.get("region", "PNW")

        social_items = []

        # Reddit
        reddit_posts = self.search_reddit(name)
        social_items.extend(reddit_posts)

        # Google Places
        place_id = self.find_place(name, lat, lon)
        google_data = {}
        if place_id:
            google_data = self.get_place_reviews(place_id)
            if google_data.get("reviews"):
                for review in google_data["reviews"]:
                    text = review.get("text", "")
                    social_items.append({
                        "source": "google",
                        "text": text,
                        "sentiment": self._sentiment_score(text),
                        "gem_language": self._has_gem_language(text),
                        "url": None,
                    })

        # Save to DB
        self.save_social_data(campsite["id"], social_items)

        return {
            "campsite_id": campsite["id"],
            "reddit_posts": len(reddit_posts),
            "reddit_avg_sentiment": (
                sum(p["sentiment"] for p in reddit_posts) / len(reddit_posts)
                if reddit_posts else 0
            ),
            "gem_mentions": sum(1 for p in social_items if p.get("gem_language")),
            "google_rating": google_data.get("rating"),
            "google_review_count": google_data.get("user_ratings_total"),
            "google_reviews": google_data.get("reviews", []),
        }
