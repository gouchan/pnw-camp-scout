-- PNW Camp Scout database schema

CREATE TABLE IF NOT EXISTS campsites (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    region TEXT,
    lat REAL,
    lon REAL,
    source TEXT,              -- ridb, campflare, manual
    facility_type TEXT,       -- campground, dispersed, walk-in, group
    bathrooms TEXT,           -- flush, vault, none
    max_occupancy INTEGER,
    reservation_url TEXT,
    phone TEXT,
    description TEXT,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS gem_profiles (
    campsite_id TEXT PRIMARY KEY,
    gem_score INTEGER,
    kid_friendly BOOLEAN,
    wildlife_risk_json TEXT,   -- JSON: {bears, cougars, coyotes, notes}
    activities_json TEXT,      -- JSON array of strings
    best_season TEXT,
    why_its_special TEXT,
    hidden_gem BOOLEAN,
    bucket_list_factor TEXT,   -- low, medium, high, legendary
    nearest_landmark TEXT,
    landmark_distance_miles REAL,
    road_conditions TEXT,
    cell_signal TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (campsite_id) REFERENCES campsites(id)
);

CREATE TABLE IF NOT EXISTS availability_cache (
    campsite_id TEXT,
    check_date DATE,
    available_sites INTEGER,
    total_sites INTEGER,
    cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (campsite_id, check_date),
    FOREIGN KEY (campsite_id) REFERENCES campsites(id)
);

CREATE TABLE IF NOT EXISTS social_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campsite_id TEXT,
    source TEXT,               -- reddit, google, blog, youtube
    content TEXT,
    sentiment_score REAL,
    mentions_gem_language BOOLEAN,
    url TEXT,
    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (campsite_id) REFERENCES campsites(id)
);

CREATE INDEX IF NOT EXISTS idx_campsites_region ON campsites(region);
CREATE INDEX IF NOT EXISTS idx_gem_profiles_score ON gem_profiles(gem_score DESC);
CREATE INDEX IF NOT EXISTS idx_availability_date ON availability_cache(check_date);
CREATE INDEX IF NOT EXISTS idx_social_campsite ON social_data(campsite_id);
