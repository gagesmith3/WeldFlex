(function () {
  'use strict';

  var kbd = null;
  var active = null;
  var shifted = false;
  var blurTimer = null;

  function init() {
    kbd = document.getElementById('wf-kbd');
    if (!kbd) return;

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
    requestAnimationFrame(function () {
      document.documentElement.style.scrollPaddingBottom = kbd.offsetHeight + 'px';
      if (active) active.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    });
  }

  function hideKbd() {
    kbd.classList.add('wf-kbd-hidden');
    kbd.setAttribute('aria-hidden', 'true');
    document.documentElement.style.scrollPaddingBottom = '';
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
