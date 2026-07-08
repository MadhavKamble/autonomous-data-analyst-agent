-- ===========================================================================
-- 001: Ride-sharing data tables (snapshot targets for the Delta Lake export)
--
-- Schemas mirror the upstream Gold-layer Delta tables EXACTLY — no columns
-- added, renamed, or inferred. Delta -> Postgres type mapping used throughout:
--
--     string        -> text
--     long          -> bigint
--     integer       -> integer
--     double        -> double precision
--     boolean       -> boolean
--     timestamp_ntz -> timestamp (without time zone)
--
-- KNOWN TYPE MISMATCH (deliberately preserved, not papered over):
-- event_hour is `long` in zone_demand but `integer` in
-- zone_demand_historical_nyc, so they land here as bigint vs integer.
-- Any query that unions or joins across these tables on event_hour must cast
-- explicitly (e.g. zone_demand.event_hour::integer). The RAG schema docs
-- teach the SQL-generator agent this rule rather than hiding the mismatch.
--
-- No PRIMARY KEY / UNIQUE constraints on purpose: these tables are a
-- read-only snapshot of data whose integrity is owned by the upstream
-- pipeline. A uniqueness quirk in the source (e.g. a replayed Kafka batch)
-- should not make the export fail; the agent only ever reads. Plain b-tree
-- indexes cover the dominant access patterns (filter by date/zone) instead.
--
-- COMMENT ON metadata below doubles as the source of truth for the RAG
-- schema-documentation chunks built in a later step.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- zone_demand: hourly per-zone aggregates from the SYNTHETIC live-simulator
-- path of the upstream platform.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS zone_demand (
    event_date           text,             -- string in Delta
    event_hour           bigint,           -- long in Delta (NOTE: integer in the historical table)
    city_zone            text,
    ride_count           bigint,
    completed_rides      bigint,
    cancelled_rides      bigint,
    gross_revenue_inr    double precision,
    avg_surge_multiplier double precision
);

COMMENT ON TABLE  zone_demand IS
    'Hourly per-zone demand aggregates from the synthetic live-simulator path. One row per (event_date, event_hour, city_zone).';
COMMENT ON COLUMN zone_demand.event_date           IS 'Calendar date as a string, format YYYY-MM-DD.';
COMMENT ON COLUMN zone_demand.event_hour           IS 'Hour of day 0-23. BIGINT here but INTEGER in zone_demand_historical_nyc - cast explicitly when unioning/joining across the two tables.';
COMMENT ON COLUMN zone_demand.city_zone            IS 'Name of the city zone the rides originated in.';
COMMENT ON COLUMN zone_demand.ride_count           IS 'Total rides in this zone-hour across all statuses (requested + completed + cancelled), so ride_count >= completed_rides + cancelled_rides.';
COMMENT ON COLUMN zone_demand.completed_rides      IS 'Rides with status ''completed'' in this zone-hour.';
COMMENT ON COLUMN zone_demand.cancelled_rides      IS 'Rides with status ''cancelled'' in this zone-hour (does not include unfulfilled ''requested'' rides).';
COMMENT ON COLUMN zone_demand.gross_revenue_inr    IS 'Total gross revenue for the zone-hour, in Indian Rupees (INR).';
COMMENT ON COLUMN zone_demand.avg_surge_multiplier IS 'Average surge pricing multiplier over rides in the zone-hour (1.0 = no surge).';

CREATE INDEX IF NOT EXISTS idx_zone_demand_date      ON zone_demand (event_date);
CREATE INDEX IF NOT EXISTS idx_zone_demand_zone_date ON zone_demand (city_zone, event_date);

-- ---------------------------------------------------------------------------
-- rides_historical_nyc: row-level rides derived from REAL NYC TLC taxi data
-- (fares/distances/tips/zones), re-modelled as ride-sharing trips with INR
-- monetary values.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rides_historical_nyc (
    ride_id          text,
    event_timestamp  timestamp,            -- timestamp_ntz in Delta
    event_date       text,
    event_hour       integer,
    city_zone        text,
    distance_km      double precision,
    fare_base_inr    double precision,
    surge_multiplier double precision,
    payment_method   text,
    vehicle_type     text,
    is_completed     boolean,
    status           text,
    gross_fare_inr   double precision
);

COMMENT ON TABLE  rides_historical_nyc IS
    'Row-level historical rides derived from real NYC TLC taxi data. One row per ride.';
COMMENT ON COLUMN rides_historical_nyc.ride_id          IS 'Unique ride identifier (string).';
COMMENT ON COLUMN rides_historical_nyc.event_timestamp  IS 'Ride start timestamp, no time zone (timestamp_ntz upstream).';
COMMENT ON COLUMN rides_historical_nyc.event_date       IS 'Calendar date as a string, format YYYY-MM-DD. Redundant with event_timestamp; prefer it for date filters (matches the aggregate tables).';
COMMENT ON COLUMN rides_historical_nyc.event_hour       IS 'Hour of day 0-23 (INTEGER).';
COMMENT ON COLUMN rides_historical_nyc.city_zone        IS 'NYC TLC zone the ride originated in.';
COMMENT ON COLUMN rides_historical_nyc.distance_km      IS 'Trip distance in kilometres.';
COMMENT ON COLUMN rides_historical_nyc.fare_base_inr    IS 'Base fare before surge, in Indian Rupees (INR).';
COMMENT ON COLUMN rides_historical_nyc.surge_multiplier IS 'Surge pricing multiplier applied to this ride (1.0 = no surge).';
COMMENT ON COLUMN rides_historical_nyc.payment_method   IS 'How the rider paid (categorical string).';
COMMENT ON COLUMN rides_historical_nyc.vehicle_type     IS 'Vehicle category of the ride (categorical string).';
COMMENT ON COLUMN rides_historical_nyc.is_completed     IS 'TRUE if the ride completed (equivalent to status = ''completed'').';
COMMENT ON COLUMN rides_historical_nyc.status           IS 'Ride lifecycle status: one of ''requested'', ''completed'', ''cancelled''. Rides left in ''requested'' were never fulfilled and count in ride totals but are neither completed nor cancelled.';
COMMENT ON COLUMN rides_historical_nyc.gross_fare_inr   IS 'Final charged fare in INR (base fare x surge for completed rides).';

CREATE INDEX IF NOT EXISTS idx_rides_hist_date       ON rides_historical_nyc (event_date);
CREATE INDEX IF NOT EXISTS idx_rides_hist_zone_date  ON rides_historical_nyc (city_zone, event_date);
CREATE INDEX IF NOT EXISTS idx_rides_hist_timestamp  ON rides_historical_nyc (event_timestamp);

-- ---------------------------------------------------------------------------
-- zone_demand_historical_nyc: hourly per-zone aggregates over the REAL NYC
-- TLC data (the aggregated counterpart of rides_historical_nyc).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS zone_demand_historical_nyc (
    event_date           text,
    event_hour           integer,          -- integer in Delta (NOTE: long/bigint in zone_demand)
    city_zone            text,
    ride_count           bigint,
    completed_rides      bigint,
    cancelled_rides      bigint,
    gross_revenue_inr    double precision,
    avg_surge_multiplier double precision
);

COMMENT ON TABLE  zone_demand_historical_nyc IS
    'Hourly per-zone demand aggregates over the real NYC TLC historical data. One row per (event_date, event_hour, city_zone). Aggregated counterpart of rides_historical_nyc.';
COMMENT ON COLUMN zone_demand_historical_nyc.event_date           IS 'Calendar date as a string, format YYYY-MM-DD.';
COMMENT ON COLUMN zone_demand_historical_nyc.event_hour           IS 'Hour of day 0-23. INTEGER here but BIGINT in zone_demand - cast explicitly when unioning/joining across the two tables.';
COMMENT ON COLUMN zone_demand_historical_nyc.city_zone            IS 'NYC TLC zone name.';
COMMENT ON COLUMN zone_demand_historical_nyc.ride_count           IS 'Total rides in this zone-hour across all statuses (requested + completed + cancelled), so ride_count >= completed_rides + cancelled_rides.';
COMMENT ON COLUMN zone_demand_historical_nyc.completed_rides      IS 'Rides with status ''completed'' in this zone-hour.';
COMMENT ON COLUMN zone_demand_historical_nyc.cancelled_rides      IS 'Rides with status ''cancelled'' in this zone-hour (does not include unfulfilled ''requested'' rides).';
COMMENT ON COLUMN zone_demand_historical_nyc.gross_revenue_inr    IS 'Total gross revenue for the zone-hour in INR (sum of completed rides'' gross fares).';
COMMENT ON COLUMN zone_demand_historical_nyc.avg_surge_multiplier IS 'Average surge multiplier over rides in the zone-hour (1.0 = no surge).';

CREATE INDEX IF NOT EXISTS idx_zone_hist_date      ON zone_demand_historical_nyc (event_date);
CREATE INDEX IF NOT EXISTS idx_zone_hist_zone_date ON zone_demand_historical_nyc (city_zone, event_date);
