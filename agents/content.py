"""Content Agent — discovers rich media content about PNW campsites.

Sources:
  - YouTube Data API v3 (high-view/high-comment camping videos)
  - Web search + scrape for blog posts and trip reports
"""

import os
import re
import requests
from typing import Optional
from bs4 import BeautifulSoup


YOUTUBE_SEARCH_BASE = "https://www.googleapis.com/youtube/v3/search"
YOUTUBE_VIDEO_BASE = "https://www.googleapis.com/youtube/v3/videos"

MIN_VIEWS = 10_000
MIN_COMMENTS = 50


class ContentAgent:
    def __init__(self):
        self.yt_key = os.getenv("YOUTUBE_DATA_API_KEY", "")
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "pnw-camp-scout/1.0"})

    # ─── YouTube ─────────────────────────────────────────────────────────────

    def search_youtube(self, campsite_name: str, max_results: int = 10) -> list[dict]:
        """Search YouTube for camping videos about this campsite."""
        if not self.yt_key:
            return []

        queries = [
            f"{campsite_name} camping",
            f"{campsite_name} campground tour",
        ]

        all_video_ids = []
        for query in queries:
            try:
                resp = self.session.get(
                    YOUTUBE_SEARCH_BASE,
                    params={
                        "q": query,
                        "type": "video",
                        "part": "id,snippet",
                        "maxResults": max_results,
                        "relevanceLanguage": "en",
                        "regionCode": "US",
                        "key": self.yt_key,
                    },
                    timeout=10
                )
                resp.raise_for_status()
                items = resp.json().get("items", [])
                all_video_ids.extend(item["id"]["videoId"] for item in items)
            except Exception as e:
                print(f"[Content] YouTube search error: {e}")

        if not all_video_ids:
            return []

        return self._get_video_details(list(set(all_video_ids)))

    def search_youtube_pnw_gems(self) -> list[dict]:
        """Search broadly for high-engagement PNW camping gem videos."""
        if not self.yt_key:
            return []

        queries = [
            "Pacific Northwest hidden gem camping",
            "best campsites Washington Oregon",
            "PNW camping bucket list",
            "Oregon coast camping hidden gem",
            "Washington state camping secret spots",
        ]

        all_ids = []
        for query in queries:
            try:
                resp = self.session.get(
                    YOUTUBE_SEARCH_BASE,
                    params={
                        "q": query,
                        "type": "video",
                        "part": "id",
                        "maxResults": 10,
                        "key": self.yt_key,
                    },
                    timeout=10
                )
                resp.raise_for_status()
                items = resp.json().get("items", [])
                all_ids.extend(item["id"]["videoId"] for item in items)
            except Exception as e:
                print(f"[Content] YouTube PNW gems error: {e}")

        return self._get_video_details(list(set(all_ids)))

    def _get_video_details(self, video_ids: list[str]) -> list[dict]:
        """Fetch stats and snippets for a list of video IDs."""
        if not video_ids or not self.yt_key:
            return []

        results = []
        # YouTube API accepts up to 50 IDs per request
        for i in range(0, len(video_ids), 50):
            batch = video_ids[i:i+50]
            try:
                resp = self.session.get(
                    YOUTUBE_VIDEO_BASE,
                    params={
                        "id": ",".join(batch),
                        "part": "snippet,statistics",
                        "key": self.yt_key,
                    },
                    timeout=10
                )
                resp.raise_for_status()
                items = resp.json().get("items", [])
                for item in items:
                    stats = item.get("statistics", {})
                    views = int(stats.get("viewCount", 0))
                    comments = int(stats.get("commentCount", 0))

                    # Only include high-signal videos
                    if views < MIN_VIEWS and comments < MIN_COMMENTS:
                        continue

                    snippet = item.get("snippet", {})
                    results.append({
                        "video_id": item["id"],
                        "title": snippet.get("title"),
                        "description": snippet.get("description", "")[:500],
                        "channel": snippet.get("channelTitle"),
                        "published_at": snippet.get("publishedAt"),
                        "view_count": views,
                        "comment_count": comments,
                        "url": f"https://youtube.com/watch?v={item['id']}",
                        "thumbnail": snippet.get("thumbnails", {}).get("high", {}).get("url"),
                    })
            except Exception as e:
                print(f"[Content] YouTube details error: {e}")

        results.sort(key=lambda x: x["view_count"], reverse=True)
        return results

    # ─── Blog / web scraping ─────────────────────────────────────────────────

    def scrape_trip_report(self, url: str) -> Optional[str]:
        """Scrape the first 800 chars of text from a blog/trip report URL."""
        try:
            resp = self.session.get(url, timeout=10)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            # Remove nav/header/footer noise
            for tag in soup(["nav", "header", "footer", "script", "style", "aside"]):
                tag.decompose()
            text = soup.get_text(separator=" ", strip=True)
            # Collapse whitespace
            text = re.sub(r"\s+", " ", text)
            return text[:800]
        except Exception as e:
            print(f"[Content] Blog scrape error for {url}: {e}")
            return None

    def extract_campsite_mentions(self, text: str) -> list[str]:
        """Extract potential campsite names from unstructured text."""
        # Heuristic: capitalized 2-4 word phrases followed by "campground", "camp", "site"
        pattern = r"([A-Z][a-zA-Z\s]{2,30}?(?:Campground|Campsite|Camp Site|State Park|National Forest|Wilderness))"
        matches = re.findall(pattern, text)
        return list(set(matches))

    # ─── Main run ────────────────────────────────────────────────────────────

    def enrich_campsite(self, campsite: dict) -> dict:
        """Find YouTube content + blog mentions for a campsite."""
        name = campsite["name"]
        videos = self.search_youtube(name)

        return {
            "campsite_id": campsite["id"],
            "youtube_videos": videos[:5],   # top 5
            "youtube_total_found": len(videos),
        }
