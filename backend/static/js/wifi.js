// Settings page Wi-Fi card: opens the network sheet for a tapped row.
// The card itself is server-rendered (partials/wifi_card.html) and swaps on
// every action, so row clicks are delegated from the document.
(function () {
  'use strict';

  var overlay, form, title, note, ssidRow, ssidInput, ssidHidden, hiddenFlag,
      pwRow, pwInput, pwToggle, connectBtn, forgetBtn;
  var current = null;   // the tapped row's dataset, or {other: '1'}
  var forgetArmed = false;

  function init() {
    overlay = document.getElementById('wifi-modal');
    if (!overlay) return;
    form = overlay.querySelector('form');
    title = document.getElementById('wifi-modal-title');
    note = document.getElementById('wifi-modal-note');
    ssidRow = document.getElementById('wifi-modal-ssid-row');
    ssidInput = document.getElementById('wifi-modal-ssid');
    ssidHidden = document.getElementById('wifi-modal-ssid-hidden');
    hiddenFlag = form.querySelector('input[name="hidden"]');
    pwRow = document.getElementById('wifi-modal-pw-row');
    pwInput = document.getElementById('wifi-modal-pw');
    pwToggle = document.getElementById('wifi-modal-pw-toggle');
    connectBtn = document.getElementById('wifi-modal-connect');
    forgetBtn = document.getElementById('wifi-modal-forget');

    document.addEventListener('click', function (e) {
      var row = e.target.closest('.wifi-row');
      if (!row || row.disabled) return;
      if (row.dataset.hotspot === '1') openHotspot(row.dataset);
      else open(row.dataset);
    });
    initHotspot();
    overlay.querySelector('[data-wifi-close]').addEventListener('click', close);
    overlay.addEventListener('click', function (e) { if (e.target === overlay) close(); });

    pwToggle.addEventListener('click', function () {
      var show = pwInput.type === 'password';
      pwInput.type = show ? 'text' : 'password';
      pwToggle.textContent = show ? 'Hide' : 'Show';
    });

    forgetBtn.addEventListener('click', function () {
      // Two taps, not a confirm dialog: a modal on top of this modal is clumsy on
      // the kiosk, and forgetting the connected network drops the panel off it.
      if (!forgetArmed) {
        forgetArmed = true;
        forgetBtn.textContent = 'Tap again to forget';
        return;
      }
      var ssid = current.ssid;
      close();
      htmx.ajax('POST', '/ui/wifi/forget', {
        target: '#wifi-card', swap: 'outerHTML', values: { ssid: ssid }
      });
    });

    // Validate before htmx sends. configRequest, not beforeRequest: htmx has already
    // collected the form values by beforeRequest, and cancelling either stops the send.
    form.addEventListener('htmx:configRequest', function (e) {
      if (current && current.other === '1') {
        ssidHidden.value = ssidInput.value.trim();
        e.detail.parameters.ssid = ssidHidden.value;
      }
      var err = '';
      if (!ssidHidden.value) err = 'Enter the network name.';
      else if (!pwRow.hidden && current.other !== '1' && pwInput.value.length < 8) err = 'A Wi-Fi password is at least 8 characters.';
      else if (pwInput.value && (pwInput.value.length < 8 || pwInput.value.length > 64)) err = 'A Wi-Fi password is 8 to 63 characters.';
      if (err) {
        e.preventDefault();
        showNote(err);
        return;
      }
      close();
    });
    // Don't leave the password sitting in the DOM once it has been sent.
    form.addEventListener('htmx:afterRequest', function () { pwInput.value = ''; });
  }

  function showNote(text) {
    note.textContent = text;
    note.hidden = !text;
  }

  function open(data) {
    current = data;
    forgetArmed = false;
    forgetBtn.textContent = 'Forget this network';
    pwInput.value = '';
    pwInput.type = 'password';
    pwToggle.textContent = 'Show';
    ssidInput.value = '';
    showNote('');

    var other = data.other === '1';
    var secured = data.secured === '1';
    var saved = data.saved === '1';
    var isCurrent = data.current === '1';
    var enterprise = data.enterprise === '1';

    title.textContent = other ? 'Other network' : data.ssid;
    ssidHidden.value = other ? '' : data.ssid;
    hiddenFlag.value = other ? '1' : '0';
    ssidRow.hidden = !other;
    pwRow.hidden = !(other || (secured && !saved && !enterprise));
    forgetBtn.hidden = !saved;
    connectBtn.hidden = isCurrent || enterprise;

    if (isCurrent) showNote('This panel is connected to this network.');
    else if (enterprise) showNote('This network needs a username or certificate (WPA-Enterprise). It has to be set up over SSH.');
    else if (saved) showNote('Saved network. To change its password, forget it and join again.');
    else if (other) showNote('For a hidden network. Leave the password blank if it is open.');
    else if (!secured) showNote('Open network. No password needed.');

    overlay.hidden = false;
    var first = other ? ssidInput : (!pwRow.hidden ? pwInput : null);
    if (first) first.focus();
  }

  function close() {
    overlay.hidden = true;
    if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
  }

  // ── hotspot sheet ──
  var hsOverlay, hsSsid, hsPw, hsError;

  function initHotspot() {
    hsOverlay = document.getElementById('hotspot-modal');
    if (!hsOverlay) return;
    hsSsid = document.getElementById('hotspot-modal-ssid');
    hsPw = document.getElementById('hotspot-modal-pw');
    hsError = document.getElementById('hotspot-modal-error');
    hsOverlay.querySelector('[data-hotspot-close]').addEventListener('click', closeHotspot);
    hsOverlay.addEventListener('click', function (e) { if (e.target === hsOverlay) closeHotspot(); });

    hsOverlay.querySelector('form').addEventListener('htmx:configRequest', function (e) {
      var ssid = hsSsid.value.trim();
      e.detail.parameters.ssid = ssid;
      var err = '';
      if (!ssid) err = 'Enter a name for the hotspot.';
      else if (new TextEncoder().encode(ssid).length > 32) err = 'The name is too long (32 characters at most).';
      else if (hsPw.value.length < 8 || hsPw.value.length > 63) err = 'The password is 8 to 63 characters.';
      if (err) {
        e.preventDefault();
        hsError.textContent = err;
        hsError.hidden = false;
        return;
      }
      closeHotspot();
    });
  }

  function openHotspot(data) {
    if (!hsOverlay) return;
    hsSsid.value = data.ssid || '';
    hsPw.value = data.password || '';
    hsError.hidden = true;
    hsOverlay.hidden = false;
  }

  function closeHotspot() {
    hsOverlay.hidden = true;
    if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
