// Runs INSIDE the controller's web app, injected by the kiosk's nginx proxy
// (deploy/rpi/nginx-robot-web.conf). Not loaded by any WeldFlex page.
//
// The kiosk has no system on-screen keyboard, and WeldFlex's own keyboard
// (keyboard.js) cannot reach into a cross-origin frame. So when a field here
// takes focus, this tells the framing WeldFlex page (robot_web.html), which
// opens its keyboard; the page sends back the field's new value on every key.

(function () {
  'use strict';
  if (window.parent === window) return;  // opened directly, not framed

  // Only the WeldFlex page that framed us may drive fields or see what is typed
  // (passwords included). ancestorOrigins is Chromium-only, which the kiosk is.
  var parentOrigin = (location.ancestorOrigins && location.ancestorOrigins[0]) || null;
  if (!parentOrigin) return;

  var TEXT_TYPES = ['text', 'password', 'number', 'search', 'email', 'tel', 'url', ''];
  var active = null;
  // Tapping from one field to the next blurs the page's relay input (-> kbd-done)
  // after this frame has already opened the new field; the id keeps that late
  // done from dropping the new one.
  var seq = 0;

  function editable(el) {
    if (!el || el.readOnly || el.disabled) return false;
    if (el.tagName === 'TEXTAREA') return true;
    return el.tagName === 'INPUT' && TEXT_TYPES.indexOf((el.getAttribute('type') || '').toLowerCase()) !== -1;
  }

  // The label nearest before the field: the login page stacks label/input pairs
  // in one container, so the container's first label would name every field.
  function labelFor(el) {
    var lbl = null;
    if (el.id) lbl = document.querySelector('label[for="' + el.id + '"]');
    for (var sib = el.previousElementSibling; !lbl && sib; sib = sib.previousElementSibling) {
      if (sib.tagName === 'LABEL') lbl = sib;
    }
    var text = (lbl && lbl.textContent.trim()) || el.placeholder || el.name || el.type || 'input';
    return text.slice(0, 40);
  }

  document.addEventListener('focusin', function (e) {
    var el = e.target;
    if (!editable(el)) return;
    active = el;
    seq += 1;
    var type = (el.getAttribute('type') || '').toLowerCase();
    window.parent.postMessage({
      wf: 'kbd-open',
      id: seq,
      // Letters get the layout with a digit row: robot passwords are often numeric.
      mode: type === 'number' || el.inputMode === 'numeric' || el.inputMode === 'decimal' ? 'num' : 'alphanum',
      secret: type === 'password',
      label: labelFor(el),
      value: el.value
    }, parentOrigin);
  }, true);

  window.addEventListener('message', function (e) {
    if (e.origin !== parentOrigin || !e.data || !active || e.data.id !== seq) return;
    if (e.data.wf === 'kbd-value') {
      active.value = String(e.data.value);
      // Angular's ng-model listens for `input`; `change` covers everything else.
      active.dispatchEvent(new Event('input', { bubbles: true }));
      active.dispatchEvent(new Event('change', { bubbles: true }));
    } else if (e.data.wf === 'kbd-reveal') {
      // The page just shrank this frame to clear its keyboard.
      active.scrollIntoView({ block: 'center' });
    } else if (e.data.wf === 'kbd-done') {
      active = null;
    }
  });
})();
