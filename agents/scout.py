"""Scout Agent — fetches campsite data and real-time availability.

Sources:
  - RIDB API (Recreation.gov) for federal land facility info
  - Rec.gov undocumented availability endpoint
  - Campflare API for aggregated availability (350k+ sites)
"""

import os
import json
import sqlite3
import requests
from datetime import datetime, timedelta, date
from typing import Optional


RIDB_BASE = "https://ridb.recreation.gov/api/v1"
RECGOV_AVAIL = "https://www.recreation.gov/api/camps/availability/campground/{campground_id}/month"
CAMPFLARE_BASE = "https://campflare.com/api"

CACHE_TTL_FACILITY = 86400       # 24 hours for facility metadata
CACHE_TTL_AVAILABILITY = 900     # 15 minutes for availability


class ScoutAgent:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.ridb_key = os.getenv("RECREATION_GOV_API_KEY", "")
        self.campflare_key = os.getenv("CAMPFLARE_API_KEY", "")
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "pnw-camp-scout/1.0"})

    def _db(self):
        return sqlite3.connect(self.db_path)

    # ─── RIDB: Campground search by state/activity ───────────────────────────

    def search_ridb(self, state: str = "WA", activity: Optional[str] = None,
                    limit: int = 50) -> list[dict]:
        """Search RIDB for campgrounds in a given state."""
        if not self.ridb_key:
            return []

        params = {
            "state": state,
            "limit": limit,
            "offset": 0,
            "apikey": self.ridb_key,
        }
        if activity:
            params["activity"] = activity

        try:
            resp = self.session.get(f"{RIDB_BASE}/facilities", params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            return data.get("RECDATA", [])
        except Exception as e:
            print(f"[Scout] RIDB search error: {e}")
            return []

    def get_ridb_facility(self, facility_id: str) -> Optional[dict]:
        """Fetch full facility detail from RIDB."""
        if not self.ridb_key:
            return None
        try:
            resp = self.session.get(
                f"{RIDB_BASE}/facilities/{facility_id}",
                params={"apikey": self.ridb_key},
                timeout=10
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            print(f"[Scout] RIDB facility {facility_id} error: {e}")
            return None

    # ─── Rec.gov: Live campsite availability ─────────────────────────────────

    def get_recgov_availability(self, campground_id: str,
                                check_date: Optional[date] = None) -> dict:
        """
        Fetch monthly availability from Rec.gov's undocumented endpoint.
        Returns dict of {site_id: {date: status}} for the month.
        """
        if check_date is None:
            check_date = date.today()
        start = check_date.replace(day=1)

        # Check cache first
        conn = self._db()
        cur = conn.cursor()
        cur.execute(
            """SELECT available_sites, total_sites, cached_at
               FROM availability_cache
               WHERE campsite_id = ? AND check_date = ?""",
            (campground_id, str(check_date))
        )
        row = cur.fetchone()
        if row:
            cached_at = datetime.fromisoformat(row[2])
            if (datetime.now() - cached_at).seconds < CACHE_TTL_AVAILABILITY:
                conn.close()
                return {"available": row[0], "total": row[1], "cached": True}
        conn.close()

        try:
            resp = self.session.get(
                RECGOV_AVAIL.format(campground_id=campground_id),
                params={"start_date": start.isoformat() + "T00:00:00.000Z"},
                headers={
                    "Accept": "application/json",
                    "User-Agent": "Mozilla/5.0",
                },
                timeout=15
            )
            resp.raise_for_status()
            data = resp.json()

            campsites = data.get("campsites", {})
            available = 0
            total = len(campsites)

            for site_id, site_data in campsites.items():
                availabilities = site_data.get("availabilities", {})
                for dt, status in availabilities.items():
                    if str(check_date) in dt and status == "Available":
                        available += 1
                        break

            # Cache it
            conn = self._db()
            conn.execute(
                """INSERT OR REPLACE INTO availability_cache
                   (campsite_id, check_date, available_sites, total_sites, cached_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (campground_id, str(check_date), available, total, datetime.now().isoformat())
            )
            conn.commit()
            conn.close()

            return {"available": available, "total": total, "cached": False}

        except Exception as e:
            print(f"[Scout] Rec.gov availability error for {campground_id}: {e}")
            return {"available": -1, "total": -1, "error": str(e)}

    # ─── Campflare: Aggregated availability ──────────────────────────────────

    def get_campflare_availability(self, campground_name: str,
                                   start_date: Optional[str] = None,
                                   end_date: Optional[str] = None) -> dict:
        """Query Campflare API for availability."""
        if not self.campflare_key:
            return {"error": "No Campflare API key"}

        if start_date is None:
            start_date = date.today().isoformat()
        if end_date is None:
            end_date = (date.today() + timedelta(days=30)).isoformat()

        try:
            resp = self.session.get(
                f"{CAMPFLARE_BASE}/campgrounds",
                params={
                    "name": campground_name,
                    "start": start_date,
                    "end": end_date,
                },
                headers={"Authorization": f"Bearer {self.campflare_key}"},
                timeout=10
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            print(f"[Scout] Campflare error: {e}")
            return {"error": str(e)}

    # ─── Database: load seed data ─────────────────────────────────────────────

    def load_seeds(self, seeds_path: str) -> int:
        """Load pnw_gems.json seed data into the campsites table."""
        with open(seeds_path) as f:
            data = json.load(f)

        conn = self._db()
        loaded = 0
        for gem in data.get("gems", []):
            conn.execute(
                """INSERT OR IGNORE INTO campsites
                   (id, name, region, lat, lon, source, facility_type, reservation_url, description)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    gem["id"],
                    gem["name"],
                    gem.get("region"),
                    gem.get("lat"),
                    gem.get("lon"),
                    gem.get("source", "manual"),
                    gem.get("facility_type"),
                    gem.get("reservation_url"),
                    gem.get("notes"),
                )
            )
            loaded += 1
        conn.commit()
        conn.close()
        return loaded

    # ─── Main scout run ───────────────────────────────────────────────────────

    def get_campsite_with_availability(self, campsite_id: str,
                                       ridb_id: Optional[str] = None,
                                       check_dates: Optional[list[date]] = None) -> dict:
        """Return campsite info + availability for given dates."""
        conn = self._db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM campsites WHERE id = ?", (campsite_id,))
        row = cur.fetchone()
        conn.close()

        if not row:
            return {"error": f"Campsite {campsite_id} not found"}

        cols = ["id", "name", "region", "lat", "lon", "source", "facility_type",
                "bathrooms", "max_occupancy", "reservation_url", "phone",
                "description", "last_updated"]
        campsite = dict(zip(cols, row))

        if check_dates and ridb_id:
            campsite["availability"] = {}
            for d in check_dates:
                avail = self.get_recgov_availability(ridb_id, d)
                campsite["availability"][str(d)] = avail

        return campsite

    def search_campsites(self, region: Optional[str] = None,
                         facility_type: Optional[str] = None) -> list[dict]:
        """Search local DB for campsites matching criteria."""
        conn = self._db()
        cur = conn.cursor()

        query = "SELECT id, name, region, lat, lon, facility_type, reservation_url FROM campsites WHERE 1=1"
        params = []

        if region:
            query += " AND region = ?"
            params.append(region)
        if facility_type:
            query += " AND facility_type = ?"
            params.append(facility_type)

        cur.execute(query, params)
        rows = cur.fetchall()
        conn.close()

        cols = ["id", "name", "region", "lat", "lon", "facility_type", "reservation_url"]
        return [dict(zip(cols, row)) for row in rows]
