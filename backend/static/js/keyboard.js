(function () {
  'use strict';

  var kbd = null;
  var active = null;
  var shifted = false;
  var blurTimer = null;
  var previewEl = null;
  var previewLabel = null;
  var previewValue = null;

  function init() {
    kbd = document.getElementById('wf-kbd');
    if (!kbd) return;
    previewEl    = document.getElementById('wf-kbd-preview');
    previewLabel = document.getElementById('wf-kbd-preview-label');
    previewValue = document.getElementById('wf-kbd-preview-value');

    // Prevent keyboard clicks from stealing focus away from the active input.
    kbd.addEventListener('mousedown', function (e) { e.preventDefault(); });
    kbd.addEventListener('pointerdown', function (e) { e.preventDefault(); });

    kbd.addEventListener('click', function (e) {
      var keyEl = e.target.closest('[data-key]');
      if (keyEl) handleKey(keyEl.dataset.key);
    });

    document.addEventListener('focus', onFocus, true);
    document.addEventListener('blur', onBlur, true);
  }

  function onFocus(e) {
    var el = e.target;
    if (!el.dataset || !el.dataset.kbd) return;
    clearTimeout(blurTimer);
    active = el;
    showKbd(el.dataset.kbd);
  }

  function onBlur(e) {
    if (!e.target.dataset || !e.target.dataset.kbd) return;
    blurTimer = setTimeout(function () {
      var ae = document.activeElement;
      if (ae && ae.dataset && ae.dataset.kbd) return;
      hideKbd();
      active = null;
    }, 150);
  }

  function showKbd(mode) {
    kbd.dataset.mode = mode;
    kbd.classList.remove('wf-kbd-hidden');
    kbd.setAttribute('aria-hidden', 'false');
    kbd.querySelectorAll('.wf-kbd-panel').forEach(function (p) {
      p.hidden = (p.dataset.panel !== mode);
    });
    shifted = false;
    syncShift();
    updatePreview();
    // For num mode the panel is on the right — inputs stay visible, no scroll needed.
    // For other modes scroll the active input clear of the bottom panel.
    if (mode !== 'num') {
      requestAnimationFrame(function () {
        if (active) active.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      });
      setTimeout(ensureAboveKeyboard, 240);
    }
  }

  function ensureAboveKeyboard() {
    if (!active || kbd.classList.contains('wf-kbd-hidden')) return;
    var kbdH = kbd.offsetHeight;
    var clearance = window.innerHeight - kbdH - 12;
    var rect = active.getBoundingClientRect();
    if (rect.bottom <= clearance) return;
    var overflow = rect.bottom - clearance;
    // Walk up to find the scrollable ancestor and nudge it.
    var el = active.parentElement;
    while (el && el !== document.body) {
      var ov = getComputedStyle(el).overflowY;
      if (ov === 'auto' || ov === 'scroll') {
        el.scrollBy({ top: overflow, behavior: 'smooth' });
        return;
      }
      el = el.parentElement;
    }
    window.scrollBy({ top: overflow, behavior: 'smooth' });
  }

  function updatePreview() {
    if (!previewEl || !previewLabel || !previewValue) return;
    if (!active) { previewEl.hidden = true; return; }

    // Build label: "Row N · X (mm)" for coord table inputs, otherwise input name.
    var name = (active.getAttribute('name') || '').toUpperCase();
    var label = name || 'INPUT';
    var row = active.closest('tr');
    if (row) {
      var numCell = row.querySelector('.row-num');
      if (numCell && numCell.textContent.trim()) {
        label = 'Stud ' + numCell.textContent.trim() + ' · ' + label + ' (mm)';
      }
    }

    previewLabel.textContent = label;
    previewValue.textContent = active.value || '—';
    previewEl.hidden = false;
  }

  function hideKbd() {
    kbd.classList.add('wf-kbd-hidden');
    kbd.setAttribute('aria-hidden', 'true');
    if (previewEl) previewEl.hidden = true;
  }

  function handleKey(key) {
    if (!active) return;

    switch (key) {
      case 'backspace': deleteChar(); break;
      case 'clear':     active.value = ''; break;
      case 'enter':     hideKbd(); active.blur(); return;
      case 'shift':     shifted = !shifted; syncShift(); return;
      case 'negate':    toggleNegate(); break;
      default:          insert(shifted ? key.toUpperCase() : key); break;
    }

    fire();
    updatePreview();
    if (key !== 'shift' && shifted && key.length === 1 && key !== key.toUpperCase()) {
      shifted = false;
      syncShift();
    }
  }

  function insert(ch) {
    var s = getStart();
    var e = getEnd();
    active.value = active.value.slice(0, s) + ch + active.value.slice(e);
    setCaret(s + ch.length);
  }

  function deleteChar() {
    var s = getStart();
    var e = getEnd();
    if (s !== e) {
      active.value = active.value.slice(0, s) + active.value.slice(e);
      setCaret(s);
    } else if (s > 0) {
      active.value = active.value.slice(0, s - 1) + active.value.slice(s);
      setCaret(s - 1);
    }
  }

  function toggleNegate() {
    if (active.value.charAt(0) === '-') {
      active.value = active.value.slice(1);
    } else {
      active.value = '-' + active.value;
    }
  }

  function syncShift() {
    kbd.querySelectorAll('[data-alpha]').forEach(function (el) {
      el.textContent = shifted ? el.dataset.alpha.toUpperCase() : el.dataset.alpha;
    });
    var shiftBtn = kbd.querySelector('[data-key="shift"]');
    if (shiftBtn) shiftBtn.classList.toggle('wf-key-active', shifted);
  }

  function fire() {
    if (!active) return;
    active.dispatchEvent(new Event('input', { bubbles: true }));
    active.dispatchEvent(new Event('change', { bubbles: true }));
  }

  // Selection helpers — number inputs don't support selectionStart/End.
  function getStart() {
    try { return active.selectionStart || active.value.length; } catch (e) { return active.value.length; }
  }
  function getEnd() {
    try { return active.selectionEnd || active.value.length; } catch (e) { return active.value.length; }
  }
  function setCaret(pos) {
    try { active.setSelectionRange(pos, pos); } catch (e) { /* number inputs ignore this */ }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
