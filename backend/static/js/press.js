// Touch press feedback for card-style controls.
//
// Chromium latches :hover onto the last-tapped element and holds it until you
// tap elsewhere, so on the kiosk touchscreen a tapped card stayed looking
// "selected" after the finger lifted. operator.css therefore gates every
// :hover rule on `body:not(.kiosk)` — which leaves these controls with no tap
// feedback at all, since (unlike .btn) they carry no :active styling of their
// own. This file supplies it: .is-pressed goes on at pointerdown and comes off
// the moment the pointer is released, anywhere on the page.
//
// Same shape as jog.html's .is-jogging handling — the release listeners are on
// document in the capture phase so a finger that slides off the control before
// lifting still clears the state.

(function () {
  // Card-style controls that lost their :hover feedback on the kiosk. Plain
  // .btn is deliberately absent: .btn:active already handles it and is not
  // hover-gated.
  var SELECTOR = [
    '.fp-menu-btn',
    '.home-nav-card',
    '.landing-card',
    '.calib-menu-card',
    '.mgr-menu-btn',
    '.pd-part-item'
  ].join(',');

  var pressed = null;

  function release() {
    if (!pressed) return;
    pressed.classList.remove('is-pressed');
    pressed = null;
  }

  document.addEventListener('pointerdown', function (e) {
    var el = e.target.closest ? e.target.closest(SELECTOR) : null;
    if (!el || el.disabled) return;
    release();
    pressed = el;
    el.classList.add('is-pressed');
  }, true);

  ['pointerup', 'pointercancel'].forEach(function (evt) {
    document.addEventListener(evt, release, true);
  });

  // The pointer left the window entirely without a pointerup we can see.
  document.addEventListener('pointerleave', release, true);

  // An htmx swap can replace the pressed element mid-press, orphaning the
  // reference before any release event reaches it.
  document.body.addEventListener('htmx:afterSwap', release);
})();
