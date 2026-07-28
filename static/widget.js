(function () {
  var script = document.currentScript;
  var publicKey = script.getAttribute("data-key");
  if (!publicKey) return;

  var origin = new URL(script.src).origin;
  var storageKey = "inbox_visitor_" + publicKey;
  var visitorId = localStorage.getItem(storageKey);
  if (!visitorId) {
    visitorId = crypto.randomUUID();
    localStorage.setItem(storageKey, visitorId);
  }

  var launcher = document.createElement("button");
  launcher.setAttribute("aria-label", "Open chat");
  launcher.style.cssText =
    "position:fixed;bottom:20px;right:20px;width:56px;height:56px;border-radius:28px;" +
    "background:#111;color:#fff;border:none;font-size:24px;cursor:pointer;z-index:2147483000;" +
    "box-shadow:0 4px 12px rgba(0,0,0,.2);";
  launcher.innerHTML = "&#128172;";

  var badge = document.createElement("span");
  badge.style.cssText =
    "position:fixed;bottom:60px;right:14px;background:#e11;color:#fff;border-radius:10px;" +
    "font-size:11px;padding:1px 6px;display:none;z-index:2147483001;";
  document.body.appendChild(badge);

  var iframe = document.createElement("iframe");
  iframe.src = origin + "/widget?key=" + encodeURIComponent(publicKey) + "&v=" + encodeURIComponent(visitorId);
  iframe.style.cssText =
    "position:fixed;bottom:88px;right:20px;width:360px;height:520px;border:none;border-radius:12px;" +
    "box-shadow:0 8px 30px rgba(0,0,0,.25);z-index:2147483000;display:none;background:#fff;";
  document.body.appendChild(iframe);
  document.body.appendChild(launcher);

  var open = false;
  function setOpen(v) {
    open = v;
    iframe.style.display = open ? "block" : "none";
    if (open) {
      badge.style.display = "none";
      iframe.contentWindow.postMessage({ type: "widget.opened" }, "*");
    }
  }

  launcher.addEventListener("click", function () {
    setOpen(!open);
  });

  window.addEventListener("message", function (event) {
    if (event.source !== iframe.contentWindow) return;
    var data = event.data || {};
    if (data.type === "widget.close") setOpen(false);
    if (data.type === "widget.unread") {
      var count = data.count || 0;
      if (count > 0 && !open) {
        badge.textContent = count;
        badge.style.display = "block";
      } else {
        badge.style.display = "none";
      }
    }
  });
})();
