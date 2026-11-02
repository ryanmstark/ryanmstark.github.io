// Apartment Summary page: renders apartments/listings.json.
// The JSON is fetched fresh on every load (no cache), and a GitHub Action
// re-checks each posting link on a schedule and marks removed ones.

const DAY = 24 * 60 * 60 * 1000;

const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const fmt = n => '$' + n.toLocaleString('en-US');

// Date-only strings ("2026-10-31") are treated as noon Pacific that day.
function parseDate(d) {
  if (!d) return null;
  const t = new Date(d.length === 10 ? d + 'T12:00:00-08:00' : d);
  return isNaN(t) ? null : t;
}

function sortKey(l) {
  const t = parseDate(l.posted) || parseDate(l.updated);
  return t ? t.getTime() : 0;
}

function isNew(l) {
  if (!l.posted || l.posted_approx || l.posted.length === 10) return false;
  const t = parseDate(l.posted);
  return t && (Date.now() - t.getTime()) < DAY && t.getTime() <= Date.now() + 60000;
}

function ago(t) {
  const ms = Date.now() - t.getTime();
  const h = Math.round(ms / 3600000);
  if (h < 1) return 'just now';
  if (h < 24) return `${h} hr${h === 1 ? '' : 's'} ago`;
  const d = Math.round(ms / DAY);
  return `${d} day${d === 1 ? '' : 's'} ago`;
}

const short = t => t.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'America/Los_Angeles' });

function dateLine(l) {
  const p = parseDate(l.posted), u = parseDate(l.updated);
  if (p) return `<b>Posted ${l.posted_approx ? '~' : ''}${short(p)}</b> (${ago(p)})${u && u > p ? ` · updated ${short(u)}` : ''}`;
  if (u) return `Post date not shown · <b>updated ${short(u)}</b> (${ago(u)})`;
  return 'Post date not shown';
}

function card(l, i) {
  const tags = l.tags.map(([t, k]) => `<span class="tag ${k}">${esc(t)}</span>`).join('');
  const notes = l.notes.map(n => `<p>${n}</p>`).join('');
  const url = esc(l.url);
  const fresh = isNew(l);
  return `
    <div class="card${fresh ? ' is-new' : ''}">
      ${fresh ? '<div class="new-banner">★ New posting · last 24 hrs</div>' : ''}
      <div class="mini-map" id="map-${i}"></div>
      ${l.approx ? '<div class="map-note">Pin is approximate: exact address not confirmed.</div>' : ''}
      <div class="card-head">
        <div class="rank">${esc(l.src)}</div>
        <h2><a href="${url}" target="_blank" rel="noopener">${esc(l.name)} ↗</a></h2>
        <div class="hood">${esc(l.hood)}</div>
        <div class="date">${dateLine(l)}</div>
        <div class="price">${fmt(l.rent)}<small>/mo · ${esc(l.allin)}</small></div>
      </div>
      <div class="tags">${tags}</div>
      <div class="body">${notes}
        <p class="scam"><b>Scam check: ${esc(l.scam[0])}.</b> ${esc(l.scam[1])}</p>
        <p style="color:#777">${esc(l.who)}</p>
      </div>
      <a class="open-btn" href="${url}" target="_blank" rel="noopener">Open posting on ${esc(l.src)} ↗</a>
    </div>`;
}

function render(data) {
  const live = x => x.status !== 'removed';
  const unique = data.unique.filter(live).sort((a, b) => sortKey(b) - sortKey(a));
  const complexes = data.complexes.filter(live).sort((a, b) => sortKey(b) - sortKey(a));

  if (data.checked_at) {
    const c = parseDate(data.checked_at);
    if (c) document.getElementById('checked').textContent = `Links last checked ${ago(c)}.`;
  }

  document.getElementById('unique').innerHTML = unique.length
    ? unique.map(card).join('')
    : '<p class="section-note">No active listings right now.</p>';

  let n = 0;
  complexes.forEach(c => { c.num = c.lat != null ? ++n : null; });
  document.getElementById('complexes').innerHTML = complexes.map(c => {
    const p = parseDate(c.posted);
    const fresh = isNew(c);
    return `
      <tr class="${fresh ? 'is-new' : ''}">
        <td class="num">${c.num ?? '–'}</td>
        <td><a href="${esc(c.url)}" target="_blank" rel="noopener"><b>${esc(c.name)}</b> ↗</a>${fresh ? '<span class="new-chip">NEW</span>' : ''}</td>
        <td class="addr">${esc(c.addr)}</td>
        <td>${esc(c.area)}</td>
        <td>${p ? `${short(p)}<br><span class="addr">${ago(p)}</span>` : '<span class="addr">not shown</span>'}</td>
        <td>${esc(c.price)}</td>
        <td>${esc(c.parking)}</td>
        <td>${esc(c.notes)}</td>
        <td class="count">${esc(c.seen)}</td>
      </tr>`;
  }).join('');

  drawMaps(unique, complexes);
}

function drawMaps(unique, complexes) {
  if (!window.L) {
    document.querySelectorAll('.mini-map, #complex-map').forEach(el => el.style.display = 'none');
    return;
  }
  const tiles = () => L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors', maxZoom: 19
  });
  const pin = (label, cls) => L.divIcon({ className: 'pin ' + (cls || ''), html: label, iconSize: null, iconAnchor: cls === 'dot' ? [8, 8] : [12, 12] });

  unique.forEach((l, i) => {
    const m = L.map('map-' + i, {
      zoomControl: false, attributionControl: false, scrollWheelZoom: false, dragging: false,
      doubleClickZoom: false, boxZoom: false, keyboard: false, touchZoom: false
    }).setView([l.lat, l.lng], 15);
    tiles().addTo(m);
    L.marker([l.lat, l.lng], { icon: pin('', 'dot'), interactive: false }).addTo(m);
  });

  const pts = complexes.filter(c => c.lat != null);
  if (!pts.length) return;
  const big = L.map('complex-map', { scrollWheelZoom: false });
  tiles().addTo(big);
  pts.forEach(c => {
    L.marker([c.lat, c.lng], { icon: pin(c.num) })
      .bindPopup(`<b>${esc(c.name)}</b><br>${esc(c.addr)}<br>${esc(c.price)}<br><a href="${esc(c.url)}" target="_blank" rel="noopener">Open posting ↗</a>`)
      .addTo(big);
  });
  big.fitBounds(L.latLngBounds(pts.map(c => [c.lat, c.lng])), { padding: [24, 24] });
}

fetch('listings.json?t=' + Date.now(), { cache: 'no-store' })
  .then(r => r.json())
  .then(render)
  .catch(() => {
    document.getElementById('unique').innerHTML = '<p class="section-note">Couldn\'t load listings. Try refreshing.</p>';
  });
