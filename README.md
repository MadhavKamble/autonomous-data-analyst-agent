# Autonomous Data-Analyst Agent

A multi-agent system that answers natural-language questions about ride-sharing data by
**planning → retrieving schema context (RAG) → generating SQL → executing it read-only →
critiquing its own result → retrying if wrong → summarizing**, and returns the full
reasoning trace alongside the answer.

> **Status: work in progress.** Sections below marked *(TODO)* are filled in as the
> corresponding component lands.

## Relationship to the Ride-Sharing Analytics Platform

This project reasons over a **one-time Postgres snapshot** exported from the Gold layer of
my [Real-Time Ride-Sharing Analytics Platform] (Kafka → Delta Lake → Airflow). The two
systems are deliberately decoupled: the agent has no runtime dependency on Delta Lake,
Spark, or Kafka, and either project can run, deploy, or fail independently. The only
bridge is `scripts/export_from_delta.py`, a manually-run utility.

## Architecture *(TODO — diagram + agent-by-agent walkthrough)*

## Quickstart (local development)

```bash
# 1. Local Postgres 16 + pgvector (host port 5433, avoids clashing with other stacks)
docker compose up -d

# 2. Python env
cd backend && uv sync --extra dev && cp .env.example .env && cd ..

# 3. Apply migrations, then load realistic mock data (no Delta Lake needed)
uv run --project backend python scripts/run_migrations.py
uv run --project backend python db/seed/seed_mock_data.py
```

To load **real** data instead of mock data, run the export utility against the other
project's Gold tables (requires the `export` extra):

```bash
cd backend && uv sync --extra export && cd ..
uv run --project backend python scripts/export_from_delta.py \
  --rides-historical        /path/to/gold/rides_historical_nyc \
  --zone-demand-historical  /path/to/gold/zone_demand_historical_nyc \
  --zone-demand             /path/to/gold/zone_demand
```

## Deployment

*(TODO — Render + Vercel + Neon walkthrough)*

### Keeping the Render free tier warm during demos

Render's free tier spins a service down after ~15 minutes without traffic, and the next
request eats a 30–60 second cold start — exactly what you don't want mid-interview. The
zero-cost fix: create a free [UptimeRobot](https://uptimerobot.com) HTTP(S) monitor
pointed at `https://<your-service>.onrender.com/health` with the default **5-minute
interval**. Each ping counts as traffic, so the service never reaches Render's idle
threshold and stays warm around the clock. Two honest caveats: (1) Render's free tier
includes 750 instance-hours/month and an always-warm service consumes ~730 of them, so
this budget covers exactly **one** service — don't try to keep two warm; (2) this
sidesteps the cold-start problem rather than solving it, so the frontend still detects
in-progress cold starts and shows a "waking up the agent service" message for the case
where the monitor is off or a ping was missed. Pause the monitor outside placement season
to keep usage well under the cap.

## Design decisions & tradeoffs *(TODO — full write-ups)*

- Why a fixed agent pipeline instead of an autonomous tool-choosing agent *(TODO)*
- How bad or injected SQL from a hallucinating model is prevented *(TODO)*
- Why a Postgres snapshot instead of live Delta access *(TODO)*
- What changes for a real production deployment *(TODO)*
