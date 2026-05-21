#!/usr/bin/env python3
"""
Daily job research generator.

Fetches senior EE / comms-systems job postings from Adzuna, uses Groq to
filter for relevance and produce structured summaries, then writes a daily
JSON file and rebuilds jobs/index.html.
"""

import json
import os
import re
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

from groq import Groq

BASE_DIR = Path(__file__).parent
JOBS_DIR = Path(__file__).resolve().parents[2] / "jobs"
SEEN_FILE = BASE_DIR / "seen_companies.json"
HISTORY_DAYS = 14

ADZUNA_BASE = "https://api.adzuna.com/v1/api/jobs"

# Rotated daily so results vary across the week
SEARCH_TERMS = [
    "RF engineer senior",
    "signal processing engineer",
    "communications systems engineer",
    "DSP engineer",
    "mixed signal engineer",
    "wireless systems engineer",
    "modem engineer",
    "radio frequency engineer",
]

SF_LOCATION = {
    "country": "us",
    "where": "San Francisco",
    "distance": 50,
    "label": "San Francisco Bay Area",
    "region": "sf",
}

# Separate search targeting SF startups — "startup" in the query surfaces smaller companies
SF_STARTUP_LOCATION = {
    "country": "us",
    "where": "San Francisco",
    "distance": 50,
    "label": "San Francisco Bay Area",
    "region": "sf",
    "startup_search": True,
}

NON_SF_LOCATIONS = [
    {"country": "us", "where": "San Diego",  "distance": 25, "label": "San Diego, CA",        "region": "outside_sf"},
    {"country": "us", "where": "New York",   "distance": 25, "label": "New York City, NY",     "region": "outside_sf"},
    {"country": "us", "where": "Boston",     "distance": 25, "label": "Boston, MA",            "region": "outside_sf"},
    {"country": "us", "where": "Chicago",    "distance": 25, "label": "Chicago, IL",           "region": "outside_sf"},
    {"country": "gb", "where": "Edinburgh",  "distance": 40, "label": "Edinburgh, Scotland",   "region": "outside_sf"},
    {"country": "gb", "where": "Glasgow",    "distance": 20, "label": "Glasgow, Scotland",     "region": "outside_sf"},
    {"country": "au", "where": "Sydney",     "distance": 30, "label": "Sydney, Australia",     "region": "outside_sf"},
    {"country": "au", "where": "Melbourne",  "distance": 30, "label": "Melbourne, Australia",  "region": "outside_sf"},
    {"country": "nz", "where": "Auckland",   "distance": 30, "label": "Auckland, New Zealand", "region": "outside_sf"},
    {"country": "sg", "where": "Singapore",  "distance": 30, "label": "Singapore",             "region": "outside_sf"},
]


# ── Adzuna ────────────────────────────────────────────────────────────────────

def _adzuna_search(app_id: str, app_key: str, country: str,
                   what: str, where: str, distance: int) -> list[dict]:
    """
    Fetch one page of results from the Adzuna jobs API.

    Parameters:
        app_id / app_key — Adzuna credentials from env
        country          — ISO country code (us, gb, au, nz, sg)
        what             — keyword search string
        where            — city / region name
        distance         — search radius in km

    Returns list of raw Adzuna result dicts.
    """
    params: dict = {
        "app_id": app_id,
        "app_key": app_key,
        "what": what,
        "results_per_page": 20,
        "sort_by": "date",
    }
    if where:
        params["where"] = where
        params["distance"] = distance
    url = f"{ADZUNA_BASE}/{country}/search/1?{urlencode(params)}"
    try:
        with urlopen(url, timeout=15) as resp:
            return json.loads(resp.read()).get("results", [])
    except HTTPError as exc:
        print(f"    Adzuna {exc.code} ({country}/{where}): {exc.read().decode()[:120]}")
        return []
    except Exception as exc:
        print(f"    Adzuna error ({country}/{where}): {exc}")
        return []


# ── Company deduplication ─────────────────────────────────────────────────────

def load_seen() -> dict:
    """
    Load seen_companies.json and prune entries older than 7 days.

    Returns dict mapping company name -> list of ISO date strings (last 7 days).
    """
    if not SEEN_FILE.exists():
        return {}
    try:
        data = json.loads(SEEN_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    cutoff = (date.today() - timedelta(days=7)).isoformat()
    return {
        company: [d for d in dates if d >= cutoff]
        for company, dates in data.items()
        if any(d >= cutoff for d in dates)
    }


def save_seen(seen: dict) -> None:
    SEEN_FILE.write_text(json.dumps(seen, indent=2) + "\n")


def _allowed(job: dict, seen: dict) -> bool:
    """Return True if the job's company has appeared fewer than 2 times this week."""
    company = (job.get("company") or {}).get("display_name", "")
    return len(seen.get(company, [])) < 2


# ── Candidate collection ──────────────────────────────────────────────────────

def _collect(app_id: str, app_key: str, loc: dict,
             terms: list[str], seen: dict) -> list[dict]:
    """
    Fetch job candidates for one location across the given search terms.

    Deduplicates by Adzuna job ID and applies the company-frequency filter.
    """
    seen_ids: set[str] = set()
    results = []
    for term in terms:
        for job in _adzuna_search(app_id, app_key, loc["country"], term,
                                  loc.get("where", ""), loc.get("distance", 30)):
            jid = str(job.get("id", ""))
            if not jid or jid in seen_ids:
                continue
            if not _allowed(job, seen):
                continue
            seen_ids.add(jid)
            job["_label"] = loc["label"]
            job["_region"] = loc["region"]
            results.append(job)
        time.sleep(0.3)  # be polite to the API
    return results


# ── Groq processing ───────────────────────────────────────────────────────────

def _groq_process(jobs: list[dict], client: Groq) -> list[dict]:
    """
    Send a batch of candidates to Groq for relevance scoring and summarization.

    Returns a list of dicts; irrelevant postings have relevant=false,
    relevant ones include pay_range, key_skills, bonus_skills, summary.
    """
    if not jobs:
        return []

    snippets = []
    for i, job in enumerate(jobs):
        title = job.get("title", "")
        company = (job.get("company") or {}).get("display_name", "")
        location = (job.get("location") or {}).get("display_name", "")
        desc = (job.get("description") or "")[:700]
        s_min, s_max = job.get("salary_min"), job.get("salary_max")
        salary = f"${s_min:,.0f}–${s_max:,.0f}/yr" if s_min and s_max else "not listed"
        snippets.append(
            f"[{i}] {title} @ {company} | {location} | Salary: {salary}\n{desc}"
        )

    prompt = f"""You are screening job postings for a senior electrical engineer.

Candidate profile:
- MS Electrical Engineering
- 7.5 years at Astranis (geostationary satellite comms), specializing in communications
  systems design and test, RF systems, signal processing, modem design, circuit design
- Last 4 years as engineering team lead — now targeting Senior IC roles
- Prefers to move away from aerospace/space industry
- NOT interested in: power electronics, power systems, antenna design

Review these {len(jobs)} postings. Return ONLY a valid JSON array with one object per posting.

For irrelevant: {{"idx": N, "relevant": false}}
For relevant:
{{
  "idx": N,
  "relevant": true,
  "pay_range": "e.g. $140K–$180K (estimate from role/location/seniority if not listed)",
  "top_requirements": [
    "Full sentence describing a specific technical requirement — e.g. 'Hands-on experience designing and validating OFDM modem architectures, from link budget through hardware bring-up'",
    "Second requirement as a full sentence",
    "Third requirement as a full sentence"
  ],
  "bonus_skills": ["2–4 short nice-to-have skills"],
  "summary": "Two sentences: what the role does and why it suits this candidate.",
  "is_startup": true or false
}}

For top_requirements: extract the 3 most specific, actionable technical requirements from the posting — written as full sentences describing what you would need to know or be able to do. These should be study-worthy (e.g. 'Proficiency in RF system analysis: noise figure, gain, IIP3, EVM characterization across the full signal chain'). Not generic buzzwords.
For is_startup: true if the company appears to be a startup or small company (<100 employees) based on language, name, or any context clues.

Postings:
{chr(10).join(snippets)}"""

    try:
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=3000,
        )
        raw = resp.choices[0].message.content.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        return json.loads(raw)
    except Exception as exc:
        print(f"    Groq error: {exc}")
        return []


def _build_posting(raw: dict, summary: dict) -> dict:
    """Merge Adzuna raw result with Groq summary into a clean posting record."""
    return {
        "title": raw.get("title", ""),
        "company": (raw.get("company") or {}).get("display_name", ""),
        "location_label": raw.get("_label", ""),
        "region": raw.get("_region", "outside_sf"),
        "url": raw.get("redirect_url", ""),
        "pay_range": summary.get("pay_range", ""),
        "top_requirements": summary.get("top_requirements", []),
        "bonus_skills": summary.get("bonus_skills", []),
        "summary": summary.get("summary", ""),
        "is_startup": bool(summary.get("is_startup", False)),
        "adzuna_id": str(raw.get("id", "")),
    }


# ── HTML generation ───────────────────────────────────────────────────────────

_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  background: #f4f5f7; color: #1a1a2e; min-height: 100vh;
}
header {
  background: #1a1a2e; color: white; padding: 14px 24px;
  display: flex; align-items: center; justify-content: space-between;
  gap: 16px; flex-wrap: wrap;
}
header h1 { font-size: 1.1rem; font-weight: 700; letter-spacing: 0.02em; }
.week-nav { display: flex; align-items: center; gap: 8px; }
.week-nav button {
  background: rgba(255,255,255,0.15); border: none; color: white;
  padding: 6px 14px; border-radius: 6px; cursor: pointer; font-size: 0.85rem;
  transition: background 0.15s;
}
.week-nav button:hover { background: rgba(255,255,255,0.27); }
.week-nav button:disabled { opacity: 0.3; cursor: default; }
.week-nav .date-label {
  font-size: 0.9rem; opacity: 0.85; min-width: 96px; text-align: center;
}
main { max-width: 1160px; margin: 0 auto; padding: 24px 16px; }
.columns { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
@media (max-width: 720px) { .columns { grid-template-columns: 1fr; } }
.col-header {
  font-size: 0.78rem; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.1em; margin-bottom: 14px;
  display: flex; align-items: center; gap: 8px; color: #333;
}
.dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; }
.dot-sf { background: #2563eb; }
.dot-non-sf { background: #7c3aed; }
.job-card {
  background: white; border-radius: 10px; padding: 16px;
  border: 1px solid #e4e5ef; margin-bottom: 14px;
  transition: box-shadow 0.15s;
}
.job-card:hover { box-shadow: 0 4px 18px rgba(0,0,0,0.07); }
.job-title { font-size: 0.95rem; font-weight: 600; margin-bottom: 5px; }
.job-title a { color: #1a1a2e; text-decoration: none; }
.job-title a:hover { color: #2563eb; }
.job-meta { display: flex; flex-wrap: wrap; gap: 7px; align-items: center; margin-bottom: 10px; }
.job-company { font-size: 0.84rem; color: #555; }
.loc-badge {
  font-size: 0.73rem; padding: 2px 8px; border-radius: 12px;
  background: #f0f0f9; color: #444; border: 1px solid #dde;
}
.pay-range {
  font-size: 0.84rem; font-weight: 600; color: #166534;
  background: #f0fdf4; border-radius: 6px; padding: 4px 10px;
  display: inline-block; margin-bottom: 10px; border: 1px solid #bbf7d0;
}
.skills-label {
  font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.08em;
  color: #999; margin-bottom: 6px; margin-top: 10px;
}
.req-list { list-style: none; padding: 0; margin: 0; }
.req-list li {
  font-size: 0.82rem; color: #1e293b; line-height: 1.5;
  padding: 5px 0 5px 20px; position: relative; border-bottom: 1px solid #f0f0f8;
}
.req-list li:last-child { border-bottom: none; }
.req-list li::before {
  content: counter(req-counter);
  counter-increment: req-counter;
  position: absolute; left: 0; top: 5px;
  font-size: 0.7rem; font-weight: 700; color: #2563eb;
  background: #eef2ff; border-radius: 50%;
  width: 15px; height: 15px; display: flex; align-items: center; justify-content: center;
  line-height: 15px; text-align: center;
}
.req-list { counter-reset: req-counter; }
.pill-row { display: flex; flex-wrap: wrap; gap: 5px; }
.pill { font-size: 0.74rem; padding: 3px 9px; border-radius: 12px; }
.pill-bonus { background: #fdf4ff; color: #6b21a8; border: 1px solid #e9d5ff; }
.startup-badge {
  font-size: 0.68rem; padding: 2px 7px; border-radius: 10px;
  background: #fff7ed; color: #c2410c; border: 1px solid #fed7aa;
}
.job-summary { font-size: 0.84rem; color: #444; line-height: 1.55; margin-top: 10px; }
.loading { text-align: center; color: #aaa; padding: 80px 0; }
.empty { text-align: center; color: #bbb; padding: 40px 0; font-size: 0.9rem; }
"""

_JS = """
let currentDate = AVAILABLE_DATES[AVAILABLE_DATES.length - 1] || null;

function fmtDate(iso) {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString('en-US',
    {weekday: 'short', month: 'short', day: 'numeric'});
}

function renderNav() {
  const idx = AVAILABLE_DATES.indexOf(currentDate);
  document.getElementById('week-nav').innerHTML = `
    <button onclick="navigate(-1)" ${idx <= 0 ? 'disabled' : ''}>&larr;</button>
    <span class="date-label">${currentDate ? fmtDate(currentDate) : '&mdash;'}</span>
    <button onclick="navigate(1)" ${idx >= AVAILABLE_DATES.length - 1 ? 'disabled' : ''}>&rarr;</button>
  `;
}

function navigate(dir) {
  const idx = AVAILABLE_DATES.indexOf(currentDate) + dir;
  if (idx >= 0 && idx < AVAILABLE_DATES.length) {
    currentDate = AVAILABLE_DATES[idx];
    loadDate(currentDate);
  }
}

function cardHtml(job) {
  const title = job.url
    ? `<a href="${job.url}" target="_blank" rel="noopener">${job.title}</a>`
    : job.title;

  const startupBadge = job.is_startup
    ? `<span class="startup-badge">Startup</span>`
    : '';

  const reqList = (job.top_requirements || []).length
    ? `<div class="skills-label">What You'd Need to Know</div>
       <ol class="req-list">${job.top_requirements.map(r => `<li>${r}</li>`).join('')}</ol>`
    : '';

  const bonusPills = (job.bonus_skills || []).length
    ? `<div class="skills-label">Bonus</div>
       <div class="pill-row">${job.bonus_skills.map(s => `<span class="pill pill-bonus">${s}</span>`).join('')}</div>`
    : '';

  return `<div class="job-card">
    <div class="job-title">${title}</div>
    <div class="job-meta">
      <span class="job-company">${job.company}</span>
      <span class="loc-badge">${job.location_label}</span>
      ${startupBadge}
    </div>
    ${job.pay_range ? `<div class="pay-range">${job.pay_range}</div>` : ''}
    ${reqList}${bonusPills}
    ${job.summary ? `<div class="job-summary">${job.summary}</div>` : ''}
  </div>`;
}

function renderData(data) {
  const sf    = (data.postings || []).filter(p => p.region === 'sf');
  const nonSf = (data.postings || []).filter(p => p.region === 'outside_sf');
  const sfHtml    = sf.length    ? sf.map(cardHtml).join('')    : '<div class="empty">No SF postings today</div>';
  const nonSfHtml = nonSf.length ? nonSf.map(cardHtml).join('') : '<div class="empty">No postings outside SF today</div>';
  document.getElementById('main-content').innerHTML = `
    <div class="columns">
      <div>
        <div class="col-header"><span class="dot dot-sf"></span>SF Bay Area</div>
        ${sfHtml}
      </div>
      <div>
        <div class="col-header"><span class="dot dot-non-sf"></span>Beyond SF</div>
        ${nonSfHtml}
      </div>
    </div>`;
}

async function loadDate(iso) {
  renderNav();
  if (!iso) {
    document.getElementById('main-content').innerHTML = '<div class="empty">No data yet — check back after the first run.</div>';
    return;
  }
  document.getElementById('main-content').innerHTML = '<div class="loading">Loading&hellip;</div>';
  try {
    const resp = await fetch(`${iso}.json`);
    if (!resp.ok) throw new Error(resp.status);
    renderData(await resp.json());
  } catch {
    document.getElementById('main-content').innerHTML =
      `<div class="empty">Could not load data for ${fmtDate(iso)}.</div>`;
  }
}

loadDate(currentDate);
"""


def build_html(available_dates: list[str]) -> str:
    """Generate the jobs/index.html shell. Data is fetched on demand from daily JSON files."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Market Research</title>
  <style>{_CSS}</style>
</head>
<body>
  <header>
    <h1>Market Research</h1>
    <div class="week-nav" id="week-nav"></div>
  </header>
  <main id="main-content">
    <div class="loading">Loading&hellip;</div>
  </main>
  <script>
    const AVAILABLE_DATES = {json.dumps(available_dates)};
    {_JS}
  </script>
</body>
</html>
"""


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    """
    Orchestrate the daily job research run:
    1. Fetch candidates from Adzuna (SF + rotated non-SF locations)
    2. Filter by company frequency
    3. Score and summarize via Groq
    4. Select 3 SF + 3 non-SF postings
    5. Write daily JSON, update seen_companies.json, rebuild index.html
    """
    app_id = os.environ["ADZUNA_APP_ID"]
    app_key = os.environ["ADZUNA_APP_KEY"]
    client = Groq(api_key=os.environ["GROQ_API_KEY"])

    today = date.today().isoformat()
    JOBS_DIR.mkdir(exist_ok=True)
    out_file = JOBS_DIR / f"{today}.json"

    if out_file.exists():
        print(f"Already generated for {today}.")
        _rebuild_index()
        return

    seen = load_seen()
    day_idx = date.today().timetuple().tm_yday

    # Two search terms today, rotating through the list
    terms = [
        SEARCH_TERMS[day_idx % len(SEARCH_TERMS)],
        SEARCH_TERMS[(day_idx + 1) % len(SEARCH_TERMS)],
    ]
    print(f"Search terms: {terms}")

    # Four non-SF locations today, rotating through the list
    non_sf_locs = [NON_SF_LOCATIONS[(day_idx + i) % len(NON_SF_LOCATIONS)] for i in range(4)]

    print("Fetching SF Bay Area candidates…")
    sf_pool = _collect(app_id, app_key, SF_LOCATION, terms, seen)
    print(f"  {len(sf_pool)} general candidates")

    # Startup-targeted SF search: appending "startup" to the query surfaces smaller companies
    startup_terms = [f"{t} startup" for t in terms[:1]]
    sf_startup_pool = _collect(app_id, app_key, SF_STARTUP_LOCATION, startup_terms, seen)
    # Mark startup candidates so we can prioritize them in selection
    for job in sf_startup_pool:
        job["_startup_search"] = True
    print(f"  {len(sf_startup_pool)} startup-search candidates")

    print("Fetching non-SF candidates…")
    non_sf_pool: list[dict] = []
    for loc in non_sf_locs:
        batch = _collect(app_id, app_key, loc, [terms[0]], seen)
        print(f"  {loc['label']}: {len(batch)} candidates")
        non_sf_pool.extend(batch)

    # Interleave startup candidates with general SF so Groq sees them mixed
    all_pool = sf_startup_pool[:8] + sf_pool[:12] + non_sf_pool[:20]
    print(f"\nSending {len(all_pool)} candidates to Groq…")
    summaries = _groq_process(all_pool, client)
    summary_map = {s["idx"]: s for s in summaries if s.get("relevant")}
    print(f"  {len(summary_map)} relevant")

    sf_postings: list[dict] = []
    non_sf_postings: list[dict] = []
    used_companies: set[str] = set()
    sf_has_startup = False

    # Two passes for SF: first pass fills up to 2 slots + reserves slot 3 for a startup;
    # second pass fills any remaining slots if no startup was found.
    for pass_num in range(2):
        for idx, raw in enumerate(all_pool):
            if len(sf_postings) >= 3 and len(non_sf_postings) >= 3:
                break
            if idx not in summary_map:
                continue
            company = (raw.get("company") or {}).get("display_name", "")
            if company in used_companies:
                continue
            posting = _build_posting(raw, summary_map[idx])

            if raw["_region"] == "sf" and len(sf_postings) < 3:
                is_startup = posting["is_startup"] or raw.get("_startup_search", False)
                if pass_num == 0:
                    # First pass: take up to 2 non-startup SF jobs, plus any startup
                    if is_startup and not sf_has_startup:
                        sf_postings.append(posting)
                        used_companies.add(company)
                        sf_has_startup = True
                    elif not is_startup and len(sf_postings) < (2 if not sf_has_startup else 3):
                        sf_postings.append(posting)
                        used_companies.add(company)
                else:
                    # Second pass: fill remaining SF slots without startup restriction
                    sf_postings.append(posting)
                    used_companies.add(company)

            elif raw["_region"] == "outside_sf" and len(non_sf_postings) < 3:
                non_sf_postings.append(posting)
                used_companies.add(company)

    all_postings = sf_postings + non_sf_postings
    print(f"Selected: {len(sf_postings)} SF, {len(non_sf_postings)} non-SF")

    out_file.write_text(json.dumps({"date": today, "postings": all_postings}, indent=2) + "\n")
    print(f"Wrote {out_file.name}")

    # Record companies shown today
    for p in all_postings:
        if p["company"]:
            seen.setdefault(p["company"], [])
            if today not in seen[p["company"]]:
                seen[p["company"]].append(today)
    save_seen(seen)

    # Prune old JSON files
    cutoff = (date.today() - timedelta(days=HISTORY_DAYS)).isoformat()
    for f in JOBS_DIR.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].json"):
        if f.stem < cutoff:
            f.unlink()
            print(f"Pruned {f.name}")

    _rebuild_index()


def _rebuild_index() -> None:
    """Regenerate jobs/index.html from whatever daily JSON files exist."""
    available = sorted(
        f.stem for f in JOBS_DIR.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].json")
    )
    (JOBS_DIR / "index.html").write_text(build_html(available))
    print("Rebuilt jobs/index.html")


if __name__ == "__main__":
    main()
