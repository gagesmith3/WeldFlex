const NS = 'http://www.w3.org/2000/svg';

let _state = {
  activeId:     null,  // UUID — primary key for all backend ops
  activePart:   null,  // display name
  selectedPoint: null,
  points: [],
  safe_z: 60.0,
  retract_z: 10.0,
  part_z: 0.0,
  units: 'mm',
  stud_type: 'M4',
  substrate: 'Mild Steel',
  pressure_setting: 20.0,
  speed: 25,
  dsc_enabled: false,
  stud_reload_ms: 600,
  di_check: true,
  origin_corner: 'front_left',
  isDirty: false,
};

let _pdMouseMove  = null;
let _deletePending = null;

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

  const nameInput = document.getElementById('pd-create-name');
  if (nameInput) {
    nameInput.addEventListener('keydown', e => {
      if (e.key === 'Enter')  pdConfirmCreate();
      if (e.key === 'Escape') pdCancelCreate();
    });
  }

  const createModal = document.getElementById('pd-create-modal');
  if (createModal) createModal.addEventListener('click', e => {
    if (e.target === createModal) pdCancelCreate();
  });

  const deleteInput = document.getElementById('pd-delete-confirm-input');
  if (deleteInput) {
    deleteInput.addEventListener('input', e => {
      const btn = document.getElementById('pd-delete-confirm-btn');
      if (btn) btn.disabled = e.target.value.trim() !== 'DELETE';
    });
    deleteInput.addEventListener('keydown', e => {
      if (e.key === 'Enter')  pdConfirmDelete();
      if (e.key === 'Escape') pdCancelDelete();
    });
  }

  const deleteModal = document.getElementById('pd-delete-modal');
  if (deleteModal) deleteModal.addEventListener('click', e => {
    if (e.target === deleteModal) pdCancelDelete();
  });

  const renameInput = document.getElementById('pd-rename-input');
  if (renameInput) {
    renameInput.addEventListener('keydown', e => {
      if (e.key === 'Enter')  pdConfirmRename();
      if (e.key === 'Escape') pdCancelRename();
    });
  }

  const renameModal = document.getElementById('pd-rename-modal');
  if (renameModal) renameModal.addEventListener('click', e => {
    if (e.target === renameModal) pdCancelRename();
  });

  const settingsModal = document.getElementById('pd-settings-modal');
  if (settingsModal) {
    settingsModal.addEventListener('click', e => {
      if (e.target === settingsModal) pdCloseJobSettingsModal();
    });
    settingsModal.addEventListener('keydown', pdsOnKeydown);
    settingsModal.addEventListener('input', () => pdsRefresh());
    settingsModal.addEventListener('change', e => {
      if (e.target.classList.contains('pds-input')) e.target.dataset.touched = '1';
      pdsRefresh();
    });
  }

  const dryRunModal = document.getElementById('pd-dry-run-confirm-modal');
  if (dryRunModal) dryRunModal.addEventListener('click', e => {
    if (e.target === dryRunModal) pdCloseDryRunConfirmModal();
  });

  const reportsModal = document.getElementById('pd-reports-modal');
  if (reportsModal) reportsModal.addEventListener('click', e => {
    if (e.target === reportsModal) pdCloseJobReportsModal();
  });
}

// ── Coordinate helpers (part ↔ SVG) ──────────────────────────────────────────
// The bed is drawn as the operator faces it: front at the bottom, zerozero at
// the bottom-left. A part's X/Y are measured inward from its origin corner, so a
// right corner mirrors X and a back corner mirrors Y. SVG 0,0 is top-left.

const BED = 762; // 30in bed, in mm
const MM_PER_INCH = 25.4;

// Same keys as part_origin.CORNERS; tests/test_part_origin.py holds them together.
const PD_CORNERS = ['front_left', 'front_right', 'back_left', 'back_right'];
const PD_CORNER_LABELS = {
  front_left: 'Front-left',
  front_right: 'Front-right',
  back_left: 'Back-left',
  back_right: 'Back-right',
};

function normalizeCorner(value) { return PD_CORNERS.includes(value) ? value : PD_CORNERS[0]; }
function cornerLabel(corner = _state.origin_corner) { return PD_CORNER_LABELS[normalizeCorner(corner)]; }
function cornerMirrors(corner = _state.origin_corner) {
  const c = normalizeCorner(corner);
  return { x: c.endsWith('_right'), y: c.startsWith('back_') };
}

// Length helpers default to the part's units; the Part Settings modal passes its
// own until Apply.
function toSVG(px, py, corner = _state.origin_corner) {
  const m = cornerMirrors(corner);
  const bedY = m.y ? BED - py : py;
  return { x: m.x ? BED - px : px, y: BED - bedY };
}
function toPhys(sx, sy, corner = _state.origin_corner) {
  const m = cornerMirrors(corner);
  const bedY = BED - sy;
  return { x: m.x ? BED - sx : sx, y: m.y ? BED - bedY : bedY };
}
function isInches(units = _state.units) { return units === 'in'; }
function lengthFactor(units = _state.units) { return isInches(units) ? MM_PER_INCH : 1; }
function lengthStep(units = _state.units) { return isInches(units) ? '0.0001' : '0.001'; }
function formatLength(mm, units = _state.units) {
  const decimals = isInches(units) ? 4 : 3;
  return Number((mm / lengthFactor(units)).toFixed(decimals)).toString();
}
function parseLength(value) { return parseFloat(value) * lengthFactor(); }
function unitLabel(units = _state.units) { return isInches(units) ? 'in' : 'mm'; }

// ── Grid ────────────────────────────────────────────────────────────────────

function buildGrid() {
  const g = document.getElementById('pd-grid');
  if (!g) return;
  g.innerHTML = '';
  const SIZE = BED, STEP = 50;
  const m = cornerMirrors();

  const gridLines = Array.from(
    { length: Math.floor(SIZE / STEP) + 1 },
    (_, index) => index * STEP,
  );
  if (gridLines.at(-1) !== SIZE) gridLines.push(SIZE);

  for (const i of gridLines) {
    const major = i % 100 === 0;
    const color = major ? '#c8d8e6' : '#e4ecf2';
    const w     = major ? 0.7 : 0.35;

    g.appendChild(svgEl('line', {x1:i, y1:0,    x2:i,    y2:SIZE, stroke:color, 'stroke-width':w}));
    g.appendChild(svgEl('line', {x1:0, y1:i,    x2:SIZE, y2:i,    stroke:color, 'stroke-width':w}));

    // Axis labels count from the part's corner, along the two edges that meet
    // there: X on the front or back edge, Y on the left or right edge.
    if (major && i > 0 && i < SIZE) {
      const tx = svgEl('text', {
        x: i + 4, y: m.y ? 16 : SIZE - 6,
        fill:'#7c95a8', 'font-size':'13', 'font-weight':'600', 'font-family':'monospace',
      });
      tx.textContent = formatLength(m.x ? SIZE - i : i);
      g.appendChild(tx);

      const ty = svgEl('text', {
        x: m.x ? SIZE - 4 : 4, y: i - 4, 'text-anchor': m.x ? 'end' : 'start',
        fill:'#7c95a8', 'font-size':'13', 'font-weight':'600', 'font-family':'monospace',
      });
      ty.textContent = formatLength(m.y ? i : SIZE - i);
      g.appendChild(ty);
    }
  }

  // Bed border, under the origin arrows that run along it.
  g.appendChild(svgEl('rect', {
    x:0, y:0, width:SIZE, height:SIZE,
    fill:'none', stroke:'#b0c8dc', 'stroke-width':1,
  }));

  // The part's 0,0: the corner it is tooled against, with X and Y running inward.
  // Sized to stay legible on the ~190px kiosk bed without crowding the studs.
  const ORIGIN = '#b4232f', AXIS = 130;
  const ox = m.x ? SIZE : 0, oy = m.y ? 0 : SIZE;
  const dx = m.x ? -1 : 1, dy = m.y ? 1 : -1;
  const origin = svgEl('g', {opacity:0.75});
  const defs = svgEl('defs');
  const arrow = svgEl('marker', {
    id:'pd-origin-arrow', viewBox:'0 0 10 10', refX:5, refY:5,
    markerWidth:4, markerHeight:4, orient:'auto-start-reverse',
  });
  arrow.appendChild(svgEl('path', {d:'M0 0 10 5 0 10z', fill:ORIGIN}));
  defs.appendChild(arrow);
  origin.appendChild(defs);
  for (const [x2, y2] of [[ox + dx * AXIS, oy], [ox, oy + dy * AXIS]]) {
    origin.appendChild(svgEl('line', {
      x1:ox, y1:oy, x2, y2, stroke:ORIGIN, 'stroke-width':4,
      'marker-end':'url(#pd-origin-arrow)',
    }));
  }
  origin.appendChild(svgEl('circle', {cx:ox, cy:oy, r:10, fill:ORIGIN, stroke:'#ffffff', 'stroke-width':3}));

  const labelAttrs = {fill:ORIGIN, 'font-size':'24', 'font-weight':'600', 'font-family':'monospace'};
  const axisX = svgEl('text', {...labelAttrs, x: ox + dx * (AXIS - 10), y: oy + dy * 32 + 9, 'text-anchor':'middle'});
  axisX.textContent = 'X';
  const axisY = svgEl('text', {...labelAttrs, x: ox + dx * 32, y: oy + dy * (AXIS - 10) + 9, 'text-anchor':'middle'});
  axisY.textContent = 'Y';
  const zero = svgEl('text', {
    ...labelAttrs, x: ox + dx * 28, y: oy + dy * 44 + 9, 'text-anchor': m.x ? 'end' : 'start',
  });
  zero.textContent = '0,0';
  origin.append(axisX, axisY, zero);
  g.appendChild(origin);
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
        el.innerHTML = '<span class="pd-list-empty">No parts yet. Click + to create one.</span>';
        return;
      }
      parts.forEach((part, idx) => {
        const item = document.createElement('div');
        item.className = 'pd-part-item' + (part.id === _state.activeId ? ' active' : '');
        item.dataset.partName = part.name;
        item.dataset.partId   = part.id;
        item.dataset.partIdx  = idx;

        const body = document.createElement('div');
        body.className = 'pd-part-item-body';
        body.innerHTML = `
          <span class="pd-part-name">${part.name}</span>
          <span class="pd-part-desc">${part.studs_count} stud${part.studs_count !== 1 ? 's' : ''} · ${part.updated_label}</span>
        `;
        item.appendChild(body);

        const delBtn = document.createElement('button');
        delBtn.type = 'button';
        delBtn.className = 'pd-part-delete-btn';
        delBtn.title = 'Delete part';
        delBtn.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="m19 6-.867 12.142A2 2 0 0 1 16.138 20H7.862a2 2 0 0 1-1.995-1.858L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>`;
        delBtn.addEventListener('click', e => {
          e.stopPropagation();
          pdDeletePart(part.id, part.name);
        });
        item.appendChild(delBtn);

        item.addEventListener('click', () => loadPart(part.id, part.name, idx));
        el.appendChild(item);
      });
      // Re-select the active part by UUID, fall back to name for newly created parts
      const targetIdx = _state.activeId
        ? parts.findIndex(p => p.id === _state.activeId)
        : _state.activePart
          ? parts.findIndex(p => p.name === _state.activePart)
          : -1;
      if (targetIdx >= 0) loadPart(parts[targetIdx].id, parts[targetIdx].name, targetIdx);
      else if (parts.length) loadPart(parts[0].id, parts[0].name, 0);
    })
    .catch(() => {
      el.innerHTML = '<span class="pd-list-empty">Failed to load parts.</span>';
    });
}

function pdParamChanged() {
  pdSetDirty(true);
}

function loadPart(id, name, idx) {
  _state.activeId   = id;
  _state.activePart = name;
  _state.selectedPoint = null;

  const titleEl = document.getElementById('pd-canvas-part-title');
  if (titleEl) titleEl.textContent = name;

  document.querySelectorAll('.pd-part-item').forEach(el => {
    el.classList.toggle('active', el.dataset.partId === id);
  });

  fetch(`/ui/manager/part-points?id=${encodeURIComponent(id)}`)
    .then(r => r.json())
    .then(data => {
      if (!data.ok) { _state.points = []; }
      else          { _state.points = data.points.map((p, i) => ({ ...p, id: i + 1 })); }
      if (data.recipe) {
        _currentRecipe = data.recipe;
        _state.safe_z = data.recipe.safe_z !== undefined ? data.recipe.safe_z : 60.0;
        _state.retract_z = data.recipe.retract_z !== undefined ? data.recipe.retract_z : 10.0;
        _state.part_z = data.recipe.part_z !== undefined ? data.recipe.part_z : 0.0;
        _state.units = data.recipe.units === 'in' ? 'in' : 'mm';
        _state.stud_type = data.recipe.stud_type || 'M4';
        _state.substrate = data.recipe.substrate || 'Mild Steel';
        _state.pressure_setting = data.recipe.pressure_setting !== undefined ? (parseFloat(data.recipe.pressure_setting) || 20.0) : 20.0;
        _state.speed = data.recipe.speed !== undefined && data.recipe.speed !== null
          ? Math.max(1, Math.min(100, Math.round(parseFloat(data.recipe.speed) || 25)))
          : 25;
        _state.dsc_enabled = data.recipe.dsc_enabled === true;
        _state.stud_reload_ms = normalizeStudReloadMs(data.recipe.stud_reload_ms);
        _state.di_check = data.recipe.di_check !== false;
        _state.origin_corner = normalizeCorner(data.recipe.origin_corner);
      }
      renderPoints();
      renderStudList();
      setCoords(null);
      buildGrid();
    })
    .catch(() => {
      _state.points = [];
      renderPoints();
      renderStudList();
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
      x:sv.x, y:sv.y + 4,
      'text-anchor':'middle',
      fill: sel ? '#ffffff' : '#275f84',
      'font-size':'10', 'font-weight':'bold',
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
  const tip = document.getElementById('pd-tooltip');
  if (!tip) return;
  tip.innerHTML = `<strong>Point ${p.id}</strong><br>X: ${formatLength(p.x)} ${unitLabel()} &nbsp; Y: ${formatLength(p.y)} ${unitLabel()} from the ${cornerLabel().toLowerCase()} corner`;
  tip.classList.remove('pd-hidden');
}

function hideTooltip() {
  const tip = document.getElementById('pd-tooltip');
  if (tip) tip.classList.add('pd-hidden');
}

function svgPoint(e, svg) {
  const pt = svg.createSVGPoint();
  pt.x = e.clientX;
  pt.y = e.clientY;
  return pt.matrixTransform(svg.getScreenCTM().inverse());
}

// ── Dirty / save state ────────────────────────────────────────────────────────

function pdSetDirty(dirty) {
  _state.isDirty = dirty;
  const btn = document.getElementById('pd-save-btn');
  if (!btn) return;
  if (dirty && (_state.activeId || _state.activePart)) btn.classList.remove('pd-hidden');
  else btn.classList.add('pd-hidden');
}

// ── New part ──────────────────────────────────────────────────────────────────

function pdNewPart() {
  const modal = document.getElementById('pd-create-modal');
  const input = document.getElementById('pd-create-name');
  if (!modal || !input) return;
  modal.removeAttribute('hidden');
  input.value = '';
  input.focus();
}

function pdCancelCreate() {
  const modal = document.getElementById('pd-create-modal');
  if (modal) modal.setAttribute('hidden', '');
}

function pdConfirmCreate() {
  const input = document.getElementById('pd-create-name');
  const name  = (input ? input.value : '').trim();
  if (!name) { if (input) input.focus(); return; }
  pdCancelCreate();
  pdStartNewPart(name);
  pdSave();
}

function pdStartNewPart(name) {
  _state.activeId      = null;
  _state.activePart    = name;
  _state.points        = [];
  _state.selectedPoint = null;
  _state.units         = 'mm';
  _state.speed         = 25;
  _state.dsc_enabled   = false;
  _state.stud_reload_ms = 600;
  _state.di_check      = true;
  _state.origin_corner = PD_CORNERS[0];

  const titleEl = document.getElementById('pd-canvas-part-title');
  if (titleEl) titleEl.textContent = name;

  document.querySelectorAll('.pd-part-item').forEach(el => el.classList.remove('active'));
  renderPoints();
  renderStudList();
  setCoords(null);
  buildGrid();
  pdSetDirty(true);

  const coords = document.getElementById('pd-coords');
  if (coords) coords.textContent = `New: ${name}  ·  click bed to place studs`;
}

// ── Add point ─────────────────────────────────────────────────────────────────

function pdAddPoint() {
  if (!_state.activeId && !_state.activePart) return;
  const nextId = _state.points.length ? Math.max(..._state.points.map(p => p.id)) + 1 : 1;
  _state.points.push({ id: nextId, x: 0, y: 0 });
  renderPoints();
  renderStudList();
  pdSetDirty(true);
  const el = document.getElementById('pd-stud-list');
  if (el) {
    const xInput = el.querySelector(`.pd-stud-input[data-pid="${nextId}"][data-axis="x"]`);
    if (xInput) { xInput.focus(); xInput.select(); }
  }
}

// ── Save ──────────────────────────────────────────────────────────────────────

function pdSave() {
  const name = _state.activePart;
  if (!name) return;

  const studs_json = JSON.stringify(_state.points.map(p => ({ x: p.x, y: p.y })));
  const safe_z = _state.safe_z !== undefined ? _state.safe_z : 60.0;
  const retract_z = _state.retract_z !== undefined ? _state.retract_z : 10.0;
  const part_z = _state.part_z !== undefined ? _state.part_z : 0.0;
  const units = _state.units === 'in' ? 'in' : 'mm';
  const stud_type = _state.stud_type || 'M4';
  const substrate = _state.substrate || 'Mild Steel';
  const pressure_setting = _state.pressure_setting !== undefined ? _state.pressure_setting : 20.0;
  const speed = _state.speed !== undefined ? _state.speed : 25;
  const dsc_enabled = _state.dsc_enabled === true ? '1' : '0';
  const stud_reload_ms = normalizeStudReloadMs(_state.stud_reload_ms);
  const di_check = _state.di_check === false ? '0' : '1';
  const origin_corner = normalizeCorner(_state.origin_corner);

  const body = new URLSearchParams({
    recipe_name: name,
    studs_json,
    safe_z,
    retract_z,
    part_z,
    units,
    stud_type,
    substrate,
    pressure_setting,
    speed,
    dsc_enabled,
    stud_reload_ms,
    di_check,
    origin_corner,
  });
  if (_state.activeId) body.set('recipe_id', _state.activeId);

  const saveBtn = document.getElementById('pd-save-btn');
  if (saveBtn) saveBtn.disabled = true;

  fetch('/ui/recipes/save', { method: 'POST', body })
    .then(r => {
      const rid = r.headers.get('X-Recipe-Id');
      if (rid) _state.activeId = rid;
      pdSetDirty(false);
      if (saveBtn) saveBtn.disabled = false;
      fetchParts();
    })
    .catch(() => {
      if (saveBtn) saveBtn.disabled = false;
    });
}

// ── Delete part ───────────────────────────────────────────────────────────────

function pdDeletePart(id, name) {
  _deletePending = { id, name };
  const nameEl = document.getElementById('pd-delete-part-name');
  const input  = document.getElementById('pd-delete-confirm-input');
  const btn    = document.getElementById('pd-delete-confirm-btn');
  const modal  = document.getElementById('pd-delete-modal');
  if (!modal) return;
  if (nameEl) nameEl.textContent = name;
  if (input)  input.value = '';
  if (btn)    btn.disabled = true;
  modal.removeAttribute('hidden');
  if (input)  input.focus();
}

function pdCancelDelete() {
  const modal = document.getElementById('pd-delete-modal');
  const input = document.getElementById('pd-delete-confirm-input');
  if (modal) modal.setAttribute('hidden', '');
  if (input) input.value = '';
  _deletePending = null;
}

function pdConfirmDelete() {
  const input = document.getElementById('pd-delete-confirm-input');
  if (!_deletePending || !input || input.value.trim() !== 'DELETE') return;
  const { id, name } = _deletePending;
  pdCancelDelete();
  fetch('/ui/parts/delete', { method: 'POST', body: new URLSearchParams({ recipe_id: id }) })
    .then(() => {
      if (_state.activeId === id) {
        _state.activeId      = null;
        _state.activePart    = null;
        _state.points        = [];
        _state.selectedPoint = null;
        _state.origin_corner = PD_CORNERS[0];
        const titleEl = document.getElementById('pd-canvas-part-title');
        if (titleEl) titleEl.textContent = 'No Part Selected';
        buildGrid();
        renderPoints();
        renderStudList();
        setCoords(null);
        pdSetDirty(false);
      }
      fetchParts();
    });
}

// ── Coords bar ────────────────────────────────────────────────────────────────

function setCoords(p) {
  const el = document.getElementById('pd-coords');
  if (!el) return;
  const corner = cornerLabel().toLowerCase();
  el.textContent = p
    ? `Point ${p.id}  ·  X: ${formatLength(p.x)} ${unitLabel()}  ·  Y: ${formatLength(p.y)} ${unitLabel()} from ${corner}`
    : `0,0 = ${corner} corner · ${formatLength(BED)} × ${formatLength(BED)} ${unitLabel()} bed`;
}

// ── Stud list ─────────────────────────────────────────────────────────────────

let _draggedStudIdx = null;

function renderStudList() {
  const el    = document.getElementById('pd-stud-list');
  const badge = document.getElementById('pd-stud-count');
  if (!el) return;

  if (badge) badge.textContent = _state.points.length ? String(_state.points.length) : '';

  if (!_state.points.length) {
    el.innerHTML = '<span class="pd-list-empty">No studs yet.</span>';
    return;
  }

  el.innerHTML = _state.points.map((p, idx) => `
    <div class="pd-stud-row${_state.selectedPoint === p.id ? ' selected' : ''}"
         data-pid="${p.id}" data-idx="${idx}" draggable="true">
      <span class="pd-drag-handle" title="Drag to reorder">⋮⋮</span>
      <span class="pd-stud-num">${p.id}</span>
      <span class="pd-stud-label">X</span>
      <input class="pd-stud-input" type="number" min="0" max="${formatLength(BED)}" step="${lengthStep()}" value="${formatLength(p.x)}" data-pid="${p.id}" data-axis="x" inputmode="none" data-kbd="num">
      <span class="pd-stud-label">Y</span>
      <input class="pd-stud-input" type="number" min="0" max="${formatLength(BED)}" step="${lengthStep()}" value="${formatLength(p.y)}" data-pid="${p.id}" data-axis="y" inputmode="none" data-kbd="num">
      <button class="pd-stud-goto-btn" data-pid="${p.id}" title="Move robot above this stud at Safe Z">⌖</button>
      <button class="pd-stud-delete-btn" data-pid="${p.id}" title="Remove stud">×</button>
    </div>
  `).join('');

  el.querySelectorAll('.pd-stud-row').forEach((row, idx) => {
    row.addEventListener('click', () => {
      const pid = parseInt(row.dataset.pid);
      const p   = _state.points.find(pt => pt.id === pid);
      if (!p) return;
      _state.selectedPoint = _state.selectedPoint === pid ? null : pid;
      renderPoints();
      renderStudList();
      setCoords(_state.selectedPoint ? p : null);
    });

    row.addEventListener('dragstart', e => {
      _draggedStudIdx = idx;
      row.classList.add('dragging');
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', String(idx));
    });

    row.addEventListener('dragover', e => {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      row.classList.add('drag-over');
    });

    row.addEventListener('dragenter', e => {
      e.preventDefault();
      row.classList.add('drag-over');
    });

    row.addEventListener('dragleave', () => {
      row.classList.remove('drag-over');
    });

    row.addEventListener('drop', e => {
      e.preventDefault();
      e.stopPropagation();
      row.classList.remove('drag-over');
      const fromIdx = _draggedStudIdx;
      const toIdx = idx;
      if (fromIdx === null || fromIdx === toIdx) return;

      const [moved] = _state.points.splice(fromIdx, 1);
      _state.points.splice(toIdx, 0, moved);
      _state.points.forEach((pt, i) => { pt.id = i + 1; });

      _draggedStudIdx = null;
      renderPoints();
      renderStudList();
      pdSetDirty(true);
    });

    row.addEventListener('dragend', () => {
      _draggedStudIdx = null;
      el.querySelectorAll('.pd-stud-row').forEach(r => {
        r.classList.remove('dragging', 'drag-over');
      });
    });
  });

  el.querySelectorAll('.pd-stud-input').forEach(input => {
    input.addEventListener('click', e => e.stopPropagation());
    input.addEventListener('focus', e => {
      const row = e.target.closest('.pd-stud-row');
      if (row) row.setAttribute('draggable', 'false');
    });
    input.addEventListener('blur', e => {
      const row = e.target.closest('.pd-stud-row');
      if (row) row.setAttribute('draggable', 'true');
    });
    input.addEventListener('change', () => {
      const pid  = parseInt(input.dataset.pid);
      const axis = input.dataset.axis;
      const val  = Math.max(0, Math.min(BED, parseLength(input.value) || 0));
      input.value = formatLength(val);
      const p = _state.points.find(pt => pt.id === pid);
      if (!p) return;
      p[axis] = val;
      renderPoints();
      pdSetDirty(true);
      if (_state.selectedPoint === pid) setCoords(p);
    });
  });

  el.querySelectorAll('.pd-stud-goto-btn').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      const pid = parseInt(btn.dataset.pid);
      const p = _state.points.find(pt => pt.id === pid);
      if (!p) return;
      pdGotoStud(p, btn);
    });
  });

  el.querySelectorAll('.pd-stud-delete-btn').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      const pid = parseInt(btn.dataset.pid);
      const idx = _state.points.findIndex(pt => pt.id === pid);
      if (idx === -1) return;
      _state.points.splice(idx, 1);
      _state.points.forEach((pt, i) => { pt.id = i + 1; });
      if (_state.selectedPoint === pid) {
        _state.selectedPoint = null;
        setCoords(null);
      }
      renderPoints();
      renderStudList();
      pdSetDirty(true);
    });
  });
}

function pdGotoStud(p, btn) {
  if (btn) btn.disabled = true;
  // Safe Z clears the fixtures; the Search Height is only for a run's search.
  const safe_z = _state.safe_z !== undefined ? _state.safe_z : 60.0;
  const part_z = _state.part_z !== undefined ? _state.part_z : 0.0;
  fetch('/ui/parts/goto', {
    method: 'POST',
    body: new URLSearchParams({
      x: p.x, y: p.y, safe_z, part_z,
      origin_corner: normalizeCorner(_state.origin_corner),
    }),
  })
    .then(r => r.text())
    .then(html => {
      const rack = document.getElementById('toast-rack');
      if (rack) rack.innerHTML = html;
    })
    .finally(() => { if (btn) btn.disabled = false; });
}

// ── SVG helper ────────────────────────────────────────────────────────────────

function svgEl(tag, attrs = {}) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
}

// ── Part Settings modal ───────────────────────────────────────────────────────
// Four tabs over one form. Nothing reaches _state until Apply & Save, so Cancel,
// Esc and a backdrop tap discard every edit, the units switch and the origin
// corner included. Limits are read from the inputs' own min/max, which tests
// hold to weld.lua and lua_builder.

const PDS_TABS = ['heights', 'weld', 'motion', 'origin'];
const PDS_LENGTH_IDS = ['pd-modal-safe-z', 'pd-modal-retract-z', 'pd-modal-part-z'];

let _pdsTab = 'heights';      // reopens on the tab last used
let _pdsUnits = 'mm';         // the modal's units until Apply
let _pdsCorner = PD_CORNERS[0]; // the modal's origin corner until Apply
let _pdsShowErrors = false;   // set by the first Apply attempt

function pdsEl(id) { return document.getElementById(id); }

function pdsNum(id) {
  const raw = (pdsEl(id)?.value || '').trim();
  return raw === '' ? NaN : Number(raw);
}

function pdsLimit(id, attr) { return Number(pdsEl(id)?.getAttribute(attr)); }

function pdsText(id, text) {
  const el = pdsEl(id);
  if (el) el.textContent = text;
}

function pdsSetValue(id, value) {
  const el = pdsEl(id);
  if (el) el.value = value;
}

function pdsSetSelect(id, value) {
  const el = pdsEl(id);
  if (!el) return;
  // Keep a saved value the list doesn't offer rather than silently replacing it.
  if (value && ![...el.options].some(option => option.value === value)) el.add(new Option(value, value));
  el.value = value;
}

function pdOpenJobSettingsModal() {
  const modal = pdsEl('pd-settings-modal');
  if (!modal || (!_state.activeId && !_state.activePart)) return;

  _pdsUnits = _state.units === 'in' ? 'in' : 'mm';
  _pdsCorner = normalizeCorner(_state.origin_corner);
  _pdsShowErrors = false;
  pdsText('pd-modal-settings-part-name', _state.activePart || 'Untitled');

  pdsSetValue('pd-modal-safe-z', formatLength(_state.safe_z ?? 60.0, _pdsUnits));
  pdsSetValue('pd-modal-retract-z', formatLength(_state.retract_z ?? 10.0, _pdsUnits));
  pdsSetValue('pd-modal-part-z', formatLength(_state.part_z ?? 0.0, _pdsUnits));
  pdsSetSelect('pd-modal-stud-type', _state.stud_type || 'M4');
  pdsSetSelect('pd-modal-substrate', _state.substrate || 'Mild Steel');
  pdsSetValue('pd-modal-pressure', _state.pressure_setting ?? 20.0);
  pdsSetValue('pd-modal-speed', _state.speed ?? 25);
  pdsSetValue('pd-modal-stud-reload-ms', normalizeStudReloadMs(_state.stud_reload_ms));
  const diCheck = pdsEl('pd-modal-di-check');
  if (diCheck) diCheck.checked = _state.di_check !== false;
  const dsc = pdsEl('pd-modal-dsc-enabled');
  if (dsc) dsc.checked = _state.dsc_enabled === true;
  modal.querySelectorAll('.pds-input').forEach(input => { delete input.dataset.touched; });

  pdsSyncUnits();
  pdsRefresh();
  modal.removeAttribute('hidden');
  pdsSelectTab(_pdsTab, true);
}

function pdCloseJobSettingsModal() {
  const modal = pdsEl('pd-settings-modal');
  if (modal) modal.setAttribute('hidden', '');
}

function pdSaveJobSettingsModal() {
  _pdsShowErrors = true;
  const { values, errors } = pdsRefresh();
  const invalid = [...document.querySelectorAll('#pd-settings-modal .pds-input')].find(input => errors[input.id]);
  if (invalid) {
    pdsSelectTab(invalid.closest('.pds-panel').dataset.tab, false);
    invalid.focus();
    return;
  }

  const unitsChanged = _pdsUnits !== _state.units;
  // A new corner keeps the studs' numbers, so they move: redraw them.
  const cornerChanged = values.origin_corner !== normalizeCorner(_state.origin_corner);
  Object.assign(_state, values, { units: _pdsUnits });
  if (unitsChanged || cornerChanged) {
    buildGrid();
    renderPoints();
    renderStudList();
    setCoords(_state.points.find(point => point.id === _state.selectedPoint) || null);
  }

  pdCloseJobSettingsModal();
  pdSetDirty(true);
  pdSave();
}

// Reads every field. `values` holds the ones that will save, in mm and the
// recipe's own units; `errors` maps an input id to why it won't.
function pdsReadForm() {
  const values = {};
  const errors = {};
  const factor = lengthFactor(_pdsUnits);

  const safeZ = pdsNum('pd-modal-safe-z');
  if (safeZ > 0) values.safe_z = safeZ * factor;
  else errors['pd-modal-safe-z'] = 'Enter a height above 0.';

  const retractZ = pdsNum('pd-modal-retract-z');
  if (retractZ > 0) values.retract_z = retractZ * factor;
  else errors['pd-modal-retract-z'] = 'Enter a height above 0.';

  const partZ = pdsNum('pd-modal-part-z');
  if (Number.isFinite(partZ)) values.part_z = partZ * factor;
  else errors['pd-modal-part-z'] = 'Enter a height.';

  values.stud_type = pdsEl('pd-modal-stud-type')?.value || 'M4';
  values.substrate = pdsEl('pd-modal-substrate')?.value || 'Mild Steel';

  const pressure = pdsNum('pd-modal-pressure');
  const pressureMax = pdsLimit('pd-modal-pressure', 'max');
  if (pressure > 0 && pressure <= pressureMax) values.pressure_setting = pressure;
  else errors['pd-modal-pressure'] = `Enter more than 0, up to ${pressureMax} lbf.`;

  values.di_check = pdsEl('pd-modal-di-check')?.checked !== false;

  const speed = Math.round(pdsNum('pd-modal-speed'));
  const speedMin = pdsLimit('pd-modal-speed', 'min');
  const speedMax = pdsLimit('pd-modal-speed', 'max');
  if (speed >= speedMin && speed <= speedMax) values.speed = speed;
  else errors['pd-modal-speed'] = `Enter ${speedMin} to ${speedMax} %.`;

  values.dsc_enabled = pdsEl('pd-modal-dsc-enabled')?.checked === true;
  const reloadMs = Math.round(pdsNum('pd-modal-stud-reload-ms'));
  const reloadMin = pdsLimit('pd-modal-stud-reload-ms', 'min');
  const reloadMax = pdsLimit('pd-modal-stud-reload-ms', 'max');
  if (reloadMs >= reloadMin && reloadMs <= reloadMax) values.stud_reload_ms = reloadMs;
  else if (!values.dsc_enabled) values.stud_reload_ms = normalizeStudReloadMs(reloadMs);
  else errors['pd-modal-stud-reload-ms'] = `Enter ${reloadMin} to ${reloadMax} ms.`;

  values.origin_corner = normalizeCorner(_pdsCorner);

  return { values, errors };
}

// Redraws everything that depends on the fields: tab summaries and dots, the
// warning notes, field errors and the heights drawing. Returns pdsReadForm().
function pdsRefresh() {
  const form = pdsReadForm();
  const modal = pdsEl('pd-settings-modal');
  if (!modal) return form;

  const raw = id => (pdsEl(id)?.value || '').trim() || '—';
  const diOn = pdsEl('pd-modal-di-check')?.checked !== false;
  const dscOn = pdsEl('pd-modal-dsc-enabled')?.checked === true;

  pdsText('pds-sum-heights', `Safe ${raw('pd-modal-safe-z')} · Part ${raw('pd-modal-part-z')} ${unitLabel(_pdsUnits)}`);
  pdsText('pds-sum-weld', `${raw('pd-modal-stud-type')} · ${raw('pd-modal-pressure')} lbf · DI ${diOn ? 'on' : 'off'}`);
  pdsText('pds-sum-motion', `${raw('pd-modal-speed')}% · DSC ${dscOn ? 'on' : 'off'}`);
  pdsText('pds-sum-origin', cornerLabel(_pdsCorner));

  const diNote = pdsEl('pds-di-note');
  if (diNote) diNote.hidden = diOn;
  const calibrationNote = pdsEl('pds-dsc-cal-note');  // rendered only on an uncalibrated machine
  if (calibrationNote) calibrationNote.hidden = !dscOn;
  const reload = pdsEl('pd-modal-stud-reload-ms');
  if (reload) reload.disabled = !dscOn;

  // A field shows its error once it has been committed or Apply was tried, not
  // while the first digit is still being typed.
  const tabsWithErrors = new Set();
  let errorCount = 0;
  modal.querySelectorAll('.pds-input').forEach(input => {
    const message = (_pdsShowErrors || input.dataset.touched) ? (form.errors[input.id] || '') : '';
    input.classList.toggle('is-invalid', !!message);
    input.setAttribute('aria-invalid', message ? 'true' : 'false');
    pdsText(`${input.id}-error`, message);
    if (message) {
      errorCount += 1;
      tabsWithErrors.add(input.closest('.pds-panel')?.dataset.tab);
    }
  });

  const warnings = { weld: !diOn, motion: dscOn && !!calibrationNote };
  modal.querySelectorAll('.pds-tab').forEach(tab => {
    const name = tab.dataset.tab;
    tab.dataset.state = tabsWithErrors.has(name) ? 'error' : (warnings[name] ? 'warn' : '');
  });
  pdsText('pds-foot-msg', errorCount ? `Fix ${errorCount} field${errorCount === 1 ? '' : 's'} before saving.` : '');

  pdsDrawHeights();
  pdsDrawOrigin();
  return form;
}

function pdsSetCorner(corner) {
  _pdsCorner = normalizeCorner(corner);
  pdsRefresh();
}

// The Origin tab: the picked corner's button, and a drawing of the bed as the
// operator faces it with that corner's 0,0 and X/Y arrows running inward.
function pdsDrawOrigin() {
  document.querySelectorAll('#pd-settings-modal .pds-corner-btn').forEach(btn => {
    const on = btn.dataset.corner === _pdsCorner;
    btn.classList.toggle('active', on);
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
  });

  const m = cornerMirrors(_pdsCorner);
  const [left, right, top, bottom, length] = [40, 170, 20, 150, 72];  // the bed rect in the SVG
  const x0 = m.x ? right : left, y0 = m.y ? top : bottom;
  const dx = m.x ? -1 : 1, dy = m.y ? 1 : -1;  // inward
  const place = (id, attrs) => {
    const el = pdsEl(id);
    if (el) Object.entries(attrs).forEach(([name, value]) => el.setAttribute(name, value));
  };
  place('pds-od-x', { x1: x0, y1: y0, x2: x0 + dx * length, y2: y0 });
  place('pds-od-y', { x1: x0, y1: y0, x2: x0, y2: y0 + dy * length });
  place('pds-od-dot', { cx: x0, cy: y0 });
  place('pds-od-x-label', { x: x0 + dx * (length - 6), y: y0 + dy * 12 + 4 });
  place('pds-od-y-label', { x: x0 + dx * 12, y: y0 + dy * (length - 6) + 4 });
  place('pds-od-zero', { x: x0 + dx * 22, y: y0 + dy * 20 + 4 });
}

function pdsDrawHeights() {
  const unit = unitLabel(_pdsUnits);
  const safeZ = pdsNum('pd-modal-safe-z');
  const searchZ = pdsNum('pd-modal-retract-z');
  const partZ = pdsNum('pd-modal-part-z');
  const show = (value, sign) => (Number.isFinite(value) ? `${sign}${value} ${unit}` : '—');

  // The higher plane takes the top slot, so the drawing never puts the search
  // height above Safe Z unless it really is.
  const searchOnTop = searchZ > safeZ;
  pdsEl('pds-zd-safe')?.setAttribute('transform', `translate(0 ${searchOnTop ? 122 : 52})`);
  pdsEl('pds-zd-search')?.setAttribute('transform', `translate(0 ${searchOnTop ? 52 : 122})`);
  pdsText('pds-zd-safe-val', show(safeZ, '+'));
  pdsText('pds-zd-search-val', show(searchZ, '+'));
  pdsText('pds-zd-part-val', show(partZ, ''));
}

function pdsSetUnits(units) {
  const next = units === 'in' ? 'in' : 'mm';
  if (next === _pdsUnits) return;
  const toNext = lengthFactor(_pdsUnits);
  PDS_LENGTH_IDS.forEach(id => {
    const value = pdsNum(id);
    if (Number.isFinite(value)) pdsSetValue(id, formatLength(value * toNext, next));
  });
  _pdsUnits = next;
  pdsSyncUnits();
  pdsRefresh();
}

function pdsSyncUnits() {
  document.querySelectorAll('#pd-settings-modal .pds-seg-btn').forEach(btn => {
    const on = btn.dataset.units === _pdsUnits;
    btn.classList.toggle('active', on);
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
  document.querySelectorAll('#pd-settings-modal .pd-length-unit').forEach(el => { el.textContent = unitLabel(_pdsUnits); });
  PDS_LENGTH_IDS.forEach(id => {
    const el = pdsEl(id);
    if (el) el.step = lengthStep(_pdsUnits);
  });
}

function pdsSelectTab(name, focusTab) {
  _pdsTab = PDS_TABS.includes(name) ? name : PDS_TABS[0];
  document.querySelectorAll('#pd-settings-modal .pds-tab').forEach(tab => {
    const on = tab.dataset.tab === _pdsTab;
    tab.classList.toggle('active', on);
    tab.setAttribute('aria-selected', on ? 'true' : 'false');
    tab.tabIndex = on ? 0 : -1;
    if (on && focusTab) tab.focus();
  });
  document.querySelectorAll('#pd-settings-modal .pds-panel').forEach(panel => {
    panel.classList.toggle('active', panel.dataset.tab === _pdsTab);
  });
}

function pdsOnKeydown(e) {
  if (e.key === 'Escape') {
    e.preventDefault();
    pdCloseJobSettingsModal();
    return;
  }
  // Arrow keys move between tabs, as a tablist should.
  const tab = e.target.closest?.('.pds-tab');
  if (!tab) return;
  const index = PDS_TABS.indexOf(tab.dataset.tab);
  const next = {
    ArrowRight: PDS_TABS[(index + 1) % PDS_TABS.length],
    ArrowLeft: PDS_TABS[(index - 1 + PDS_TABS.length) % PDS_TABS.length],
    Home: PDS_TABS[0],
    End: PDS_TABS[PDS_TABS.length - 1],
  }[e.key];
  if (!next) return;
  e.preventDefault();
  pdsSelectTab(next, true);
}

function normalizeStudReloadMs(value) {
  const reloadMs = Math.round(parseFloat(value));
  return Number.isFinite(reloadMs) ? Math.max(1, Math.min(10000, reloadMs)) : 600;
}

function pdOpenJobReportsModal() {
  const modal = document.getElementById('pd-reports-modal');
  if (!modal) return;
  const nameEl = document.getElementById('pd-report-part-name');
  if (nameEl) nameEl.textContent = _state.activePart || 'Untitled';

  const studsEl = document.getElementById('pd-report-studs');
  if (studsEl) studsEl.textContent = _state.points ? `${_state.points.length} stud${_state.points.length !== 1 ? 's' : ''}` : '0 studs';

  const r = _currentRecipe || {};
  const runsEl = document.getElementById('pd-report-runs');
  if (runsEl) runsEl.textContent = r.times_ran !== undefined ? `${r.times_ran} run${r.times_ran !== 1 ? 's' : ''}` : '0 runs';

  const avgEl = document.getElementById('pd-report-avg-time');
  if (avgEl) avgEl.textContent = r.avg_cycle_time ? `${r.avg_cycle_time.toFixed(1)}s` : '—';

  const lastEl = document.getElementById('pd-report-last-run');
  if (lastEl) lastEl.textContent = r.updated_label || r.last_run || 'Never';

  const tbody = document.getElementById('pd-report-runs-tbody');
  if (tbody) {
    if (_state.activeId) {
      tbody.innerHTML = `<tr><td colspan="4" class="mgr-table-empty">Loading runs...</td></tr>`;
      fetch(`/api/reports/parts/${encodeURIComponent(_state.activeId)}`)
        .then(res => res.json())
        .then(data => {
          const runs = (data && data.runs) || [];
          if (runs.length === 0) {
            tbody.innerHTML = `<tr><td colspan="4" class="mgr-table-empty">No production runs recorded for this part</td></tr>`;
            return;
          }
          tbody.innerHTML = runs.map(run => {
            const avgT = (run.cycle_times && run.cycle_times.length)
              ? (run.cycle_times.reduce((a, b) => a + b, 0) / run.cycle_times.length).toFixed(1) + 's'
              : '—';
            const statusCls = run.status === 'completed' ? 'completed' : (run.status === 'error' ? 'error' : 'stopped');
            return `
              <tr>
                <td>${run.started_at ? run.started_at.replace('T', ' ') : '—'}</td>
                <td><span class="job-status-badge ${statusCls}">${run.status || 'unknown'}</span></td>
                <td>${run.cycles_done || 0} / ${run.cycles_target || 1}</td>
                <td>${avgT}</td>
              </tr>
            `;
          }).join('');
        })
        .catch(() => {
          tbody.innerHTML = `<tr><td colspan="4" class="mgr-table-empty error">Failed to load run history</td></tr>`;
        });
    } else {
      tbody.innerHTML = `<tr><td colspan="4" class="mgr-table-empty">Save or select a part to view run logs</td></tr>`;
    }
  }

  modal.removeAttribute('hidden');
}

function pdCloseJobReportsModal() {
  const modal = document.getElementById('pd-reports-modal');
  if (modal) modal.setAttribute('hidden', '');
}

// ── Dry Run Confirmation ──────────────────────────────────────────────────────

function pdOpenDryRunConfirmModal() {
  if (!_state.activeId && !_state.activePart) return;
  const modal = document.getElementById('pd-dry-run-confirm-modal');
  const nameEl = document.getElementById('pd-dry-run-part-name');
  if (!modal) return;
  if (nameEl) nameEl.textContent = _state.activePart || 'Untitled';
  modal.removeAttribute('hidden');
}

function pdCloseDryRunConfirmModal() {
  const modal = document.getElementById('pd-dry-run-confirm-modal');
  if (modal) modal.setAttribute('hidden', '');
}

function pdConfirmDryRun() {
  if (!_state.activeId && !_state.activePart) {
    pdCloseDryRunConfirmModal();
    return;
  }
  pdCloseDryRunConfirmModal();
  // Save first to ensure current state is persisted
  pdSave();
  // Brief delay to ensure save completes before loading
  setTimeout(() => {
    const partId = _state.activeId;
    const partName = _state.activePart;
    if (partId) {
      const speed = Number.isFinite(_state.speed) ? Math.max(1, Math.min(100, Math.round(_state.speed))) : 25;
      fetch('/ui/job/load', {
        method: 'POST',
        body: new URLSearchParams({
          recipe_id: partId,
          cycles: 1,
          gate_mode: 'none',
          arm_mode: 'dry',
          speed,
        })
      })
      .then(r => r.json())
      .then(data => {
        if (data.ok) {
          // Open the operator page in a new tab so the job can run alongside the designer
          window.open('/operator', '_blank');
        } else {
          alert(`Failed to load job: ${data.error || 'Unknown error'}`);
        }
      })
      .catch(err => {
        alert(`Error loading dry run: ${err}`);
      });
    }
  }, 100);
}

// ── Rename Part ───────────────────────────────────────────────────────────────

function pdOpenRenameModal() {
  if (!_state.activeId && !_state.activePart) return;
  const modal = document.getElementById('pd-rename-modal');
  const input = document.getElementById('pd-rename-input');
  if (!modal || !input) return;
  input.value = _state.activePart || '';
  modal.removeAttribute('hidden');
  input.focus();
  input.select();
}

function pdCancelRename() {
  const modal = document.getElementById('pd-rename-modal');
  const input = document.getElementById('pd-rename-input');
  if (modal) modal.setAttribute('hidden', '');
  if (input) input.value = '';
}

function pdConfirmRename() {
  const input = document.getElementById('pd-rename-input');
  if (!input) return;
  const newName = input.value.trim();
  if (!newName) return;

  _state.activePart = newName;
  const titleEl = document.getElementById('pd-canvas-part-title');
  if (titleEl) titleEl.textContent = newName;

  pdCancelRename();
  pdSetDirty(true);
  pdSave();
}

