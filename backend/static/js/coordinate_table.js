(function () {
  var dragState = null;

  // ── Drag-and-drop (pointer events, touch-compatible) ──────────────────────

  document.addEventListener('pointermove', function (e) {
    if (!dragState) return;
    e.preventDefault();

    var tbody = dragState.tbody;
    var rows = Array.from(tbody.querySelectorAll('tr:not(.dragging):not(.drag-placeholder)'));
    var placed = false;

    for (var i = 0; i < rows.length; i++) {
      var rect = rows[i].getBoundingClientRect();
      if (e.clientY < rect.top + rect.height / 2) {
        tbody.insertBefore(dragState.placeholder, rows[i]);
        placed = true;
        break;
      }
    }

    if (!placed) {
      tbody.appendChild(dragState.placeholder);
    }
  }, { passive: false });

  function endDrag() {
    if (!dragState) return;
    var tbody = dragState.tbody;
    tbody.insertBefore(dragState.row, dragState.placeholder);
    dragState.placeholder.remove();
    dragState.row.classList.remove('dragging');
    renumberRows(tbody);
    syncHiddenField(tbody);
    dragState = null;
  }

  function cancelDrag() {
    if (!dragState) return;
    dragState.placeholder.remove();
    dragState.row.classList.remove('dragging');
    dragState = null;
  }

  document.addEventListener('pointerup', endDrag);
  document.addEventListener('pointercancel', cancelDrag);

  // ── Table helpers ─────────────────────────────────────────────────────────

  function notifyStudsChanged() {
    if (document.body) {
      document.body.dispatchEvent(new CustomEvent('studs-changed', { bubbles: true }));
    }
  }

  function parseStudsText(text) {
    if (!text || !text.trim()) return [];

    var rows = [];
    text.split(/\r?\n/).forEach(function (line) {
      var cleaned = line.trim().replace(/,$/, '');
      if (!cleaned) return;
      var parts = cleaned.split(',');
      if (parts.length === 2) {
        rows.push({ x: parts[0].trim(), y: parts[1].trim() });
      }
    });
    return rows;
  }

  function renumberRows(tbody) {
    tbody.querySelectorAll('tr').forEach(function (tr, idx) {
      var numCell = tr.querySelector('.row-num');
      if (numCell) numCell.textContent = String(idx + 1);
    });
  }

  function syncHiddenField(tbody) {
    if (!tbody) tbody = document.getElementById('coord-table-body');
    var hidden = document.getElementById('studs_text');
    if (!tbody || !hidden) return;

    var lines = [];
    tbody.querySelectorAll('tr').forEach(function (row) {
      var x = (row.querySelector("input[name='x']") || {}).value || '';
      var y = (row.querySelector("input[name='y']") || {}).value || '';
      if (x.trim() || y.trim()) lines.push(x.trim() + ',' + y.trim());
    });

    hidden.value = lines.join('\n');
    notifyStudsChanged();
  }

  function makeRow(xVal, yVal) {
    var tr = document.createElement('tr');
    tr.innerHTML =
      "<td class='drag-handle-cell'><span class='drag-handle'>⠿</span></td>" +
      "<td class='row-num'></td>" +
      "<td><input type='text' inputmode='none' data-kbd='num' name='x' value='" + (xVal || '') + "' placeholder='X' required></td>" +
      "<td><input type='text' inputmode='none' data-kbd='num' name='y' value='" + (yVal || '') + "' placeholder='Y' required></td>" +
      "<td><button class='btn recipe-save row-action-btn' type='button'>Drag</button></td>" +
      "<td><button class='btn row-remove' type='button'>✕</button></td>";
    return tr;
  }

  function ensureAtLeastOneRow(tbody) {
    if (!tbody.querySelector('tr')) tbody.appendChild(makeRow('', ''));
  }

  // ── Init ──────────────────────────────────────────────────────────────────

  function initCoordTable() {
    var tbody = document.getElementById('coord-table-body');
    var hidden = document.getElementById('studs_text');
    var addBtn = document.getElementById('add-row-btn');
    if (!tbody || !hidden || !addBtn) return;

    tbody.innerHTML = '';
    var initialRows = parseStudsText(hidden.value);
    if (initialRows.length === 0) initialRows = [{ x: '', y: '' }];
    initialRows.forEach(function (row) { tbody.appendChild(makeRow(row.x, row.y)); });
    renumberRows(tbody);
    syncHiddenField(tbody);

    // Add row
    addBtn.addEventListener('click', function () {
      tbody.appendChild(makeRow('', ''));
      renumberRows(tbody);
      syncHiddenField(tbody);
    });

    // Remove row (delegated)
    tbody.addEventListener('click', function (e) {
      if (!(e.target instanceof HTMLElement)) return;
      if (!e.target.classList.contains('row-remove')) return;
      var row = e.target.closest('tr');
      if (row) row.remove();
      ensureAtLeastOneRow(tbody);
      renumberRows(tbody);
      syncHiddenField(tbody);
    });

    // Drag-and-drop (delegated)
    tbody.addEventListener('pointerdown', function (e) {
      var handle = e.target.closest('.drag-handle');
      if (!handle) return;
      var row = handle.closest('tr');
      if (!row || !tbody.contains(row)) return;

      e.preventDefault();
      handle.setPointerCapture(e.pointerId);

      var placeholder = document.createElement('tr');
      placeholder.className = 'drag-placeholder';
      placeholder.innerHTML = '<td colspan="6" class="drag-placeholder-cell"></td>';
      tbody.insertBefore(placeholder, row.nextSibling);

      row.classList.add('dragging');
      dragState = { tbody: tbody, row: row, placeholder: placeholder };
    });

    // Sync on input change
    tbody.addEventListener('input', function () { syncHiddenField(tbody); });
  }

  document.addEventListener('DOMContentLoaded', initCoordTable);
  document.addEventListener('htmx:afterSwap', function (e) {
    if (e.target instanceof HTMLElement && e.target.id === 'studs-input-block') {
      initCoordTable();
    }
  });
})();
