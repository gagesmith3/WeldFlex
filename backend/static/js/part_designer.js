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
  arm_mode: 'live',
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
  if (settingsModal) settingsModal.addEventListener('click', e => {
    if (e.target === settingsModal) pdCloseJobSettingsModal();
  });

  const dryRunModal = document.getElementById('pd-dry-run-confirm-modal');
  if (dryRunModal) dryRunModal.addEventListener('click', e => {
    if (e.target === dryRunModal) pdCloseDryRunConfirmModal();
  });

  const reportsModal = document.getElementById('pd-reports-modal');
  if (reportsModal) reportsModal.addEventListener('click', e => {
    if (e.target === reportsModal) pdCloseJobReportsModal();
  });
}

// ── Coordinate helpers (physical ↔ SVG) ──────────────────────────────────────
// Physical 0,0 (zerozero) is at the bottom-left corner of the 762×762 bed.
// SVG 0,0 is top-left, so we mirror the Y axis only.

const BED = 762; // 30in bed, in mm
const MM_PER_INCH = 25.4;

function toSVG(px, py)  { return { x: px, y: BED - py }; }
function toPhys(sx, sy) { return { x: sx, y: BED - sy }; }
function isInches() { return _state.units === 'in'; }
function lengthFactor() { return isInches() ? MM_PER_INCH : 1; }
function lengthStep() { return isInches() ? '0.0001' : '0.001'; }
function formatLength(mm) {
  const decimals = isInches() ? 4 : 3;
  return Number((mm / lengthFactor()).toFixed(decimals)).toString();
}
function parseLength(value) { return parseFloat(value) * lengthFactor(); }
function unitLabel() { return isInches() ? 'in' : 'mm'; }

// ── Grid ────────────────────────────────────────────────────────────────────

function buildGrid() {
  const g = document.getElementById('pd-grid');
  if (!g) return;
  g.innerHTML = '';
  const SIZE = BED, STEP = 50;

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

    // Labels for X and Y axes
    if (major && i > 0 && i < SIZE) {
      // X-axis label (bottom edge: i=0 is 0 at zerozero, i=762 is 762)
      const physX = formatLength(i);
      const tx = svgEl('text', {
        x: i + 4, y: SIZE - 6,
        fill:'#7c95a8', 'font-size':'13', 'font-weight':'600', 'font-family':'monospace',
      });
      tx.textContent = `${physX}`;
      g.appendChild(tx);

      // Y-axis label (left edge: i=0 is 762, i=762 is 0 at zerozero)
      const physY = formatLength(BED - i);
      const ty = svgEl('text', {
        x: 4, y: i - 4,
        fill:'#7c95a8', 'font-size':'13', 'font-weight':'600', 'font-family':'monospace',
      });
      ty.textContent = `${physY}`;
      g.appendChild(ty);
    }
  }

  // Origin marker at SVG bottom-left (physical X=0, Y=0 — zerozero)
  const ox = svgEl('text', {
    x: 6, y: SIZE - 20,
    fill:'#5c7fa0', 'font-size':'14', 'font-weight':'700', 'font-family':'monospace',
  });
  ox.textContent = `zerozero (${formatLength(0)}, ${formatLength(0)})`;
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
      }
      renderPoints();
      renderStudList();
      setCoords(null);
      syncUnitsControl();
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
  tip.innerHTML = `<strong>Point ${p.id}</strong><br>X: ${formatLength(p.x)} ${unitLabel()} &nbsp; Y: ${formatLength(p.y)} ${unitLabel()} from zerozero`;
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

  const titleEl = document.getElementById('pd-canvas-part-title');
  if (titleEl) titleEl.textContent = name;

  document.querySelectorAll('.pd-part-item').forEach(el => el.classList.remove('active'));
  renderPoints();
  renderStudList();
  setCoords(null);
  syncUnitsControl();
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
        const titleEl = document.getElementById('pd-canvas-part-title');
        if (titleEl) titleEl.textContent = 'No Part Selected';
        renderPoints();
        renderStudList();
        setCoords(null);
        pdSetDirty(false);
        const coords = document.getElementById('pd-coords');
        if (coords) coords.textContent = 'zerozero reference · 762 × 762 mm bed';
      }
      fetchParts();
    });
}

// ── Coords bar ────────────────────────────────────────────────────────────────

function setCoords(p) {
  const el = document.getElementById('pd-coords');
  if (!el) return;
  el.textContent = p
    ? `Point ${p.id}  ·  X: ${formatLength(p.x)} ${unitLabel()}  ·  Y: ${formatLength(p.y)} ${unitLabel()} from zerozero`
    : `zerozero reference · ${formatLength(BED)} × ${formatLength(BED)} ${unitLabel()} bed`;
}

function syncUnitsControl() {
  const select = document.getElementById('pd-units');
  if (select) select.value = _state.units;
  document.querySelectorAll('.pd-length-unit').forEach(el => { el.textContent = unitLabel(); });
}

function pdSetUnits(units) {
  _state.units = units === 'in' ? 'in' : 'mm';
  buildGrid();
  renderPoints();
  renderStudList();
  const selected = _state.points.find(point => point.id === _state.selectedPoint);
  setCoords(selected || null);
  syncUnitsControl();
  pdSetDirty(true);
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
      <button class="pd-stud-goto-btn" data-pid="${p.id}" title="Move robot to this stud (retract height)">⌖</button>
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
  const retract_z = _state.retract_z !== undefined ? _state.retract_z : 10.0;
  const part_z = _state.part_z !== undefined ? _state.part_z : 0.0;
  fetch('/ui/parts/goto', {
    method: 'POST',
    body: new URLSearchParams({ x: p.x, y: p.y, retract_z, part_z }),
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

// ── Job Settings & Reports Modals ─────────────────────────────────────────────

function pdOpenJobSettingsModal() {
  const modal = document.getElementById('pd-settings-modal');
  if (!modal) return;
  const nameEl = document.getElementById('pd-modal-settings-part-name');
  if (nameEl) nameEl.textContent = _state.activePart || 'Untitled';

  const safeZ = _state.safe_z !== undefined ? _state.safe_z : 60.0;
  const retractZ = _state.retract_z !== undefined ? _state.retract_z : 10.0;
  const partZ = _state.part_z !== undefined ? _state.part_z : 0.0;
  const studType = _state.stud_type || 'M4';
  const substrate = _state.substrate || 'Mild Steel';
  const pressure = _state.pressure_setting || '20.0';
  const speed = _state.speed !== undefined ? _state.speed : 25;
  const armMode = _state.arm_mode === 'dry' ? 'dry' : 'live';

  const mSafeZ = document.getElementById('pd-modal-safe-z');
  const mRetractZ = document.getElementById('pd-modal-retract-z');
  const mPartZ = document.getElementById('pd-modal-part-z');
  const mStudType = document.getElementById('pd-modal-stud-type');
  const mSubstrate = document.getElementById('pd-modal-substrate');
  const mPressure = document.getElementById('pd-modal-pressure');
  const mSpeed = document.getElementById('pd-modal-speed');
  const mArmMode = document.getElementById('pd-modal-arm-mode');

  if (mSafeZ) { mSafeZ.value = formatLength(safeZ); mSafeZ.step = lengthStep(); }
  if (mRetractZ) { mRetractZ.value = formatLength(retractZ); mRetractZ.step = lengthStep(); }
  if (mPartZ) { mPartZ.value = formatLength(partZ); mPartZ.step = lengthStep(); }
  if (mStudType) mStudType.value = studType;
  if (mSubstrate) mSubstrate.value = substrate;
  if (mPressure) mPressure.value = pressure;
  if (mSpeed) mSpeed.value = speed;
  if (mArmMode) mArmMode.value = armMode;

  modal.removeAttribute('hidden');
}

function pdCloseJobSettingsModal() {
  const modal = document.getElementById('pd-settings-modal');
  if (modal) modal.setAttribute('hidden', '');
}

function pdSaveJobSettingsModal() {
  const mSafeZ = document.getElementById('pd-modal-safe-z')?.value;
  const mRetractZ = document.getElementById('pd-modal-retract-z')?.value;
  const mPartZ = document.getElementById('pd-modal-part-z')?.value;
  const mStudType = document.getElementById('pd-modal-stud-type')?.value;
  const mSubstrate = document.getElementById('pd-modal-substrate')?.value;
  const mPressure = document.getElementById('pd-modal-pressure')?.value;
  const mSpeed = document.getElementById('pd-modal-speed')?.value;

  if (mSafeZ !== undefined) _state.safe_z = parseLength(mSafeZ) || 60.0;
  if (mRetractZ !== undefined) _state.retract_z = parseLength(mRetractZ) || 10.0;
  if (mPartZ !== undefined) _state.part_z = parseLength(mPartZ) || 0.0;
  if (mStudType !== undefined) _state.stud_type = mStudType;
  if (mSubstrate !== undefined) _state.substrate = mSubstrate;
  if (mPressure !== undefined) _state.pressure_setting = parseFloat(mPressure) || 20.0;
  if (mSpeed !== undefined) {
    const speed = Math.round(parseFloat(mSpeed));
    _state.speed = Number.isFinite(speed) ? Math.max(1, Math.min(100, speed)) : 25;
  }

  pdCloseJobSettingsModal();
  pdSetDirty(true);
  pdSave();
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

