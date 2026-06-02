(function () {
  function notifyStudsChanged() {
    if (document.body) {
      document.body.dispatchEvent(new CustomEvent("studs-changed", { bubbles: true }));
    }
  }

  function parseStudsText(text) {
    if (!text || !text.trim()) {
      return [];
    }

    var rows = [];
    text.split(/\r?\n/).forEach(function (line) {
      var cleaned = line.trim().replace(/,$/, "");
      if (!cleaned) {
        return;
      }

      var parts = cleaned.split(",");
      if (parts.length === 2) {
        rows.push({ x: parts[0].trim(), y: parts[1].trim() });
      }
    });

    return rows;
  }

  function renumberRows(tbody) {
    var trs = tbody.querySelectorAll("tr");
    trs.forEach(function (tr, idx) {
      var numCell = tr.querySelector(".row-num");
      if (numCell) {
        numCell.textContent = String(idx + 1);
      }
    });
  }

  function syncHiddenField() {
    var tbody = document.getElementById("coord-table-body");
    var hidden = document.getElementById("studs_text");
    if (!tbody || !hidden) {
      return;
    }

    var lines = [];
    var rows = tbody.querySelectorAll("tr");
    rows.forEach(function (row) {
      var x = (row.querySelector("input[name='x']") || {}).value || "";
      var y = (row.querySelector("input[name='y']") || {}).value || "";
      var xTrim = x.trim();
      var yTrim = y.trim();
      if (!xTrim && !yTrim) {
        return;
      }
      lines.push(xTrim + "," + yTrim);
    });

    hidden.value = lines.join("\n");
    notifyStudsChanged();
  }

  function makeRow(xVal, yVal) {
    var tr = document.createElement("tr");
    tr.innerHTML = ""
      + "<td class='row-num'></td>"
      + "<td><input type='number' step='any' name='x' value='" + (xVal || "") + "' placeholder='X' required></td>"
      + "<td><input type='number' step='any' name='y' value='" + (yVal || "") + "' placeholder='Y' required></td>"
      + "<td><button class='btn row-remove' type='button'>Remove</button></td>";
    return tr;
  }

  function ensureAtLeastOneRow(tbody) {
    if (!tbody.querySelector("tr")) {
      tbody.appendChild(makeRow("", ""));
    }
  }

  function initCoordTable() {
    var tbody = document.getElementById("coord-table-body");
    var hidden = document.getElementById("studs_text");
    var addBtn = document.getElementById("add-row-btn");

    if (!tbody || !hidden || !addBtn) {
      return;
    }

    tbody.innerHTML = "";

    var initialRows = parseStudsText(hidden.value);
    if (initialRows.length === 0) {
      initialRows = [{ x: "", y: "" }];
    }

    initialRows.forEach(function (row) {
      tbody.appendChild(makeRow(row.x, row.y));
    });

    renumberRows(tbody);
    syncHiddenField();

    addBtn.addEventListener("click", function () {
      tbody.appendChild(makeRow("", ""));
      renumberRows(tbody);
      syncHiddenField();
    });

    tbody.addEventListener("click", function (event) {
      var target = event.target;
      if (!(target instanceof HTMLElement)) {
        return;
      }
      if (!target.classList.contains("row-remove")) {
        return;
      }

      var row = target.closest("tr");
      if (row) {
        row.remove();
      }

      ensureAtLeastOneRow(tbody);
      renumberRows(tbody);
      syncHiddenField();
    });

    tbody.addEventListener("input", syncHiddenField);

    var runForm = document.querySelector("form[hx-post='/ui/run']");
    if (runForm) {
      runForm.addEventListener("submit", syncHiddenField);
    }
  }

  document.addEventListener("DOMContentLoaded", initCoordTable);
  document.addEventListener("htmx:afterSwap", function (event) {
    var target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    if (target.id === "studs-input-block") {
      initCoordTable();
    }
  });
})();
