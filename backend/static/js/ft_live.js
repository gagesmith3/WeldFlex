(function () {
  'use strict';

  var readout = document.getElementById('ft-readout');
  if (!readout) return;
  var staleHtml = readout.innerHTML;
  var source = null;
  var retry = null;
  var pending = null;
  var lastMessage = 0;

  function stop() {
    clearTimeout(retry);
    retry = null;
    if (source) source.close();
    source = null;
    if (pending) pending.abort();
    pending = null;
    readout.innerHTML = staleHtml;
  }

  function fallback() {
    if (document.hidden || pending) return;
    var controller = new AbortController();
    pending = controller;
    var timeout = setTimeout(function () { controller.abort(); }, 1000);
    fetch('/ui/ft/reading', { signal: controller.signal, cache: 'no-store' })
      .then(function (response) {
        if (!response.ok) throw new Error('Force read failed');
        return response.text();
      })
      .then(function (html) {
        if (!source && pending === controller && !document.hidden) {
          readout.innerHTML = html;
          lastMessage = performance.now();
        }
      })
      .catch(function () {
        if (!source && pending === controller) readout.innerHTML = staleHtml;
      })
      .finally(function () {
        clearTimeout(timeout);
        if (pending === controller) pending = null;
      });
  }

  function reconnect() {
    stop();
    if (document.hidden) return;
    fallback();
    retry = setTimeout(connect, 2000);
  }

  function connect() {
    if (document.hidden || source) return;
    clearTimeout(retry);
    retry = null;
    if (!window.EventSource) {
      fallback();
      retry = setTimeout(connect, 2000);
      return;
    }
    lastMessage = performance.now();
    source = new EventSource('/ui/ft/stream');
    var current = source;
    current.onmessage = function (event) {
      if (source !== current) return;
      try {
        var message = JSON.parse(event.data);
        if (typeof message.html !== 'string') throw new Error('Invalid force data');
        readout.innerHTML = message.html;
        lastMessage = performance.now();
      } catch (error) {
        reconnect();
      }
    };
    current.onerror = function () {
      if (source === current) reconnect();
    };
  }

  setInterval(function () {
    if (document.hidden || performance.now() - lastMessage <= 1000) return;
    if (source) reconnect();
    else readout.innerHTML = staleHtml;
  }, 500);
  document.addEventListener('visibilitychange', function () {
    if (document.hidden) stop();
    else connect();
  });
  window.addEventListener('pagehide', stop);
  window.addEventListener('pageshow', connect);
  connect();
})();