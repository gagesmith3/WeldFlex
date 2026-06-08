const NS = 'http://www.w3.org/2000/svg';

// Fake run data pool — each part gets one dataset assigned deterministically by name length.
const DEMO_RUN_POOL = [
  {
    runs: [
      {date:'Jan 15', total:46.8, travel:9.8,  weld:32.1, other:4.9},
      {date:'Jan 15', total:47.2, travel:10.1, weld:32.2, other:4.9},
      {date:'Jan 16', total:45.9, travel:9.5,  weld:31.8, other:4.6},
    ],
    tips: [
      'Longest travel segment covers ~390 mm. A snake-order pattern could save ~0.9s per cycle.',
      'Two points show slightly longer weld times — verify gun height at those positions.',
    ],
  },
  {
    runs: [
      {date:'Jan 14', total:68.3, travel:14.2, weld:48.0, other:6.1},
      {date:'Jan 14', total:67.8, travel:13.9, weld:48.1, other:5.8},
    ],
    tips: [
      'Travel between point clusters is high relative to weld time. Consider grouping nearby studs into sequential runs.',
    ],
  },
  {
    runs: [
      {date:'Jan 17', total:35.2, travel:6.8, weld:24.0, other:4.4},
      {date:'Jan 17', total:34.9, travel:6.5, weld:24.3, other:4.1},
      {date:'Jan 18', total:35.6, travel:7.0, weld:24.1, other:4.5},
    ],
    tips: [
      'Cycle time is consistent within ±0.4s — path order looks optimal.',
    ],
  },
];

let _state = {
  activePart: null,   // name string of the currently selected part
  mode: 'select',
  selectedPoint: null,
  points: [],
  partIndex: 0,       // position in the fetched list, used to pick demo run data
};

let _pdMouseMove = null;

function pdInit() {
  if (_pdMouseMove) document.removeEventListener('mousemove', _pdMouseMove);
  _pdMouseMove = e => {
    const tip = document.getElementById('pd-tooltip');
    if (tip && !tip.classList.contains('pd-hidden')) {
      tip.style.left = (e.clientX + 14) + 'px';
      tip.style.top  = (e.clientY - 10) + 'px';
    }
  };
  document.addEventListener('mousemove', _pdMouseMove);

  buildGrid();
  fetchParts();
  setupImageUpload();
  setupBedClick();
  document.getElementById('pd-opacity-slider').addEventListener('input', e => {
    document.getElementById('pd-overlay').setAttribute('opacity', e.target.value / 100);
  });
}

// ── Coordinate helpers (physical ↔ SVG) ──────────────────────────────────────
// Physical 0,0 is at the bottom-right corner of the 508×508 bed.
// SVG 0,0 is top-left, so we mirror both axes.

const BED = 508;

function toSVG(px, py)  { return { x: BED - px, y: BED - py }; }
function toPhys(sx, sy) { return { x: BED - sx, y: BED - sy }; }

// ── Grid ────────────────────────────────────────────────────────────────────

function buildGrid() {
  const g = document.getElementById('pd-grid');
  if (!g) return;
  g.innerHTML = '';
  const SIZE = BED, STEP = 50;

  for (let i = 0; i <= SIZE; i += STEP) {
    const major = i % 100 === 0;
    const color = major ? '#c8d8e6' : '#e4ecf2';
    const w     = major ? 0.7 : 0.35;

    g.appendChild(svgEl('line', {x1:i, y1:0,    x2:i,    y2:SIZE, stroke:color, 'stroke-width':w}));
    g.appendChild(svgEl('line', {x1:0, y1:i,    x2:SIZE, y2:i,    stroke:color, 'stroke-width':w}));

    // Label shows physical coordinate (mirrored from SVG position)
    if (major && i > 0 && i < SIZE) {
      const physVal = BED - i;
      // X-axis label (bottom of SVG = physical Y=0 row)
      const tx = svgEl('text', {
        x: i + 2, y: SIZE - 3,
        fill:'#9ab0c4', 'font-size':'7', 'font-family':'monospace',
      });
      tx.textContent = `${physVal}`;
      g.appendChild(tx);
    }
  }

  // Origin marker at SVG bottom-right (physical 0,0)
  const ox = svgEl('text', {
    x: SIZE - 3, y: SIZE - 3,
    'text-anchor': 'end',
    fill:'#9ab0c4', 'font-size':'7', 'font-family':'monospace',
  });
  ox.textContent = '0';
  g.appendChild(ox);

  // Bed border
  g.appendChild(svgEl('rect', {
    x:0, y:0, width:SIZE, height:SIZE,
    fill:'none', stroke:'#b0c8dc', 'stroke-width':1,
  }));
}

// ── Parts list (real data) ────────────────────────────────────────────────────

function fetchParts() {
  const el = document.getElementById('pd-part-list');
  if (!el) return;
  el.innerHTML = '<span class="pd-list-loading">Loading…</span>';

  fetch('/ui/manager/parts-list')
    .then(r => r.json())
    .then(parts => {
      el.innerHTML = '';
      if (!parts.length) {
        el.innerHTML = '<span class="pd-list-empty">No parts found. Create parts in Operator.</span>';
        return;
      }
      parts.forEach((part, idx) => {
        const item = document.createElement('div');
        item.className = 'pd-part-item' + (part.name === _state.activePart ? ' active' : '');
        item.dataset.partName = part.name;
        item.dataset.partIdx  = idx;
        item.innerHTML = `
          <span class="pd-part-name">${part.name}</span>
          <span class="pd-part-desc">${part.studs_count} stud${part.studs_count !== 1 ? 's' : ''} · ${part.updated_label}</span>
        `;
        item.addEventListener('click', () => loadPart(part.name, idx));
        el.appendChild(item);
      });
      // Auto-select first part
      if (parts.length) loadPart(parts[0].name, 0);
    })
    .catch(() => {
      el.innerHTML = '<span class="pd-list-empty">Failed to load parts.</span>';
    });
}

function loadPart(name, idx) {
  _state.activePart = name;
  _state.partIndex  = idx ?? 0;
  _state.selectedPoint = null;

  document.querySelectorAll('.pd-part-item').forEach(el => {
    el.classList.toggle('active', el.dataset.partName === name);
  });

  // Show fake run data immediately while points load
  renderAnalysis(DEMO_RUN_POOL[_state.partIndex % DEMO_RUN_POOL.length]);

  fetch(`/ui/manager/part-points?name=${encodeURIComponent(name)}`)
    .then(r => r.json())
    .then(data => {
      if (!data.ok) { _state.points = []; }
      else          { _state.points = data.points; }
      renderPoints();
      setCoords(null);
    })
    .catch(() => {
      _state.points = [];
      renderPoints();
    });
}

// ── Weld points ──────────────────────────────────────────────────────────────

function renderPoints() {
  const pathG = document.getElementById('pd-path');
  const ptG   = document.getElementById('pd-points');
  if (!pathG || !ptG) return;
  pathG.innerHTML = '';
  ptG.innerHTML   = '';

  const pts = _state.points;
  if (pts.length === 0) return;

  // Travel path
  for (let i = 0; i < pts.length - 1; i++) {
    const a = toSVG(pts[i].x, pts[i].y);
    const b = toSVG(pts[i+1].x, pts[i+1].y);
    pathG.appendChild(svgEl('line', {
      x1:a.x, y1:a.y, x2:b.x, y2:b.y,
      stroke:'#275f84', 'stroke-width':1.2,
      'stroke-dasharray':'5 3', opacity:0.45,
    }));
  }

  // Points
  pts.forEach(p => {
    const sv  = toSVG(p.x, p.y);
    const g   = svgEl('g', {'data-pid':p.id, style:'cursor:pointer'});
    const sel = _state.selectedPoint === p.id;

    if (sel) {
      g.appendChild(svgEl('circle', {cx:sv.x, cy:sv.y, r:11, fill:'none', stroke:'#275f84', 'stroke-width':1.5, opacity:0.35}));
    }
    g.appendChild(svgEl('circle', {
      cx:sv.x, cy:sv.y, r:7,
      fill: sel ? '#275f84' : '#ffffff',
      stroke:'#275f84', 'stroke-width':1.8,
    }));

    const lbl = svgEl('text', {
      x:sv.x, y:sv.y + 3.5,
      'text-anchor':'middle',
      fill: sel ? '#ffffff' : '#275f84',
      'font-size':'7', 'font-weight':'bold',
      'font-family':'Segoe UI, sans-serif',
      style:'pointer-events:none; user-select:none',
    });
    lbl.textContent = p.id;
    g.appendChild(lbl);

    g.addEventListener('click', e => {
      e.stopPropagation();
      _state.selectedPoint = _state.selectedPoint === p.id ? null : p.id;
      renderPoints();
      setCoords(_state.selectedPoint ? p : null);
    });

    g.addEventListener('mouseenter', () => showTooltip(p));
    g.addEventListener('mouseleave', hideTooltip);

    ptG.appendChild(g);
  });
}

// ── Tooltip ──────────────────────────────────────────────────────────────────

function showTooltip(p) {
  const tip  = document.getElementById('pd-tooltip');
  if (!tip) return;
  const demo = DEMO_RUN_POOL[_state.partIndex % DEMO_RUN_POOL.length];
  const runs = demo ? demo.runs : [];
  const perPt = runs.length && _state.points.length
    ? (runs.reduce((s,r) => s + r.weld, 0) / runs.length / _state.points.length).toFixed(1)
    : null;

  tip.innerHTML = `<strong>Point ${p.id}</strong><br>X: ${p.x} mm &nbsp; Y: ${p.y} mm${perPt ? `<br>Avg weld: ~${perPt}s` : ''}`;
  tip.classList.remove('pd-hidden');
}

function hideTooltip() {
  const tip = document.getElementById('pd-tooltip');
  if (tip) tip.classList.add('pd-hidden');
}

// ── Bed click ────────────────────────────────────────────────────────────────

function setupBedClick() {
  const bed = document.getElementById('pd-bed');
  if (!bed) return;
  bed.addEventListener('click', e => {
    if (_state.mode !== 'add') return;
    const sv  = svgPoint(e, bed);
    const raw = toPhys(sv.x, sv.y);
    const p = {
      x: Math.round(Math.max(0, Math.min(BED, raw.x))),
      y: Math.round(Math.max(0, Math.min(BED, raw.y))),
      id: (_state.points.length ? Math.max(..._state.points.map(p => p.id)) : 0) + 1,
    };
    _state.points.push(p);
    renderPoints();
    setCoords(p);
  });
}

function svgPoint(e, svg) {
  const pt = svg.createSVGPoint();
  pt.x = e.clientX;
  pt.y = e.clientY;
  return pt.matrixTransform(svg.getScreenCTM().inverse());
}

// ── Mode / actions ────────────────────────────────────────────────────────────

function pdSetMode(mode) {
  _state.mode = mode;
  document.getElementById('pd-tool-select').classList.toggle('active', mode === 'select');
  document.getElementById('pd-tool-add').classList.toggle('active', mode === 'add');
  const bed = document.getElementById('pd-bed');
  if (bed) bed.style.cursor = mode === 'add' ? 'crosshair' : 'default';
}

function pdClearPoints() {
  if (!confirm('Clear all points from this part?')) return;
  _state.points = [];
  _state.selectedPoint = null;
  renderPoints();
  setCoords(null);
}

// ── Image upload ──────────────────────────────────────────────────────────────

function setupImageUpload() {
  const input = document.getElementById('pd-img-upload');
  if (!input) return;
  input.addEventListener('change', e => {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = ev => {
      const img = document.getElementById('pd-overlay');
      img.setAttribute('href', ev.target.result);
      img.classList.remove('pd-hidden');
      document.getElementById('pd-opacity-wrap').classList.remove('pd-hidden');
    };
    reader.readAsDataURL(file);
  });
}

// ── Coords bar ────────────────────────────────────────────────────────────────

function setCoords(p) {
  const el = document.getElementById('pd-coords');
  if (!el) return;
  el.textContent = p
    ? `Point ${p.id}  ·  X: ${p.x} mm  ·  Y: ${p.y} mm`
    : '508 × 508 mm bed';
}

// ── Analysis ──────────────────────────────────────────────────────────────────

function renderAnalysis(part) {
  const el = document.getElementById('pd-analysis');
  if (!el) return;

  if (!part.runs || part.runs.length === 0) {
    el.innerHTML = '<p style="color:var(--muted);font-size:0.82rem;padding:8px 0">No run data yet.</p>';
    return;
  }

  const avg = key => part.runs.reduce((s,r) => s + r[key], 0) / part.runs.length;
  const T = avg('total'), tv = avg('travel'), wl = avg('weld'), ot = avg('other');
  const pct = v => ((v / T) * 100).toFixed(0);

  const dot = color => `<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${color};margin-right:6px;flex-shrink:0"></span>`;

  const runsHtml = part.runs.map(r =>
    `<div class="pd-run-row"><span class="pd-run-date">${r.date}</span><span class="pd-run-time">${r.total.toFixed(1)}s</span></div>`
  ).join('');

  const tipsHtml = (part.tips || []).map(t =>
    `<div class="pd-tip"><span class="pd-tip-icon">⚡</span><span>${t}</span></div>`
  ).join('');

  el.innerHTML = `
    <div class="pd-stat-card">
      <div class="pd-stat-big">${T.toFixed(1)}s</div>
      <div class="pd-stat-sublabel">avg cycle · ${part.runs.length} run${part.runs.length > 1 ? 's' : ''}</div>
    </div>

    <div class="pd-section-label">Time Breakdown</div>
    <div class="pd-timing-bar">
      <div class="pd-timing-seg travel" style="flex:${pct(tv)}"></div>
      <div class="pd-timing-seg weld"   style="flex:${pct(wl)}"></div>
      <div class="pd-timing-seg other"  style="flex:${pct(ot)}"></div>
    </div>
    <div class="pd-stat-row">
      <span class="pd-stat-label">${dot('#275f84')}Travel</span>
      <span class="pd-stat-value">${tv.toFixed(1)}s <small style="color:var(--muted);font-weight:400">(${pct(tv)}%)</small></span>
    </div>
    <div class="pd-stat-row">
      <span class="pd-stat-label">${dot('#12744a')}Weld</span>
      <span class="pd-stat-value">${wl.toFixed(1)}s <small style="color:var(--muted);font-weight:400">(${pct(wl)}%)</small></span>
    </div>
    <div class="pd-stat-row">
      <span class="pd-stat-label">${dot('#c97a00')}Load / Unload</span>
      <span class="pd-stat-value">${ot.toFixed(1)}s <small style="color:var(--muted);font-weight:400">(${pct(ot)}%)</small></span>
    </div>

    <div class="pd-section-label" style="margin-top:16px">Recent Runs</div>
    <div class="pd-run-list">${runsHtml}</div>

    ${tipsHtml ? `<div class="pd-section-label" style="margin-top:16px">Potential Savings</div><div style="display:flex;flex-direction:column;gap:8px">${tipsHtml}</div>` : ''}
  `;
}

// ── SVG helper ────────────────────────────────────────────────────────────────

function svgEl(tag, attrs = {}) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
}
