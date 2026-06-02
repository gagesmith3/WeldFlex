(function () {
  if (window.htmx) {
    return;
  }

  function setHtml(targetSelector, html) {
    var target = document.querySelector(targetSelector);
    if (target) {
      target.innerHTML = html;
    }
  }

  function postForm(form) {
    var action = form.getAttribute("hx-post");
    var target = form.getAttribute("hx-target");
    if (!action || !target) {
      return;
    }

    var body = new URLSearchParams(new FormData(form));
    fetch(action, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: body,
    })
      .then(function (res) { return res.text(); })
      .then(function (html) { setHtml(target, html); })
      .catch(function (err) { setHtml(target, "<div class='result error'><pre>" + err + "</pre></div>"); });
  }

  function postButton(button) {
    var action = button.getAttribute("hx-post");
    var target = button.getAttribute("hx-target");
    if (!action || !target) {
      return;
    }

    fetch(action, { method: "POST" })
      .then(function (res) { return res.text(); })
      .then(function (html) { setHtml(target, html); })
      .catch(function (err) { setHtml(target, "<div class='result error'><pre>" + err + "</pre></div>"); });
  }

  function bindPseudoHtmx() {
    var forms = document.querySelectorAll("form[hx-post]");
    forms.forEach(function (form) {
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        postForm(form);
      });
    });

    var buttons = document.querySelectorAll("button[hx-post]");
    buttons.forEach(function (button) {
      button.addEventListener("click", function () {
        postButton(button);
      });
    });
  }

  function startStatusPolling() {
    var pollingNodes = document.querySelectorAll("[hx-get]");
    if (!pollingNodes.length) {
      return;
    }

    pollingNodes.forEach(function (node) {
      var action = node.getAttribute("hx-get");
      if (!action) {
        return;
      }

      var refresh = function () {
        fetch(action)
          .then(function (res) { return res.text(); })
          .then(function (html) { node.innerHTML = html; })
          .catch(function () {
            node.innerHTML = "<div class='status error'><p>Polling failed.</p></div>";
          });
      };

      refresh();
      window.setInterval(refresh, 1000);
    });
  }

  bindPseudoHtmx();
  startStatusPolling();
})();
