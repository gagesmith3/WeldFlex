(function () {
  'use strict';

  var HOLD_MS = 700;
  var _timer    = null;
  var _holdFired = false;

  // Suppress the click that follows a completed hold so the home link doesn't also navigate
  document.addEventListener('click', function (e) {
    if (!_holdFired) return;
    if (!e.target.closest('.home-btn')) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    _holdFired = false;
  }, true);

  function init() {
    var btn = document.querySelector('.home-btn');
    if (!btn) return;

    // A touch hold on a link starts a link drag (~650 ms on the Chromium kiosk,
    // seen on the ED-HMI3020 2026-09-23), and the drag fires pointercancel before
    // HOLD_MS. The CSS -webkit-user-drag: none covers this too; belt and braces.
    btn.setAttribute('draggable', 'false');
    btn.addEventListener('dragstart', function (e) { e.preventDefault(); });

    btn.addEventListener('pointerdown', function () {
      _holdFired = false;
      clearTimeout(_timer);
      _timer = setTimeout(function () {
        _holdFired = true;
        window.location.href = '/operator/admin';
      }, HOLD_MS);
    });

    btn.addEventListener('pointerup',     function () { clearTimeout(_timer); });
    btn.addEventListener('pointerleave',  function () { clearTimeout(_timer); _holdFired = false; });
    btn.addEventListener('pointercancel', function () { clearTimeout(_timer); _holdFired = false; });
    btn.addEventListener('contextmenu',   function (e) { e.preventDefault(); });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
