# Business Analyzer

Give it a local business (name + location). It researches it live and produces a
full analysis — what it does, market/local context, customers, competition,
strengths, weaknesses, opportunities, financial signals, online presence — then an
escalating **improvement ladder** (operational → menu/service → redesign → rebrand →
business-model rethink) and a **visual redesign mockup** (CSS storefront + brand
board; generated concept images where available).

## Run it

```bash
python3 server.py        # or ./run.sh
# open http://localhost:8765
```

Requirements: Python 3.8+ (stdlib only, no pip installs). The analysis engine
uses the local Hermes `auth.json` Nous credential for the LLM synthesis step
(`HERMES_AUTH_JSON` env var to point elsewhere; `BA_PORT`/`BA_MODEL` to override
port/model). Live research data (geocoding, competitor mapping, area context)
comes from OpenStreetMap / Wikipedia and needs no keys.

## How the pipeline works

1. **Geocode** the location — OpenStreetMap Nominatim (no API key).
2. **Find the business** on OSM via Overpass (with mirror fallbacks) — opening
   hours, website, cuisine tags if mapped.
3. **Map competitors** — every food/drink POI within ~800 m of the geocoded
   point, ranked by distance (Overpass).
4. **Area context** — Wikipedia REST summary of the district.
5. **Synthesis** — all of the above goes to the Nous inference API
   (`z-ai/glm-5.3`) with a conservative, evidence-first system prompt; the
   model must return strictly-structured JSON and flag unverifiable claims.
6. **Render** — the analysis view presents the report, the improvement ladder,
   and a redesign mockup rendered in CSS from the model's brand direction
   (palette, typography, signage, menu). Analyses are saved to `docs/data/` and
   re-appear in the static demo after the next push.

## Deployed (static) mode

GitHub Pages serves `docs/`: browse saved analyses in read-only mode.
Live analysis of any business requires the local server (it calls the LLM API,
which a static site cannot).

GitHub Pages URL: https://dangershony.github.io/business-analyzer/

## Repo layout

```
server.py      stdlib HTTP server + API
analyzer.py    research pipeline (geocode → OSM → wiki → LLM JSON)
docs/          frontend (index.html, app.js, style.css) + saved analyses
docs/data/     analysis JSONs + manifest (index.json)
docs/assets/   generated concept images
```

## Caveats

- LLM sections are model knowledge, not verified fact — the analysis carries an
  evidence note and confidence flags. Verify before acting.
- The Nous credential is an OAuth token managed by Hermes; if the server returns
  401, run a Hermes session so the token refreshes, then retry.
