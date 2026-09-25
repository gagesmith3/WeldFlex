// Runs INSIDE the controller's web app, injected by the kiosk's nginx proxy
// (deploy/rpi/nginx-robot-web.conf). Not loaded by any WeldFlex page.
//
// The kiosk has no system on-screen keyboard, and WeldFlex's own keyboard
// (keyboard.js) cannot reach into a cross-origin frame. So when a field here
// takes focus, this tells the framing WeldFlex page (robot_web.html), which
// opens its keyboard; the page sends back the field's new value on every key.
// Inputs, textareas and contenteditable cells (the points page's name column).

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

  // The field to type into, or null. The points page edits names in place in a
  // contenteditable table cell; focus can land on a child of it, so take the host.
  function editableTarget(el) {
    if (!el) return null;
    if (el.isContentEditable) {
      var host = el;
      while (host.parentElement && host.parentElement.isContentEditable) host = host.parentElement;
      return host;
    }
    if (el.readOnly || el.disabled) return null;
    if (el.tagName === 'TEXTAREA') return el;
    if (el.tagName === 'INPUT' && TEXT_TYPES.indexOf((el.getAttribute('type') || '').toLowerCase()) !== -1) return el;
    return null;
  }

  function getValue(el) { return el.isContentEditable ? el.textContent : el.value; }
  function setValue(el, v) {
    if (el.isContentEditable) el.textContent = v; else el.value = v;
  }

  // An in-place cell editor commits on Enter or blur. Its real blur fired when
  // the WeldFlex keyboard took focus, before anything was typed, so replay both
  // once typing is done. Synthetic key events run the page's handlers but insert
  // nothing, so the Enter adds no line break.
  function commitCell(el) {
    ['keydown', 'keypress', 'keyup'].forEach(function (t) {
      el.dispatchEvent(new KeyboardEvent(t, { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true }));
    });
    el.dispatchEvent(new FocusEvent('blur'));
    el.dispatchEvent(new FocusEvent('focusout', { bubbles: true }));
  }

  // The label nearest before the field: the login page stacks label/input pairs
  // in one container, so the container's first label would name every field.
  function labelFor(el) {
    // A table cell is named by its column header.
    var cell = el.closest('td, th');
    var table = cell && cell.closest('table');
    var th = table && table.querySelector('thead tr, tr');
    th = th && th.children[cell.cellIndex];
    if (th && th !== cell && th.textContent.trim()) return th.textContent.trim().slice(0, 40);

    var lbl = null;
    if (el.id) lbl = document.querySelector('label[for="' + el.id + '"]');
    for (var sib = el.previousElementSibling; !lbl && sib; sib = sib.previousElementSibling) {
      if (sib.tagName === 'LABEL') lbl = sib;
    }
    var text = (lbl && lbl.textContent.trim()) || el.placeholder || el.name || el.type || 'input';
    return text.slice(0, 40);
  }

  document.addEventListener('focusin', function (e) {
    var el = editableTarget(e.target);
    if (!el) return;
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
      value: getValue(el)
    }, parentOrigin);
  }, true);

  window.addEventListener('message', function (e) {
    if (e.origin !== parentOrigin || !e.data || !active || e.data.id !== seq) return;
    if (e.data.wf === 'kbd-value') {
      setValue(active, String(e.data.value));
      // Angular's ng-model listens for `input`; `change` covers everything else.
      active.dispatchEvent(new Event('input', { bubbles: true }));
      active.dispatchEvent(new Event('change', { bubbles: true }));
    } else if (e.data.wf === 'kbd-reveal') {
      // The page just shrank this frame to clear its keyboard.
      active.scrollIntoView({ block: 'center' });
    } else if (e.data.wf === 'kbd-done') {
      if (active.isContentEditable) commitCell(active);
      active = null;
    }
  });
})();
