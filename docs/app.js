/* Business Analyzer frontend — vanilla JS, no dependencies.
   Two modes:
     - live  : local server (server.py) present -> /api/analyze works
     - static: GitHub Pages -> browse saved analyses only
*/

const app = document.getElementById('app');
const banner = document.getElementById('banner');
const modepill = document.getElementById('modepill');

let MODE = 'static';
let MANIFEST = [];

const esc = s => String(s ?? '').replace(/[&<>"']/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

/* ---------------------------------------------------------------- health */

async function init() {
  try {
    const r = await fetch('/api/health');
    if (r.ok) {
      const h = await r.json();
      if (h.ok) { MODE = 'live'; }
    }
  } catch (e) { /* static mode */ }
  modepill.textContent = MODE === 'live'
    ? `live · ${'analysis engine online'}`
    : 'static demo · run locally for live analysis';
  modepill.className = 'mode-pill ' + (MODE === 'live' ? 'live' : 'static');
  banner.innerHTML = MODE === 'live' ? '' : `
    <div class="notice warn">
      <b>Static demo mode.</b> You are viewing saved analyses. To analyse any
      business live, run the local engine:
      <code>python3 server.py</code> then open <code>http://localhost:8765</code>
      (see the <a href="https://github.com/dangershony/business-analyzer#run-it">README</a>).
    </div>`;
  await loadManifest();
  route();
  window.addEventListener('hashchange', route);
}

async function loadManifest() {
  try {
    const r = await fetch('data/index.json');
    if (r.ok) MANIFEST = await r.json();
  } catch (e) { MANIFEST = []; }
  if (MODE === 'live') {
    try {
      const r = await fetch('/api/analyses');
      if (r.ok) MANIFEST = await r.json();
    } catch (e) { /* keep static manifest */ }
  }
}

/* ---------------------------------------------------------------- routing */

function route() {
  const h = location.hash;
  const m = h.match(/^#\/a\/(.+)$/);
  if (m) return showAnalysis(decodeURIComponent(m[1]));
  return showHome();
}

/* ---------------------------------------------------------------- home */

function showHome() {
  const cards = MANIFEST.map(e => `
    <div class="card" onclick="location.hash='#/a/${encodeURIComponent(e.slug)}'">
      <div class="name">${esc(e.business_name)}</div>
      <div class="loc">${esc((e.location || '').split('(')[0])}</div>
      <div class="one">${esc(e.one_liner)}</div>
    </div>`).join('');

  app.innerHTML = `
    <div class="panel">
      <h2>Analyse a local business</h2>
      <div class="form-grid">
        <input type="text" id="f-name" placeholder="Business name (e.g. Lilac)" maxlength="100">
        <input type="text" id="f-loc" placeholder="Location (e.g. Kentish Town, London)" maxlength="120">
        <button id="f-go" ${MODE === 'live' ? '' : 'disabled title="Run the local server for live analysis"'}>Analyse</button>
        <div class="full"><textarea id="f-notes" placeholder="Optional: anything you already know (cuisine, opening hours, website, reviews…)"></textarea></div>
      </div>
      <div class="examples">
        <span class="muted small">try:</span>
        <button class="chip" onclick="quick('Lilac', 'Kentish Town, London')">Lilac — Kentish Town café</button>
        <button class="chip" onclick="quick('Ace Cafe', 'Leeds, England')">Ace Cafe — Leeds</button>
      </div>
      <div id="f-status"></div>
    </div>
    <div class="panel">
      <h2>Saved analyses</h2>
      ${cards ? `<div class="cards">${cards}</div>`
              : `<p class="muted">None yet${MODE === 'live' ? ' — analyse a business above.' : '.'}</p>`}
    </div>
    <div class="panel">
      <h2>How it works</h2>
      <p class="muted">
        1. Geocodes the location (OpenStreetMap Nominatim) ·
        2. Finds the business + competitors on OSM (Overpass) ·
        3. Pulls area context (Wikipedia) ·
        4. An LLM synthesises a structured analysis: what it does, market context,
        customers, competition, strengths/weaknesses/opportunities, financial signals,
        online presence ·
        5. An improvement ladder escalates from operational fixes to menu/service,
        redesign, rebrand, and business-model rethink ·
        6. A visual redesign mockup (CSS storefront + brand board, plus generated
        concept images where available).
      </p>
    </div>`;

  document.getElementById('f-go').onclick = analyse;
}

function quick(name, loc) {
  document.getElementById('f-name').value = name;
  document.getElementById('f-loc').value = loc;
  if (MODE === 'live') analyse();
}

/* ---------------------------------------------------------------- analyse */

async function analyse() {
  const name = document.getElementById('f-name').value.trim();
  const loc = document.getElementById('f-loc').value.trim();
  const notes = document.getElementById('f-notes').value.trim();
  const status = document.getElementById('f-status');
  const btn = document.getElementById('f-go');
  if (!name || !loc) {
    status.innerHTML = '<div class="notice err">Give both a business name and a location.</div>';
    return;
  }
  btn.disabled = true;
  const t0 = Date.now();
  const tick = setInterval(() => {
    status.innerHTML = `<div class="progress"><span class="spin"></span>
      Researching “${esc(name)}” — geocoding, mapping competitors, synthesising… (${Math.round((Date.now() - t0) / 1000)}s)</div>`;
  }, 500);
  try {
    const r = await fetch('/api/analyze', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name, location: loc, notes}),
    });
    const data = await r.json();
    clearInterval(tick);
    if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
    await loadManifest();
    location.hash = `#/a/${encodeURIComponent(data._meta.slug)}`;
  } catch (e) {
    clearInterval(tick);
    status.innerHTML = `<div class="notice err">Analysis failed: ${esc(e.message)}</div>`;
    btn.disabled = false;
  }
}

/* ---------------------------------------------------------------- analysis view */

async function showAnalysis(slug) {
  app.innerHTML = `<div class="progress"><span class="spin"></span>Loading…</div>`;
  let a = null;
  try {
    const r = await fetch(`data/${slug}.json`);
    if (r.ok) a = await r.json();
  } catch (e) {}
  if (!a) { app.innerHTML = '<div class="notice err">Analysis not found.</div><a class="backlink" href="#/">← back</a>'; return; }
  render(a);
}

const TIER_LABEL = {
  operational: 'Tier 1 · Operational',
  menu_service: 'Tier 2 · Menu & service',
  menu: 'Tier 2 · Menu & service',
  redesign: 'Tier 3 · Redesign',
  rebrand: 'Tier 4 · Rebrand',
  business_model: 'Tier 5 · Business model rethink',
};

function render(a) {
  const m = a._meta || {};
  const fin = (a.financial_signals || []).map(f => `
    <tr><td>${esc(f.signal)}</td><td>${esc(f.assessment)}</td>
    <td><span class="conf ${esc((f.confidence || 'low').toLowerCase())}">${esc(f.confidence || 'low')}</span></td></tr>`).join('');

  const ladder = (a.improvement_ladder || []).map(s => `
    <div class="rung">
      <div class="tier-row"><span class="tier">${esc(TIER_LABEL[s.tier] || s.tier)}</span>
        <span class="rtitle">${esc(s.title)}</span></div>
      <div class="detail">${esc(s.detail)}</div>
      <div class="kvchips">
        <span>impact: <b>${esc(s.impact)}</b></span>
        <span>cost: ${esc(s.cost_estimate)}</span>
        <span>timeframe: ${esc(s.timeframe)}</span>
      </div>
    </div>`).join('');

  const comps = (m.competitor_sample || []).map(c => `
    <tr><td>${esc(c.name)}</td><td>${esc(c.tags && (c.tags.amenity || c.tags.shop) || '')}</td>
    <td>${c.distance_m != null ? c.distance_m + ' m' : ''}</td>
    <td>${esc(c.tags && c.tags.cuisine || '')}</td></tr>`).join('');

  const bd = a.brand_direction || {};
  const mc = a.mockup_copy || {};
  const pal = (bd.palette && bd.palette.length === 5) ? bd.palette : ['#7c9cff','#1c232e','#e3b341','#e8ecf1','#0e1116'];
  const [p0, p1, p2, p3, p4] = pal;

  const swatches = pal.map(c => `<div class="swatch" style="background:${esc(c)}"><small>${esc(c)}</small></div>`).join('');
  const images = (a.images || []).map(im => `
    <figure class="mock-imgs"><figcaption>${esc(im.label)}</figcaption>
    <img src="${esc(im.src)}" alt="${esc(im.label)}"></figure>`).join('');

  const imgSection = images ? `<h3>Generated concept images</h3>${images}
    <p class="muted small">Concept images generated offline by the analyst and committed to the repo; live analyses render the CSS mockup above.</p>` : '';

  const storeFront = `
    <div class="storefront">
      <div class="sf-frame" style="background:${esc(p3)}">
        <div class="sf-fascia" style="background:${esc(p0)};color:${esc(p3)}">
          <div class="sf-name">${esc(bd.proposed_name || a.business_name)}</div>
          <div class="sf-tag">${esc(bd.tagline || mc.subhead || '')}</div>
        </div>
        <div class="sf-awning" style="background:${esc(p1)}">
          ${'<i></i>'.repeat(12)}
        </div>
        <div class="sf-body" style="background:${esc(p3)}">
          <div class="sf-window" style="background:${esc(p4)};color:${esc(p3)};border:3px solid ${esc(p1)}">
            <div class="sf-head">${esc(mc.headline || bd.proposed_name || a.business_name)}</div>
            <div class="sf-sub">${esc(mc.subhead || bd.tagline || '')}</div>
            <div class="sf-menu">${(mc.menu_items || []).map(i => `<span>${esc(i)}</span>`).join('')}</div>
          </div>
          <div class="sf-door" style="background:${esc(p1)};color:${esc(p3)}">
            <small>OPEN</small><div class="handle" style="background:${esc(p3)}"></div>
          </div>
        </div>
        <div class="sf-pavement"></div>
      </div>
      <div class="sf-caption">Storefront concept — palette, fascia and menu generated from the analysis.</div>
    </div>`;

  app.innerHTML = `
    <a class="backlink" href="#/">← all analyses</a>
    <div class="panel a-head">
      <h2 class="bname">${esc(a.business_name)}</h2>
      <div class="bloc">${esc(a.location)}</div>
      <p>${esc(a.what_it_does)}</p>
      <div class="meta-row">
        <span>model <b>${esc(m.model || '?')}</b></span>
        <span>generated <b>${esc((m.generated_at || '').slice(0, 16).replace('T', ' ')) || '?'}</b></span>
        <span>OSM competitors <b>${m.competitors_found ?? '?'}</b></span>
        ${m.elapsed_seconds ? `<span>analysis took <b>${m.elapsed_seconds}s</b></span>` : ''}
        ${m.business_osm ? '<span>business found on OSM <b>yes</b></span>' : '<span>business found on OSM <b>no</b></span>'}
      </div>
      <div class="confidence"><b>Evidence note.</b> ${esc(a.confidence_note || '')}</div>
    </div>

    <div class="tabs" id="tabs">
      <button class="tab active" data-t="overview">Overview</button>
      <button class="tab" data-t="swo">Strengths & gaps</button>
      <button class="tab" data-t="finance">Financial signals</button>
      <button class="tab" data-t="ladder">Improvement ladder</button>
      <button class="tab" data-t="mockup">Redesign mockup</button>
      <button class="tab" data-t="data">Data & sources</button>
    </div>

    <div class="tabpage active" data-p="overview">
      <div class="panel"><h2>Market & local context</h2><p>${esc(a.market_local_context)}</p></div>
      <div class="panel"><h2>Target customers</h2><p>${esc(a.target_customers)}</p></div>
      <div class="panel"><h2>Competition</h2><p>${esc(a.competition && a.competition.summary)}</p>
        ${(a.competition && a.competition.nearby_examples || []).length ? `<h3>Named nearby venues</h3><p>${a.competition.nearby_examples.map(esc).join(' · ')}</p>` : ''}
      </div>
      <div class="panel"><h2>Online presence & reviews</h2><p>${esc(a.online_presence_reviews)}</p></div>
    </div>

    <div class="tabpage" data-p="swo">
      <div class="cols3">
        <div class="list-card strengths"><h4>Strengths</h4><ul>${(a.strengths || []).map(s => `<li>${esc(s)}</li>`).join('')}</ul></div>
        <div class="list-card weaknesses"><h4>Weaknesses</h4><ul>${(a.weaknesses || []).map(s => `<li>${esc(s)}</li>`).join('')}</ul></div>
        <div class="list-card opportunities"><h4>Opportunities</h4><ul>${(a.opportunities || []).map(s => `<li>${esc(s)}</li>`).join('')}</ul></div>
      </div>
    </div>

    <div class="tabpage" data-p="finance">
      <div class="panel"><h2>Financial signals (qualitative — estimates, not measurements)</h2>
      <table><tr><th>Signal</th><th>Assessment</th><th>Confidence</th></tr>${fin}</table></div>
    </div>

    <div class="tabpage" data-p="ladder">
      <div class="ladder">${ladder}</div>
      <p class="muted small">Each tier escalates from the previous: fix operations first, then offering, then space, then identity, then the model itself.</p>
    </div>

    <div class="tabpage" data-p="mockup">
      <div class="panel"><h2>Brand direction</h2>
        <p>${esc(bd.aesthetic || '')}</p>
        <p><b>Positioning / storefront concept:</b> ${esc(bd.storefront_concept || '')}</p>
        <p class="muted small">Typography direction: heading “${esc(bd.typography && bd.typography.heading || '')}”, body “${esc(bd.typography && bd.typography.body || '')}”</p>
      </div>
      <div class="mockzone">
        <div class="brand-board">
          <div class="logo-tile" style="background:${esc(p0)};color:${esc(p3)}">
            <div class="lname">${esc(bd.proposed_name || a.business_name)}</div>
            <div class="ltag">${esc(bd.tagline || '')}</div>
            <div class="swatches">${swatches}</div>
          </div>
          ${storeFront}
        </div>
      </div>
      ${imgSection}
    </div>

    <div class="tabpage" data-p="data">
      <div class="panel"><h2>Live data collected</h2>
        <p class="small muted">Geocoded: ${esc(m.geocode && m.geocode.display_name || '?')}
        (${esc(m.geocode && m.geocode.lat)}, ${esc(m.geocode && m.geocode.lon)})</p>
        ${m.business_osm ? `<h3>OSM record</h3><table>
          <tr><th>Field</th><th>Value</th></tr>
          ${Object.entries(m.business_osm.tags || {}).map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join('')}
        </table>` : ''}
        <h3>Nearest competitors (OpenStreetMap, ${m.competitors_found || 0} found within ~800 m)</h3>
        <table><tr><th>Name</th><th>Type</th><th>Distance</th><th>Cuisine</th></tr>${comps}</table>
      </div>
      <div class="panel"><h2>Sources & method</h2>
        <p class="small muted">
          Live: OpenStreetMap Nominatim (geocoding), Overpass API (business + competitor POIs),
          Wikipedia REST (area summary). Synthesis: ${esc(m.model || 'LLM')} via the Nous inference API.
          Anything not sourced from live data is model knowledge and flagged in the analysis —
          verify before acting on it.
        </p>
      </div>
    </div>
  `;

  document.querySelectorAll('#tabs .tab').forEach(t => {
    t.onclick = () => {
      document.querySelectorAll('#tabs .tab').forEach(x => x.classList.toggle('active', x === t));
      document.querySelectorAll('.tabpage').forEach(p => p.classList.toggle('active', p.dataset.p === t.dataset.t));
    };
  });
}

init();
