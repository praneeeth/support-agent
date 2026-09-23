/* Support widget. One script tag, no dependencies, no cookies.
   Session id lives in sessionStorage so a refresh keeps the conversation. */
(function () {
  "use strict";
  var BRAND = "__BRAND__";
  var GREETING = "__GREETING__";
  var base = (document.currentScript && document.currentScript.src || "").replace(/\/chat\/widget\.js.*$/, "");
  var KEY = "sa_session";
  var sid;
  try {
    sid = sessionStorage.getItem(KEY);
    if (!sid) { sid = "s" + Math.random().toString(36).slice(2) + Date.now().toString(36); sessionStorage.setItem(KEY, sid); }
  } catch (e) { sid = "s" + Math.random().toString(36).slice(2) + Date.now().toString(36); }

  var css = "" +
    ".sa-btn{position:fixed;right:20px;bottom:20px;width:56px;height:56px;border-radius:50%;border:0;background:#0f766e;color:#fff;font-size:24px;cursor:pointer;box-shadow:0 6px 20px rgba(0,0,0,.18);z-index:2147483000}" +
    ".sa-panel{position:fixed;right:20px;bottom:88px;width:360px;max-width:calc(100vw - 40px);height:520px;max-height:calc(100vh - 120px);background:#fff;border-radius:14px;box-shadow:0 12px 40px rgba(0,0,0,.22);display:none;flex-direction:column;overflow:hidden;z-index:2147483000;font:15px/1.5 system-ui,sans-serif;color:#1c1917}" +
    ".sa-panel.open{display:flex}" +
    ".sa-head{padding:12px 16px;background:#0f766e;color:#fff;font-weight:600;display:flex;justify-content:space-between;align-items:center}" +
    ".sa-head button{background:transparent;border:0;color:#fff;font-size:20px;cursor:pointer;line-height:1}" +
    ".sa-log{flex:1;overflow-y:auto;padding:14px;background:#fafaf9}" +
    ".sa-msg{max-width:85%;margin:0 0 10px;padding:9px 12px;border-radius:12px;white-space:pre-wrap;word-wrap:break-word}" +
    ".sa-you{margin-left:auto;background:#0f766e;color:#fff;border-bottom-right-radius:4px}" +
    ".sa-bot{background:#fff;border:1px solid #e7e5e4;border-bottom-left-radius:4px}" +
    ".sa-hand{background:#fef3c7;border:1px solid #fde68a}" +
    ".sa-src{display:block;margin-top:6px;font-size:11px;color:#78716c}" +
    ".sa-form{display:flex;gap:8px;padding:10px;border-top:1px solid #e7e5e4;background:#fff}" +
    ".sa-form input{flex:1;padding:10px;border:1px solid #d6d3d1;border-radius:8px;font:inherit}" +
    ".sa-form button{padding:10px 14px;border:0;border-radius:8px;background:#0f766e;color:#fff;font:inherit;cursor:pointer}" +
    ".sa-form button[disabled]{opacity:.5;cursor:default}" +
    ".sa-dots{color:#78716c;font-size:13px;padding:0 4px 8px}";

  var style = document.createElement("style"); style.textContent = css; document.head.appendChild(style);

  var btn = document.createElement("button");
  btn.className = "sa-btn"; btn.setAttribute("aria-label", "Chat with us"); btn.textContent = "💬";

  var panel = document.createElement("div");
  panel.className = "sa-panel"; panel.setAttribute("role", "dialog"); panel.setAttribute("aria-label", BRAND + " chat");
  panel.innerHTML =
    '<div class="sa-head"><span>' + BRAND + '</span><button aria-label="Close">×</button></div>' +
    '<div class="sa-log" aria-live="polite"></div>' +
    '<form class="sa-form"><input placeholder="Ask a question…" autocomplete="off" aria-label="Your message"><button type="submit">Send</button></form>';

  document.body.appendChild(btn); document.body.appendChild(panel);

  var log = panel.querySelector(".sa-log");
  var form = panel.querySelector(".sa-form");
  var input = panel.querySelector("input");
  var send = panel.querySelector('button[type="submit"]');
  var greeted = false;

  function bubble(text, cls, sources) {
    var d = document.createElement("div");
    d.className = "sa-msg " + cls;
    d.textContent = text;
    if (sources && sources.length) {
      var s = document.createElement("span");
      s.className = "sa-src";
      s.textContent = "From: " + sources.map(function (p) { return p.replace(/\.md$/, "").replace(/[-_]/g, " "); }).join(", ");
      d.appendChild(s);
    }
    log.appendChild(d); log.scrollTop = log.scrollHeight;
    return d;
  }

  function toggle(open) {
    panel.classList.toggle("open", open);
    if (open) {
      if (!greeted) { bubble(GREETING, "sa-bot"); greeted = true; }
      input.focus();
    }
  }
  btn.addEventListener("click", function () { toggle(!panel.classList.contains("open")); });
  panel.querySelector(".sa-head button").addEventListener("click", function () { toggle(false); });

  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    var text = input.value.trim();
    if (!text) return;
    bubble(text, "sa-you");
    input.value = ""; send.disabled = true;
    var waiting = document.createElement("div");
    waiting.className = "sa-dots"; waiting.textContent = "Typing…";
    log.appendChild(waiting); log.scrollTop = log.scrollHeight;

    fetch(base + "/chat/message", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sid, text: text })
    })
      .then(function (r) { return r.ok ? r.json() : r.json().catch(function () { return {}; }).then(function (b) { throw new Error(b.detail || "error"); }); })
      .then(function (data) {
        waiting.remove();
        bubble(data.text, data.handed_off ? "sa-msg sa-hand" : "sa-bot", data.sources);
      })
      .catch(function (err) {
        waiting.remove();
        bubble(String(err.message || "").indexOf("slow down") > -1
          ? "That's a lot of questions at once — give me a moment."
          : "Sorry, I couldn't reach the assistant. Please try again in a moment.", "sa-bot");
      })
      .then(function () { send.disabled = false; input.focus(); });
  });
})();
