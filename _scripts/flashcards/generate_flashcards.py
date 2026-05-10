"""
Daily flashcard generator.

Each day's cards are saved to flashcards/YYYY-MM-DD.json. Files older than
7 days are deleted. flashcards/index.html is rebuilt each run as a JS app
shell with the available date list embedded for week-history navigation.

If flashcards/favorites.json exists (committed by the user after exporting
from the browser UI), its titles are passed to Groq as style/depth examples.
"""

import json
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from groq import Groq

OUTPUT_DIR = Path(__file__).parent.parent.parent / "flashcards"
FAVORITES_PATH = OUTPUT_DIR / "favorites.json"
HISTORY_DAYS = 7

TOPICS = [
    {
        "id": "ee_comms",
        "label": "EE · Comms & Space",
        "color": "#0d47a1",
        "rss": ["https://spacenews.com/feed/"],
        "prompt": (
            "You are an expert electrical engineer specializing in communications systems "
            "and space technology. Write a flashcard about a recent or notable development "
            "in RF communications, satellite systems, phased arrays, link budgets, orbital "
            "mechanics, or space missions. Use any provided headlines for inspiration. "
            "Assume the reader has an MS in EE with a communications focus."
        ),
    },
    {
        "id": "ee_core",
        "label": "EE · Core Concepts",
        "color": "#1565c0",
        "rss": [],
        "prompt": (
            "You are an expert electrical engineer. Write a flashcard on a specific, "
            "non-trivial concept from signal processing, analog/RF circuit design, control "
            "systems, power electronics, or electromagnetics. Rotate across subdisciplines "
            "and avoid repeating obvious fundamentals. Include equations or quantitative "
            "reasoning where helpful. Assume the reader has an MS in EE."
        ),
    },
    {
        "id": "news",
        "label": "News",
        "color": "#c62828",
        "rss": [
            "https://feeds.reuters.com/reuters/topNews",
            "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
        ],
        "prompt": (
            "You are a clear-eyed journalist. Write a flashcard summarizing one important "
            "current event or geopolitical development. Present facts neutrally. "
            "Use the provided headlines as your source material — pick the most substantive "
            "story. Avoid opinion. Explain what happened and why it matters."
        ),
    },
    {
        "id": "history",
        "label": "History",
        "color": "#4a148c",
        "rss": [],
        "prompt": (
            "You are a historian specializing in US and world history from WW1 to the present. "
            "Write a flashcard on a specific, illuminating historical event, decision, or figure. "
            "Choose topics that reveal how the modern world was shaped. "
            "Name dates, people, and consequences. Rotate through different eras and regions."
        ),
    },
    {
        "id": "ai",
        "label": "AI & Tools",
        "color": "#00695c",
        "rss": [],
        "prompt": (
            "You are an AI researcher and practitioner. Write a flashcard about a specific "
            "AI technique, recent model development, research result, or practical tool. "
            "Topics can include transformer architectures, training methods, inference "
            "optimization, agent frameworks, or evaluation approaches. "
            "Be technically precise. Assume the reader can read Python and understands ML basics."
        ),
    },
]

_CSS = """* { box-sizing: border-box; margin: 0; padding: 0; }

body {
  font-family: monospace;
  background: #f8f8f8;
  color: #111;
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

header {
  background: #111;
  color: #fff;
  padding: 14px 20px;
  display: flex;
  align-items: center;
  gap: 16px;
}
header a { color: #aaa; text-decoration: none; font-size: 0.85rem; }
header a:hover { color: #fff; }
header h1 { font-size: 1rem; }

main {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 24px 16px 48px;
  max-width: 720px;
  width: 100%;
  margin: 0 auto;
}

.week-nav {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 20px;
  width: 100%;
}
.week-btn {
  padding: 6px 12px;
  font-family: monospace;
  font-size: 0.78rem;
  background: #fff;
  border: 1px solid #ccc;
  cursor: pointer;
  color: #444;
}
.week-btn:hover { background: #f0f0f0; }
.week-btn.active { background: #111; color: #fff; border-color: #111; }

.deck-nav {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 16px;
  width: 100%;
  justify-content: center;
}
.dot {
  width: 10px; height: 10px; border-radius: 50%;
  background: #ccc;
  cursor: pointer;
  border: none;
  padding: 0;
  transition: background 0.15s;
}
.dot.active { background: var(--cc); }

.card {
  width: 100%;
  background: #fff;
  border: 1px solid #ddd;
  border-top: 5px solid var(--cc);
  padding: 24px 24px 20px;
  display: none;
}
.card.visible { display: block; }

.card-top {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 10px;
}
.card-label {
  font-size: 0.7rem;
  font-weight: bold;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--cc);
}
.save-btn {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 1.2rem;
  color: #ccc;
  padding: 0 0 0 10px;
  line-height: 1;
  transition: color 0.15s;
  flex-shrink: 0;
}
.save-btn:hover { color: #999; }
.save-btn.saved { color: #e6a817; }

.card h2 {
  font-size: 1.05rem;
  margin-bottom: 16px;
  line-height: 1.4;
}
.card-body p {
  font-size: 0.875rem;
  line-height: 1.65;
  color: #222;
  margin-bottom: 12px;
}
.card-body p:last-child { margin-bottom: 0; }

.why-section {
  margin-top: 18px;
  padding: 12px 14px;
  background: #f7f7f7;
  border-left: 3px solid var(--cc);
}
.why-label {
  font-size: 0.65rem;
  font-weight: bold;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--cc);
  margin-bottom: 6px;
}
.why-section p {
  font-size: 0.85rem;
  line-height: 1.6;
  color: #333;
}

.card-sources {
  margin-top: 18px;
  padding-top: 12px;
  border-top: 1px solid #eee;
  font-size: 0.75rem;
  color: #777;
}
.card-sources strong { color: #444; }
.card-sources a { color: #555; text-decoration: underline; }
.card-sources a:hover { color: #000; }

.prev-next {
  display: flex;
  gap: 12px;
  margin-top: 16px;
  width: 100%;
}
.prev-next button {
  flex: 1;
  padding: 10px;
  font-family: monospace;
  font-size: 0.9rem;
  background: #111;
  color: #fff;
  border: none;
  cursor: pointer;
}
.prev-next button:hover { background: #333; }
.prev-next button:disabled { background: #ccc; cursor: default; }

.saved-panel {
  width: 100%;
  margin-top: 28px;
  border: 1px solid #ddd;
  background: #fff;
}
#saved-toggle {
  width: 100%;
  text-align: left;
  padding: 12px 16px;
  font-family: monospace;
  font-size: 0.85rem;
  background: none;
  border: none;
  cursor: pointer;
  color: #333;
}
#saved-toggle:hover { background: #f5f5f5; }
#saved-content {
  border-top: 1px solid #eee;
  padding: 12px 16px;
}
.saved-item {
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding: 8px 0;
  border-bottom: 1px solid #f0f0f0;
  font-size: 0.8rem;
}
.saved-item:last-of-type { border-bottom: none; }
.saved-topic {
  font-size: 0.65rem;
  font-weight: bold;
  color: var(--cc);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  flex-shrink: 0;
}
.saved-title { flex: 1; color: #222; }
.saved-date { color: #aaa; font-size: 0.7rem; flex-shrink: 0; }
.no-saved { color: #999; font-size: 0.85rem; padding: 4px 0; }
#export-btn {
  margin-top: 14px;
  padding: 8px 14px;
  font-family: monospace;
  font-size: 0.8rem;
  background: #111;
  color: #fff;
  border: none;
  cursor: pointer;
}
#export-btn:hover { background: #333; }
.export-note {
  margin-top: 8px;
  font-size: 0.72rem;
  color: #999;
  line-height: 1.5;
}

.loading, .error-msg {
  font-size: 0.85rem;
  color: #999;
  padding: 40px 0;
  text-align: center;
  width: 100%;
}
.error-msg { color: #c62828; }

@media (max-width: 480px) {
  .card { padding: 18px 16px 16px; }
  .week-btn { font-size: 0.72rem; padding: 5px 10px; }
}"""

_JS = """
let currentDate = AVAILABLE_DATES[0] || null;
let currentDeck = null;
let currentCard = 0;

function formatDateLabel(dateStr) {
  const todayStr = new Date().toISOString().split('T')[0];
  const yesterdayStr = new Date(Date.now() - 864e5).toISOString().split('T')[0];
  if (dateStr === todayStr) return 'Today';
  if (dateStr === yesterdayStr) return 'Yesterday';
  const d = new Date(dateStr + 'T12:00:00Z');
  return d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', timeZone: 'UTC' });
}

function buildWeekNav() {
  const nav = document.getElementById('week-nav');
  nav.innerHTML = '';
  AVAILABLE_DATES.forEach(function(dateStr) {
    const btn = document.createElement('button');
    btn.className = 'week-btn' + (dateStr === currentDate ? ' active' : '');
    btn.textContent = formatDateLabel(dateStr);
    btn.onclick = function() { loadDeck(dateStr); };
    nav.appendChild(btn);
  });
}

async function loadDeck(dateStr) {
  currentDate = dateStr;
  buildWeekNav();
  document.getElementById('cards-container').innerHTML = '<p class="loading">Loading…</p>';
  document.getElementById('dot-nav').innerHTML = '';
  document.getElementById('prev-btn').disabled = true;
  document.getElementById('next-btn').disabled = true;
  try {
    const resp = await fetch(dateStr + '.json');
    if (!resp.ok) throw new Error('not found');
    currentDeck = await resp.json();
    currentCard = 0;
    renderDeck();
  } catch(e) {
    document.getElementById('cards-container').innerHTML =
      '<p class="error-msg">Could not load flashcards for this date.</p>';
  }
}

function renderDeck() {
  if (!currentDeck) return;
  const cards = currentDeck.cards || [];

  const dotNav = document.getElementById('dot-nav');
  dotNav.innerHTML = '';
  cards.forEach(function(card, i) {
    const dot = document.createElement('button');
    dot.className = 'dot' + (i === 0 ? ' active' : '');
    dot.setAttribute('aria-label', 'Card ' + (i + 1));
    const meta = TOPICS_CONFIG[card.id] || {};
    dot.style.setProperty('--cc', meta.color || '#888');
    dot.onclick = function() { showCard(i); };
    dotNav.appendChild(dot);
  });

  const container = document.getElementById('cards-container');
  container.innerHTML = '';
  cards.forEach(function(card, i) { container.appendChild(makeCardEl(card, i)); });
  updateNavBtns();
}

function makeCardEl(card, idx) {
  const meta = TOPICS_CONFIG[card.id] || { label: card.id, color: '#888' };
  const saved = isSaved(currentDate, card.id);

  const div = document.createElement('div');
  div.className = 'card' + (idx === currentCard ? ' visible' : '');
  div.style.setProperty('--cc', meta.color);

  const paras = (card.body || []).map(function(p) { return '<p>' + p + '</p>'; }).join('');

  const whyHtml = card.why
    ? '<div class="why-section"><div class="why-label">Why it matters</div><p>' + card.why + '</p></div>'
    : '';

  const srcLinks = (card.sources || [])
    .map(function(s) { return '<a href="' + s.url + '" target="_blank" rel="noopener">' + s.name + '</a>'; })
    .join(', ');
  const sourcesHtml = srcLinks
    ? '<div class="card-sources"><strong>Sources:</strong> ' + srcLinks + '</div>'
    : '';

  div.innerHTML =
    '<div class="card-top">' +
      '<div class="card-label">' + meta.label + '</div>' +
      '<button class="save-btn' + (saved ? ' saved' : '') + '" title="' + (saved ? 'Unsave' : 'Save as favorite') + '">' +
        (saved ? '★' : '☆') +
      '</button>' +
    '</div>' +
    '<h2>' + (card.title || '') + '</h2>' +
    '<div class="card-body">' + paras + '</div>' +
    whyHtml +
    sourcesHtml;

  div.querySelector('.save-btn').onclick = function(e) {
    const btn = e.currentTarget;
    const nowSaved = toggleSave(card);
    btn.textContent = nowSaved ? '★' : '☆';
    btn.classList.toggle('saved', nowSaved);
    btn.title = nowSaved ? 'Unsave' : 'Save as favorite';
    updateSavedPanel();
  };

  return div;
}

function showCard(idx) {
  currentCard = idx;
  document.querySelectorAll('.card').forEach(function(c, i) { c.classList.toggle('visible', i === idx); });
  document.querySelectorAll('.dot').forEach(function(d, i) { d.classList.toggle('active', i === idx); });
  updateNavBtns();
}

function updateNavBtns() {
  const total = currentDeck ? (currentDeck.cards || []).length : 0;
  document.getElementById('prev-btn').disabled = currentCard === 0;
  document.getElementById('next-btn').disabled = currentCard >= total - 1;
}

function getFavorites() {
  try { return JSON.parse(localStorage.getItem('flashcard-favorites') || '[]'); }
  catch(e) { return []; }
}

function isSaved(dateStr, topicId) {
  return getFavorites().some(function(f) { return f.date === dateStr && f.topic === topicId; });
}

function toggleSave(card) {
  var favs = getFavorites();
  var match = function(f) { return f.date === currentDate && f.topic === card.id; };
  if (favs.some(match)) {
    favs = favs.filter(function(f) { return !match(f); });
    localStorage.setItem('flashcard-favorites', JSON.stringify(favs));
    return false;
  }
  favs.push({
    saved_at: new Date().toISOString().split('T')[0],
    date: currentDate,
    topic: card.id,
    title: card.title,
    body: card.body || [],
    why: card.why || '',
    sources: card.sources || [],
  });
  localStorage.setItem('flashcard-favorites', JSON.stringify(favs));
  return true;
}

function updateSavedPanel() {
  var favs = getFavorites();
  var n = favs.length;
  document.getElementById('saved-toggle').textContent =
    (n > 0 ? '★' : '☆') + ' Saved cards (' + n + ')';

  var list = document.getElementById('saved-list');
  if (n === 0) {
    list.innerHTML = '<p class="no-saved">No saved cards yet. Click ☆ on any card to save it.</p>';
    return;
  }
  list.innerHTML = favs.slice().reverse().map(function(f) {
    var meta = TOPICS_CONFIG[f.topic] || { label: f.topic, color: '#888' };
    return '<div class="saved-item" style="--cc:' + meta.color + '">' +
      '<span class="saved-topic">' + meta.label + '</span>' +
      '<span class="saved-title">' + f.title + '</span>' +
      '<span class="saved-date">' + f.date + '</span>' +
      '</div>';
  }).join('');
}

function exportFavorites() {
  var favs = getFavorites();
  var blob = new Blob([JSON.stringify(favs, null, 2)], { type: 'application/json' });
  var url = URL.createObjectURL(blob);
  var a = document.createElement('a');
  a.href = url;
  a.download = 'favorites.json';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

document.getElementById('prev-btn').onclick = function() {
  if (currentCard > 0) showCard(currentCard - 1);
};
document.getElementById('next-btn').onclick = function() {
  if (currentDeck && currentCard < (currentDeck.cards || []).length - 1) showCard(currentCard + 1);
};
document.getElementById('saved-toggle').onclick = function() {
  var content = document.getElementById('saved-content');
  var willShow = content.hidden;
  content.hidden = !willShow;
  if (willShow) updateSavedPanel();
};
document.getElementById('export-btn').onclick = exportFavorites;

buildWeekNav();
updateSavedPanel();
if (AVAILABLE_DATES.length > 0) loadDeck(AVAILABLE_DATES[0]);
"""


def fetch_headlines(urls: list[str], limit: int = 8) -> list[str]:
    """
    Fetches RSS feeds and returns up to `limit` headline strings.

    Parameters
    ----------
    urls : list[str]
        RSS feed URLs to try, in order.
    limit : int
        Max total headlines to return across all feeds.
    """
    headlines: list[str] = []
    for url in urls:
        if len(headlines) >= limit:
            break
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "flashcard-bot/1.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                body = resp.read()
            root = ET.fromstring(body)
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            for item in root.iter("item"):
                title = item.findtext("title", "").strip()
                if title:
                    headlines.append(title)
                if len(headlines) >= limit:
                    break
            if not headlines:
                for entry in root.findall("atom:entry", ns):
                    title_el = entry.find("atom:title", ns)
                    if title_el is not None and title_el.text:
                        headlines.append(title_el.text.strip())
                    if len(headlines) >= limit:
                        break
        except Exception:
            pass
    return headlines[:limit]


def load_favorites() -> list[dict]:
    """Reads favorites.json from the flashcards directory if it exists."""
    if FAVORITES_PATH.exists():
        try:
            return json.loads(FAVORITES_PATH.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _favorites_context(favorites: list[dict], topic_id: str) -> str:
    """
    Formats saved favorites as a Groq system-prompt appendix.

    Prioritizes examples from the same topic, then fills with others,
    capped at 5 total to stay concise.

    Parameters
    ----------
    favorites : list[dict]
        All saved favorites from favorites.json.
    topic_id : str
        The topic being generated — used to surface most-relevant examples first.
    """
    if not favorites:
        return ""
    same = [f for f in favorites if f.get("topic") == topic_id]
    other = [f for f in favorites if f.get("topic") != topic_id]
    examples = (same[-3:] + other[-2:])[:5]
    lines = [
        "\n\nThe user has saved these flashcards as favorites — match their depth, "
        "specificity, and writing style:"
    ]
    for fav in examples:
        lines.append(f"  [{fav.get('topic', '?')}] {fav.get('title', '?')}")
    return "\n".join(lines)


def generate_card(
    client: Groq,
    topic: dict,
    headlines: list[str],
    today: str,
    favorites: list[dict],
) -> dict:
    """
    Calls Groq to generate one flashcard for the given topic.

    Parameters
    ----------
    client : Groq
        Initialized Groq client.
    topic : dict
        Topic config dict from TOPICS.
    headlines : list[str]
        Recent headlines for context (may be empty).
    today : str
        ISO date string for grounding the prompt.
    favorites : list[dict]
        User-saved favorites used as style feedback.

    Returns
    -------
    dict
        Card dict with keys: id, title, body, why, sources.
    """
    headline_block = ""
    if headlines:
        headline_block = "\n\nRecent headlines for context:\n" + "\n".join(
            f"- {h}" for h in headlines
        )

    system = topic["prompt"] + _favorites_context(favorites, topic["id"])
    user = (
        f"Today is {today}. Generate exactly ONE flashcard in this JSON format:\n\n"
        '{"id": "<topic_id>", "title": "<specific descriptive title>", '
        '"body": ["<paragraph 1>", "<paragraph 2>", ...], '
        '"why": "<one concise paragraph: high-level significance, real-world impact, '
        'or why an engineer should care — written in plain English accessible to a non-specialist>", '
        '"sources": [{"name": "<source name>", "url": "<url>"}]}\n\n'
        "Requirements:\n"
        "- title: specific and informative (not generic)\n"
        "- body: 2-5 paragraphs, technically accurate, self-contained\n"
        "- why: exactly 1 paragraph, plain English, explains broader significance\n"
        "- sources: 1-3 real, reputable sources with real URLs\n"
        f'- id field must be: "{topic["id"]}"\n'
        "- respond with ONLY the JSON object, no markdown fences"
        + headline_block
    )

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=2000,
        temperature=0.72,
    )

    raw = response.choices[0].message.content.strip()
    raw = re.sub(r"^```[a-z]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)
    # Escape lone backslashes that aren't valid JSON escapes (e.g. LaTeX: \frac, \omega)
    raw = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', raw)
    return json.loads(raw)


def cleanup_old_json() -> None:
    """Deletes dated JSON files older than HISTORY_DAYS days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)).date()
    for f in OUTPUT_DIR.glob("????-??-??.json"):
        try:
            file_date = date.fromisoformat(f.stem)
            if file_date < cutoff:
                f.unlink()
                print(f"  Deleted old: {f.name}")
        except ValueError:
            pass


def get_available_dates() -> list[str]:
    """Returns ISO date strings for all existing daily JSON files, newest first."""
    dates = []
    for f in OUTPUT_DIR.glob("????-??-??.json"):
        try:
            date.fromisoformat(f.stem)
            dates.append(f.stem)
        except ValueError:
            pass
    return sorted(dates, reverse=True)


def build_html(available_dates: list[str]) -> str:
    """
    Generates the flashcards index.html shell.

    The page is a JS app that fetches YYYY-MM-DD.json on demand. The list of
    available dates and topic metadata are embedded as JS constants so the
    week-nav can be built without additional requests.

    Parameters
    ----------
    available_dates : list[str]
        ISO date strings for all existing daily JSON files, newest first.
    """
    dates_json = json.dumps(available_dates)
    topics_json = json.dumps(
        {t["id"]: {"label": t["label"], "color": t["color"]} for t in TOPICS}
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Daily Flashcards</title>
  <style>{_CSS}</style>
</head>
<body>
  <header>
    <a href="/">&#x2190; Home</a>
    <h1>Daily Flashcards</h1>
  </header>
  <main>
    <div class="week-nav" id="week-nav"></div>
    <div class="deck-nav" id="dot-nav"></div>
    <div id="cards-container"></div>
    <div class="prev-next">
      <button id="prev-btn" disabled>&#x2190; Prev</button>
      <button id="next-btn">Next &#x2192;</button>
    </div>
    <div class="saved-panel">
      <button id="saved-toggle">&#x2606; Saved cards (0)</button>
      <div id="saved-content" hidden>
        <div id="saved-list"></div>
        <button id="export-btn">Download favorites.json</button>
        <p class="export-note">
          Commit the downloaded file to flashcards/favorites.json in the repo.<br>
          The generator will use your saved cards as style examples for future decks.
        </p>
      </div>
    </div>
  </main>
  <script>
const AVAILABLE_DATES = {dates_json};
const TOPICS_CONFIG = {topics_json};
{_JS}
  </script>
</body>
</html>
"""


def main() -> None:
    """Fetches RSS, generates cards via Groq, writes daily JSON and rebuilds index.html."""
    client = Groq(api_key=os.environ["GROQ_API_KEY"])
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    generated_at = datetime.now(timezone.utc).strftime("%B %d, %Y at %H:%M UTC")

    favorites = load_favorites()
    if favorites:
        print(f"  Loaded {len(favorites)} saved favorites for Groq context")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    cards = []
    for topic in TOPICS:
        headlines = fetch_headlines(topic["rss"]) if topic["rss"] else []
        card = generate_card(client, topic, headlines, today, favorites)
        cards.append(card)
        print(f"  [{topic['id']}] {card.get('title', '(no title)')}")

    today_data = {
        "date": today,
        "generated_at": generated_at,
        "cards": cards,
    }
    (OUTPUT_DIR / f"{today}.json").write_text(
        json.dumps(today_data, indent=2), encoding="utf-8"
    )

    cleanup_old_json()

    available_dates = get_available_dates()
    html = build_html(available_dates)
    (OUTPUT_DIR / "index.html").write_text(html, encoding="utf-8")
    print(f"Wrote index.html ({len(available_dates)} date(s) available)")


if __name__ == "__main__":
    main()
