"""
Business Analyzer — research pipeline.

Stages:
  1. Geocode the location (OpenStreetMap Nominatim, no API key).
  2. Find the business itself on OSM (Overpass) near that location.
  3. Find competitors on OSM within a radius (cafes, restaurants, bakeries, bars...).
  4. Pull an area summary from Wikipedia (free REST API).
  5. Send all structured data + the business name to the Nous inference API
     (credential read from the local Hermes auth.json at request time) and ask
     for a strictly-structured JSON analysis + improvement ladder + brand direction.

Zero third-party Python dependencies: stdlib only.
"""

import datetime
import json
import math
import os
import re
import time
import urllib.parse
import urllib.request

HERMES_AUTH = os.environ.get(
    'HERMES_AUTH_JSON',
    os.path.expanduser('~/.hermes/profiles/local-company-hunter/auth.json'))

NOMINATIM = 'https://nominatim.openstreetmap.org'
OVERPASS_MIRRORS = [
    'https://overpass-api.de/api/interpreter',
    'https://overpass.kumi.systems/api/interpreter',
    'https://overpass.private.coffee/api/interpreter',
]
WIKIPEDIA = 'https://en.wikipedia.org/api/rest_v1'
USER_AGENT = 'business-analyzer/1.0 (github.com/dangershony/business-analyzer)'
LLM_MODEL = os.environ.get('BA_MODEL', 'z-ai/glm-5.3')


class PipelineError(Exception):
    pass


# ---------------------------------------------------------------- HTTP helpers

def _http(url, data=None, headers=None, timeout=30):
    hdrs = {'User-Agent': USER_AGENT}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def _get_json(url, timeout=30):
    status, body = _http(url, timeout=timeout)
    return json.loads(body)


# ---------------------------------------------------------------- credentials

def load_llm_credential():
    """Read the Nous inference credential from the Hermes auth file.

    Returns (base_url, token) or raises PipelineError.
    The token is an OAuth access token that Hermes refreshes while running;
    we re-read it for every request so a stale token ages out naturally.
    """
    try:
        with open(HERMES_AUTH) as f:
            pool = json.load(f).get('credential_pool', {}).get('nous', [])
    except (OSError, ValueError) as e:
        raise PipelineError(f'Could not read Hermes auth file: {e}')
    for cred in pool:
        token = cred.get('access_token') or cred.get('agent_key')
        base = cred.get('inference_base_url')
        if token and base:
            return base.rstrip('/'), token
    raise PipelineError('No usable Nous credential in Hermes auth file')


# ---------------------------------------------------------------- stage 1: geocode

def geocode(location):
    q = urllib.parse.quote(location)
    results = _get_json(
        f'{NOMINATIM}/search?q={q}&format=json&limit=1', timeout=25)
    if not results:
        raise PipelineError(f'Could not geocode location: {location!r}')
    r = results[0]
    return {
        'lat': float(r['lat']),
        'lon': float(r['lon']),
        'display_name': r.get('display_name', location),
        'source': 'OpenStreetMap Nominatim',
    }


# ---------------------------------------------------------------- stage 2/3: OSM

def _overpass(query, timeout=45):
    payload = urllib.parse.urlencode({'data': query}).encode()
    errors = []
    for attempt in range(2):  # two passes over all mirrors
        for mirror in OVERPASS_MIRRORS:
            try:
                status, body = _http(mirror, data=payload, timeout=timeout)
                return json.loads(body).get('elements', [])
            except Exception as e:  # noqa: BLE001 - try next mirror
                errors.append(f'{mirror}: {type(e).__name__} {e}')
        time.sleep(2)
    raise PipelineError('All Overpass mirrors failed; last: ' + (errors[-1] if errors else 'unknown'))


def _norm(s):
    return re.sub(r'[^a-z0-9]+', '', (s or '').lower())


def _haversine_m(lat1, lon1, lat2, lon2):
    # approximate metres; good enough for ranking
    dy = (lat2 - lat1) * 111_320
    dx = (lon2 - lon1) * 111_320 * math.cos((lat1 + lat2) * 0.5 * math.pi / 180)
    return int((dy * dy + dx * dx) ** 0.5)


def _entity(el, ref_lat, ref_lon):
    lat = el.get('lat') or (el.get('center') or {}).get('lat')
    lon = el.get('lon') or (el.get('center') or {}).get('lon')
    tags = el.get('tags', {})
    return {
        'osm_id': f"{el.get('type')}/{el.get('id')}",
        'name': tags.get('name', '(unnamed)'),
        'lat': lat,
        'lon': lon,
        'distance_m': _haversine_m(ref_lat, ref_lon, lat, lon) if lat and lon else None,
        'tags': {
            k: tags[k] for k in (
                'amenity', 'cuisine', 'shop', 'opening_hours', 'website',
                'phone', 'outdoor_seating', 'takeaway', 'dietary',
                'internet_access', 'wheelchair', 'brand') if k in tags
        },
    }


def find_business(name, lat, lon, radius=1200):
    """Search OSM for the named business near the geocoded point."""
    try:
        q = f'''[out:json][timeout:25];
        (
          nwr["name"~"{re.escape(name)}",i](around:{radius},{lat},{lon});
        );
        out center tags 5;'''
        elements = _overpass(q, timeout=40)
    except PipelineError:
        return None
    target = _norm(name)
    best = None
    for el in elements:
        ent = _entity(el, lat, lon)
        if _norm(ent['name']) == target:
            if best is None or (ent['distance_m'] or 9e9) < (best['distance_m'] or 9e9):
                best = ent
    return best


def find_competitors(lat, lon, radius=800, limit=40):
    """All food/drink POIs near the point, sorted by distance."""
    q = f'''[out:json][timeout:60];
    (
      node["amenity"~"cafe|restaurant|fast_food|bakery|bar|pub|ice_cream|biergarten"](around:{radius},{lat},{lon});
      way["amenity"~"cafe|restaurant|fast_food|bakery|bar|pub|ice_cream|biergarten"](around:{radius},{lat},{lon});
      node["shop"~"bakery|pastry|coffee|deli|greengrocer|confectionery"](around:{radius},{lat},{lon});
      way["shop"~"bakery|pastry|coffee|deli|greengrocer|confectionery"](around:{radius},{lat},{lon});
    );
    out center tags;'''
    elements = _overpass(q, timeout=90)
    ents = []
    for el in elements:
        tags = el.get('tags', {})
        if 'name' not in tags:
            continue
        ents.append(_entity(el, lat, lon))
    ents = [e for e in ents if e['distance_m'] is not None]
    ents.sort(key=lambda e: e['distance_m'])
    return ents[:limit]


# ---------------------------------------------------------------- stage 4: wiki

def area_wikipedia(place):
    """One-paragraph area summary from the English Wikipedia REST API."""
    title = urllib.parse.quote(place.split(',')[0].strip())
    try:
        status, body = _http(f'{WIKIPEDIA}/page/summary/{title}', timeout=15)
        if status == 200:
            d = json.loads(body)
            if d.get('extract'):
                return {'title': d.get('title'), 'extract': d['extract'],
                        'url': d.get('content_urls', {}).get('desktop', {}).get('page')}
    except Exception:  # noqa: BLE001 - optional context
        pass
    return None


# ---------------------------------------------------------------- stage 5: LLM

SYSTEM_PROMPT = """You are a conservative, evidence-first local-business analyst for an investment scout.
You analyse a single local business and output ONLY valid JSON (no markdown, no commentary).
Rules:
- Be factual and conservative. Where you are relying on general knowledge rather than live data, say so.
- Your knowledge has a cutoff; do NOT invent current ratings, reviews, prices or revenue numbers. Mark unverifiable claims with confidence "low" or "medium" and never present them as facts.
- Use the provided live OpenStreetMap competitor data as the factual backbone for local context and competition.
- Financial signals must be qualitative (e.g. "high footfall rent corridor", "typical gross margins for independent cafes") and explicitly flagged as estimates, not measurements.
JSON schema (all fields required unless marked optional):
{
 "business_name": str, "location": str,
 "what_it_does": str,
 "confidence_note": str,               // one line on what is verified vs model knowledge
 "market_local_context": str,
 "target_customers": str,
 "competition": {"summary": str, "nearby_examples": [str]},  // use the OSM names given
 "strengths": [str], "weaknesses": [str], "opportunities": [str],
 "financial_signals": [{"signal": str, "assessment": str, "confidence": "low"|"medium"|"high"}],
 "online_presence_reviews": str,        // note explicitly if unverifiable
 "improvement_ladder": [               // EXACTLY these 5 tiers, escalating
   {"tier": "operational", "title": str, "detail": str, "impact": "low"|"medium"|"high", "cost_estimate": str, "timeframe": str},
   {"tier": "menu_service", ...},
   {"tier": "redesign", ...},
   {"tier": "rebrand", ...},
   {"tier": "business_model", ...}
 ],
 "brand_direction": {                  // used to render a visual mockup
   "proposed_name": str,               // may equal the current name if rebrand not warranted
   "tagline": str,
   "palette": ["#RRGGBB", ... exactly 5 hex colours, ordered: primary, secondary, accent, light, dark],
   "typography": {"heading": str, "body": str},  // font family names
   "aesthetic": str,                   // 1-2 sentences of art direction
   "storefront_concept": str
 },
 "mockup_copy": {"headline": str, "subhead": str, "menu_items": [str, str, str], "review_snippet": str}
}"""


def llm_analyze(business_name, location, geo, business_osm, competitors, wiki, extra_notes=''):
    base, token = load_llm_credential()
    today = datetime.date.today().isoformat()

    comp_lines = []
    for c in competitors[:35]:
        d = c['distance_m']
        tag = c['tags'].get('amenity') or c['tags'].get('shop') or '?'
        cuisine = c['tags'].get('cuisine', '')
        comp_lines.append(f"- {c['name']} ({tag}, {cuisine}, {d}m away)"
                          + (f" website={c['tags']['website']}" if 'website' in c['tags'] else ''))

    user_prompt = f"""Today's date: {today}.
Business to analyse: "{business_name}" in/near: "{location}".

LIVE DATA (collected just now):
Geocoded location: {geo['display_name']} ({geo['lat']:.5f}, {geo['lon']:.5f}).
OSM record for the business itself: {json.dumps(business_osm, default=str) if business_osm else 'NOT FOUND on OpenStreetMap'}.
Nearby food/drink competitors on OSM within ~800m ({len(competitors)} found, nearest first):
{chr(10).join(comp_lines) or '(none found)'}
Wikipedia area summary: {wiki['extract'] if wiki else '(unavailable)'}
{extra_notes}

If the OSM record was not found, base "what it does" on your knowledge of this named business if you know it; otherwise reason from the name and location and say so in confidence_note.
Produce the JSON analysis now. Remember: conservative, flag uncertainty, no invented facts."""

    body = json.dumps({
        'model': LLM_MODEL,
        'messages': [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': user_prompt},
        ],
        'max_tokens': 5000,
        'temperature': 0.4,
    }).encode()
    req = urllib.request.Request(base + '/chat/completions', data=body, method='POST', headers={
        'Authorization': 'Bearer ' + token,
        'Content-Type': 'application/json',
    })
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', 'replace')[:300]
        raise PipelineError(f'LLM API error {e.code}: {detail}')
    text = data['choices'][0]['message']['content']
    return text


def extract_json(text):
    """Strip markdown fences and pull the outermost JSON object."""
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip(), flags=re.M)
    start = text.find('{')
    if start == -1:
        raise PipelineError('LLM response contained no JSON object')
    depth = 0
    for i in range(start, len(text)):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise PipelineError('LLM response JSON was truncated or malformed')


# ---------------------------------------------------------------- full run

def run_analysis(business_name, location, extra_notes=''):
    """Run the whole pipeline. Returns (analysis_dict, provenance_dict)."""
    geo = geocode(location)
    business_osm = find_business(business_name, geo['lat'], geo['lon'])
    try:
        competitors = find_competitors(geo['lat'], geo['lon'])
    except PipelineError as e:
        competitors = []
        extra_notes += f'\nNOTE: Overpass competitor query failed ({e}); competition section must say data was unavailable.'
    wiki = area_wikipedia(location)

    raw = llm_analyze(business_name, location, geo, business_osm, competitors, wiki, extra_notes)
    analysis = extract_json(raw)

    analysis['_meta'] = {
        'generated_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'model': LLM_MODEL,
        'geocode': geo,
        'business_osm': business_osm,
        'competitors_found': len(competitors),
        'wikipedia': bool(wiki),
        'competitor_sample': [
            {'name': c['name'], 'distance_m': c['distance_m'],
             'tags': c['tags']} for c in competitors[:25]
        ],
    }
    return analysis


if __name__ == '__main__':
    import sys
    name = sys.argv[1] if len(sys.argv) > 1 else 'Lilac'
    loc = sys.argv[2] if len(sys.argv) > 2 else 'Kentish Town, London'
    print(json.dumps(run_analysis(name, loc), indent=2))
