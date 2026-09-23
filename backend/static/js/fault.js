/*
 * Header fault modal (#fault-modal in base.html), opened from the State chip
 * while it shows FAULT / E-STOP.
 *
 * The panel body is fetched on open and re-polled once a second while the modal
 * is visible, then emptied on close so nothing polls behind a hidden dialog.
 * /ui/fault/status is a pure cache read, the same as the header chips.
 */
(function () {
  var POLL_MS = 1000;
  var timer = null;

  function modal() { return document.getElementById('fault-modal'); }

  function refresh() {
    var m = modal();
    if (!m || m.hidden || !window.htmx) return;
    htmx.ajax('GET', '/ui/fault/status', { target: '#fault-panel', swap: 'innerHTML' });
  }

  function open() {
    var m = modal();
    if (!m) return;
    m.hidden = false;
    refresh();
    clearInterval(timer);
    timer = setInterval(refresh, POLL_MS);
  }

  function close() {
    var m = modal();
    if (!m) return;
    m.hidden = true;
    clearInterval(timer);
    timer = null;
    var panel = document.getElementById('fault-panel');
    if (panel) panel.innerHTML = '';
  }

  window.WfFault = { open: open, close: close, refresh: refresh };
})();
