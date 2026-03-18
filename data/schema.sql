-- PNW Camp Scout — database schema
-- Fresh install: run this file. Existing DB: init_db() in main.py handles migrations.

CREATE TABLE IF NOT EXISTS campsites (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    region TEXT,
    lat REAL,
    lon REAL,
    source TEXT,
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
    wildlife_risk_json TEXT,
    activities_json TEXT,
    best_season TEXT,
    why_its_special TEXT,
    hidden_gem BOOLEAN,
    bucket_list_factor TEXT,
    nearest_landmark TEXT,
    landmark_distance_miles REAL,
    road_conditions TEXT,
    cell_signal TEXT,
    -- v2: family/pet/water fields
    pet_friendly BOOLEAN,
    dogs_on_leash_ok BOOLEAN,
    water_nearby_type TEXT,   -- lake, river, ocean, hot_spring, none
    water_swimmable BOOLEAN,
    hiking_trails_nearby BOOLEAN,
    hiking_trail_notes TEXT,
    group_max_size INTEGER,
    has_group_sites BOOLEAN,
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
    source TEXT,
    content TEXT,
    sentiment_score REAL,
    mentions_gem_language BOOLEAN,
    url TEXT,
    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (campsite_id) REFERENCES campsites(id)
);

CREATE INDEX IF NOT EXISTS idx_campsites_region ON campsites(region);
CREATE INDEX IF NOT EXISTS idx_gem_profiles_score ON gem_profiles(gem_score DESC);
CREATE INDEX IF NOT EXISTS idx_gem_profiles_pet ON gem_profiles(pet_friendly);
CREATE INDEX IF NOT EXISTS idx_availability_date ON availability_cache(check_date);
CREATE INDEX IF NOT EXISTS idx_social_campsite ON social_data(campsite_id);
CREATE INDEX IF NOT EXISTS idx_social_scraped ON social_data(scraped_at);
