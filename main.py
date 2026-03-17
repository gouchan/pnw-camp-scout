#!/usr/bin/env python3
"""PNW Camp Scout — CLI entrypoint.

Usage:
  python main.py "find me a bucket-list coastal campsite in Washington this weekend"
  python main.py --init          # Initialize DB + load seed data
  python main.py --top           # Show top gems by score (no re-enrichment)
  python main.py --region coastal_wa --top
"""

import os
import sys
import json
import sqlite3
import click
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent
DB_PATH = str(BASE_DIR / "data" / "campsites.db")
SCHEMA_PATH = str(BASE_DIR / "data" / "schema.sql")
SEEDS_DIR = str(BASE_DIR / "data" / "seeds")


def init_db():
    """Initialize the SQLite database from schema."""
    os.makedirs(str(BASE_DIR / "data"), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()
    click.echo("  DB initialized.")


def load_seeds():
    """Load seed campsite data."""
    from agents.scout import ScoutAgent
    scout = ScoutAgent(DB_PATH)
    seeds_path = os.path.join(SEEDS_DIR, "pnw_gems.json")
    n = scout.load_seeds(seeds_path)
    click.echo(f"  Loaded {n} seed campsites.")


@click.command()
@click.argument("query", required=False)
@click.option("--init", is_flag=True, help="Initialize DB and load seed data")
@click.option("--top", is_flag=True, help="Show top gems from existing scores (fast)")
@click.option("--region", default=None, help="Filter by region key (e.g. coastal_wa, eastern_or)")
@click.option("--min-score", default=70, help="Minimum gem score to show (default: 70)")
@click.option("--no-enrich", is_flag=True, help="Skip social/AI enrichment, use cached scores only")
@click.option("--seed", is_flag=True, help="Run social+classifier enrichment on all seed sites")
def main(query, init, top, region, min_score, no_enrich, seed):
    """
    PNW Camp Scout — Find bucket-list camping in the Pacific Northwest.

    Examples:
      python main.py "coastal hidden gem washington this weekend"
      python main.py "mt hood campground kid-friendly july"
      python main.py "eastern oregon no crowds dark skies"
      python main.py --init
      python main.py --top --region eastern_or
    """

    if init:
        click.echo("\nInitializing PNW Camp Scout...")
        init_db()
        load_seeds()
        click.echo("\nReady. Run: python main.py \"your camping query\"\n")
        return

    # Ensure DB exists
    if not os.path.exists(DB_PATH):
        click.echo("DB not found. Run: python main.py --init")
        sys.exit(1)

    from agents.orchestrator import Orchestrator
    from agents.classifier import ClassifierAgent

    if seed:
        click.echo("\nEnriching seed campsites with social + AI scoring...")
        init_db()
        load_seeds()
        orch = Orchestrator(DB_PATH)
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT id, name, region, lat, lon, facility_type, reservation_url, description FROM campsites")
        rows = cur.fetchall()
        conn.close()

        cols = ["id", "name", "region", "lat", "lon", "facility_type", "reservation_url", "description"]
        campsites = [dict(zip(cols, row)) for row in rows]

        for i, campsite in enumerate(campsites):
            click.echo(f"  [{i+1}/{len(campsites)}] Scoring: {campsite['name']}...")
            orch._enrich_and_score(campsite)

        click.echo(f"\nScored {len(campsites)} campsites. Run: python main.py --top\n")
        return

    if top:
        classifier = ClassifierAgent(DB_PATH)
        gems = classifier.get_top_gems(region=region, min_score=min_score, limit=10)
        if not gems:
            click.echo("No scored gems found. Run: python main.py --seed")
            return

        click.echo(f"\nTOP PNW GEMS{f' in {region}' if region else ''} (score >= {min_score})")
        click.echo("=" * 60)
        for gem in gems:
            flag = "LEGENDARY" if gem["gem_score"] >= 90 else ("BUCKET LIST" if gem["gem_score"] >= 75 else "EXCELLENT")
            click.echo(f"\n{gem['gem_score']}/100 [{flag}] {gem['name']}")
            click.echo(f"  Region: {gem['region']} | Type: {gem.get('facility_type', '?')}")
            click.echo(f"  {gem.get('why_its_special', '')}")
            click.echo(f"  Best time: {gem.get('best_season', '?')}")
            if gem.get("kid_friendly"):
                click.echo("  Kid-friendly: YES")
            if gem.get("reservation_url"):
                click.echo(f"  Book: {gem['reservation_url']}")
        click.echo()
        return

    if not query:
        click.echo("Provide a query or use --init / --top / --seed")
        click.echo("Example: python main.py \"coastal gem washington this weekend\"")
        sys.exit(1)

    # Main query flow
    orch = Orchestrator(DB_PATH)
    enrich = not no_enrich

    click.echo(f"\nSearching... (enrich={enrich})\n")
    response = orch.query(query, enrich=enrich)

    click.echo(response)
    click.echo()


if __name__ == "__main__":
    main()
