/* Support widget. One script tag, no dependencies, no cookies, no build step.

   The server sends a reply as a list of blocks — text the model wrote, plus cards built in code
   from tool results. This file renders those blocks. A card is never parsed out of prose, so
   what a card shows is exactly what the order or product data said.

   Session id lives in sessionStorage so a refresh keeps the conversation. */
(function () {
  "use strict";

  var CFG = __CONFIG__;
  var base = (document.currentScript && document.currentScript.src || "").replace(/\/chat\/widget\.js.*$/, "");
  var KEY = "sa_session";
  var sid;
  try {
    sid = sessionStorage.getItem(KEY);
    if (!sid) { sid = "s" + Math.random().toString(36).slice(2) + Date.now().toString(36); sessionStorage.setItem(KEY, sid); }
  } catch (e) { sid = "s" + Math.random().toString(36).slice(2) + Date.now().toString(36); }

  /* ---------- theme ---------- */

  // Readable text on the brand colour, whatever the client picks.
  function inkFor(hex) {
    var m = /^#?([0-9a-f]{6})$/i.exec(hex || "");
    if (!m) return "#ffffff";
    var n = parseInt(m[1], 16);
    var r = (n >> 16) / 255, g = ((n >> 8) & 255) / 255, b = (n & 255) / 255;
    var f = function (c) { return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b) > 0.45 ? "#10221f" : "#ffffff";
  }

  var side = CFG.position === "left" ? "left" : "right";
  var light =
    "--sa-bg:#f7f7f6;--sa-surface:#ffffff;--sa-ink:#1c1917;--sa-muted:#78716c;" +
    "--sa-line:#e7e5e4;--sa-chip:#ffffff;--sa-shadow:rgba(23,23,23,.16);--sa-note:#fffbeb;--sa-note-line:#fde68a;";
  var dark =
    "--sa-bg:#1a1817;--sa-surface:#262322;--sa-ink:#f5f5f4;--sa-muted:#a8a29e;" +
    "--sa-line:#3b3735;--sa-chip:#2f2b29;--sa-shadow:rgba(0,0,0,.5);--sa-note:#3a2f14;--sa-note-line:#6b5420;";

  var css =
    ".sa-root{" + light + "--sa-accent:" + CFG.accent + ";--sa-on-accent:" + inkFor(CFG.accent) + ";" +
      "font:15px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif}" +
    (CFG.theme === "dark" ? ".sa-root{" + dark + "}" : "") +
    (CFG.theme === "auto" ? "@media (prefers-color-scheme:dark){.sa-root{" + dark + "}}" : "") +

    ".sa-btn{position:fixed;" + side + ":20px;bottom:20px;width:58px;height:58px;border-radius:50%;border:0;" +
      "background:var(--sa-accent);color:var(--sa-on-accent);cursor:pointer;display:flex;align-items:center;" +
      "justify-content:center;box-shadow:0 8px 24px var(--sa-shadow);z-index:2147483000;transition:transform .15s ease}" +
    ".sa-btn:hover{transform:scale(1.06)}" +
    ".sa-btn svg{width:26px;height:26px}" +
    ".sa-btn img{width:32px;height:32px;border-radius:50%;object-fit:cover}" +
    ".sa-badge{position:absolute;top:-2px;" + side + ":-2px;min-width:20px;height:20px;border-radius:10px;" +
      "background:#dc2626;color:#fff;font-size:11px;font-weight:700;display:none;align-items:center;justify-content:center;padding:0 5px}" +
    ".sa-badge.on{display:flex}" +

    ".sa-panel{position:fixed;" + side + ":20px;bottom:92px;width:380px;max-width:calc(100vw - 32px);" +
      "height:600px;max-height:calc(100vh - 128px);background:var(--sa-bg);color:var(--sa-ink);" +
      "border-radius:16px;box-shadow:0 20px 60px var(--sa-shadow);display:none;flex-direction:column;" +
      "overflow:hidden;z-index:2147483000;opacity:0;transform:translateY(8px);transition:opacity .16s ease,transform .16s ease}" +
    ".sa-panel.open{display:flex}" +
    ".sa-panel.shown{opacity:1;transform:none}" +
    // Full screen on a phone. Pinning all four edges avoids 100vw, which overshoots on mobile
    // browsers, and the launcher gets out of the way while the panel is up.
    "@media (max-width:480px){.sa-panel{left:0;right:0;top:0;bottom:0;width:auto;max-width:none;" +
      "height:auto;max-height:none;border-radius:0}.sa-root.open .sa-btn{display:none}}" +

    ".sa-head{display:flex;align-items:center;gap:10px;padding:13px 14px;background:var(--sa-accent);color:var(--sa-on-accent)}" +
    ".sa-avatar{width:34px;height:34px;border-radius:50%;background:rgba(255,255,255,.22);display:flex;align-items:center;" +
      "justify-content:center;font-weight:700;font-size:15px;flex:0 0 auto;overflow:hidden}" +
    ".sa-avatar img{width:100%;height:100%;object-fit:cover}" +
    ".sa-title{flex:1;min-width:0}" +
    ".sa-title b{display:block;font-size:15px;line-height:1.3}" +
    ".sa-title span{display:block;font-size:12px;opacity:.85;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}" +
    ".sa-x{background:transparent;border:0;color:inherit;font-size:22px;line-height:1;cursor:pointer;opacity:.85;padding:2px 4px}" +
    ".sa-x:hover{opacity:1}" +

    ".sa-log{flex:1;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:2px}" +
    // Without this, a tall card is squeezed to nothing once the log overflows.
    ".sa-log>*{flex:0 0 auto}" +
    ".sa-row{display:flex;flex-direction:column;margin-bottom:8px;max-width:88%}" +
    ".sa-row.me{align-self:flex-end;align-items:flex-end}" +
    ".sa-msg{padding:9px 13px;border-radius:14px;white-space:pre-wrap;word-wrap:break-word;overflow-wrap:anywhere}" +
    ".sa-bot .sa-msg{background:var(--sa-surface);border:1px solid var(--sa-line);border-bottom-left-radius:5px}" +
    ".sa-me .sa-msg,.sa-row.me .sa-msg{background:var(--sa-accent);color:var(--sa-on-accent);border-bottom-right-radius:5px}" +
    ".sa-note .sa-msg{background:var(--sa-note);border:1px solid var(--sa-note-line);border-bottom-left-radius:5px}" +
    ".sa-time{font-size:11px;color:var(--sa-muted);margin:3px 4px 0}" +
    ".sa-src{display:block;margin-top:7px;padding-top:6px;border-top:1px dashed var(--sa-line);font-size:11px;color:var(--sa-muted)}" +

    ".sa-hand{align-self:center;max-width:100%;display:flex;align-items:center;gap:8px;margin:4px 0 10px;padding:8px 12px;" +
      "border-radius:10px;background:var(--sa-note);border:1px solid var(--sa-note-line);font-size:12.5px}" +
    ".sa-hand svg{width:15px;height:15px;flex:0 0 auto}" +

    ".sa-card{background:var(--sa-surface);border:1px solid var(--sa-line);border-radius:14px;overflow:hidden;" +
      "margin:0 0 9px;max-width:88%;box-shadow:0 1px 3px rgba(0,0,0,.04)}" +
    ".sa-card-top{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:11px 13px;border-bottom:1px solid var(--sa-line)}" +
    ".sa-card-top b{font-size:14px;letter-spacing:.2px}" +
    ".sa-pill{font-size:11px;font-weight:700;padding:3px 9px;border-radius:999px;background:var(--sa-accent);color:var(--sa-on-accent);white-space:nowrap}" +
    ".sa-pill.grey{background:var(--sa-chip);color:var(--sa-muted);border:1px solid var(--sa-line)}" +
    ".sa-card-body{padding:11px 13px}" +

    ".sa-steps{list-style:none;margin:0 0 4px;padding:0}" +
    ".sa-step{display:flex;gap:10px;align-items:flex-start;font-size:13px;position:relative;padding-bottom:12px}" +
    ".sa-step:last-child{padding-bottom:0}" +
    ".sa-dot{width:13px;height:13px;border-radius:50%;flex:0 0 auto;margin-top:3px;border:2px solid var(--sa-line);background:var(--sa-surface);position:relative;z-index:1}" +
    ".sa-step.done .sa-dot{background:var(--sa-accent);border-color:var(--sa-accent)}" +
    ".sa-step.current .sa-dot{border-color:var(--sa-accent);box-shadow:0 0 0 4px color-mix(in srgb,var(--sa-accent) 22%,transparent)}" +
    ".sa-step:not(:last-child)::before{content:'';position:absolute;left:6px;top:14px;bottom:0;width:2px;background:var(--sa-line)}" +
    ".sa-step.done:not(:last-child)::before{background:var(--sa-accent)}" +
    ".sa-step.pending .sa-label{color:var(--sa-muted)}" +
    ".sa-step.current .sa-label{font-weight:650}" +

    ".sa-kv{display:flex;justify-content:space-between;gap:12px;font-size:12.5px;padding:5px 0;border-top:1px solid var(--sa-line)}" +
    ".sa-kv span:first-child{color:var(--sa-muted)}" +
    ".sa-kv span:last-child{text-align:right}" +
    ".sa-items{margin:2px 0 6px;font-size:12.5px;color:var(--sa-muted)}" +
    ".sa-track{display:inline-flex;align-items:center;gap:6px;margin-top:10px;padding:8px 13px;border-radius:9px;" +
      "background:var(--sa-accent);color:var(--sa-on-accent);text-decoration:none;font-size:13px;font-weight:600}" +
    ".sa-price{font-size:19px;font-weight:700;margin:2px 0 4px}" +
    ".sa-desc{font-size:12.5px;color:var(--sa-muted);margin:0}" +

    ".sa-chips{display:flex;flex-wrap:wrap;gap:7px;margin:2px 0 10px}" +
    ".sa-chip{padding:7px 13px;border-radius:999px;border:1px solid var(--sa-accent);background:transparent;" +
      "color:var(--sa-accent);font:inherit;font-size:13px;cursor:pointer;line-height:1.3}" +
    ".sa-chip:hover{background:var(--sa-accent);color:var(--sa-on-accent)}" +

    ".sa-typing{display:flex;gap:4px;padding:11px 13px;width:fit-content;background:var(--sa-surface);" +
      "border:1px solid var(--sa-line);border-radius:14px;border-bottom-left-radius:5px;margin-bottom:9px}" +
    ".sa-typing i{width:7px;height:7px;border-radius:50%;background:var(--sa-muted);animation:sa-b 1.2s infinite}" +
    ".sa-typing i:nth-child(2){animation-delay:.18s}.sa-typing i:nth-child(3){animation-delay:.36s}" +
    "@keyframes sa-b{0%,60%,100%{opacity:.25;transform:translateY(0)}30%{opacity:1;transform:translateY(-3px)}}" +

    ".sa-form{display:flex;gap:8px;padding:11px;border-top:1px solid var(--sa-line);background:var(--sa-surface)}" +
    ".sa-form input{flex:1;min-width:0;padding:11px 13px;border:1px solid var(--sa-line);border-radius:10px;" +
      "font:inherit;background:var(--sa-bg);color:var(--sa-ink)}" +
    ".sa-form input:focus{outline:2px solid var(--sa-accent);outline-offset:-1px}" +
    ".sa-form button{width:42px;height:42px;flex:0 0 auto;border:0;border-radius:10px;background:var(--sa-accent);" +
      "color:var(--sa-on-accent);cursor:pointer;display:flex;align-items:center;justify-content:center}" +
    ".sa-form button[disabled]{opacity:.45;cursor:default}" +
    ".sa-form svg{width:19px;height:19px}" +
    ".sa-foot{text-align:center;font-size:10.5px;color:var(--sa-muted);padding:0 0 9px;background:var(--sa-surface)}";

  var style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);

  /* ---------- shell ---------- */

  var root = document.createElement("div");
  root.className = "sa-root";
  document.body.appendChild(root);

  var CHAT_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 8.8 8.8 0 0 1-3.9-.9L3 20.5l1.6-4.8A8.4 8.4 0 0 1 12 3.1a8.4 8.4 0 0 1 9 8.4z"/></svg>';
  var SEND_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2 11 13"/><path d="m22 2-7 20-4-9-9-4 20-7z"/></svg>';
  var PERSON_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>';

  var btn = document.createElement("button");
  btn.className = "sa-btn";
  btn.setAttribute("aria-label", "Chat with " + CFG.brand);
  btn.innerHTML = (CFG.logo ? '<img src="' + CFG.logo + '" alt="">' : CHAT_ICON) +
    '<span class="sa-badge" aria-hidden="true">0</span>';
  var badge = btn.querySelector(".sa-badge");

  var panel = document.createElement("div");
  panel.className = "sa-panel";
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-label", CFG.brand + " chat");
  panel.innerHTML =
    '<div class="sa-head">' +
      '<div class="sa-avatar">' + (CFG.logo ? '<img src="' + CFG.logo + '" alt="">' : esc(CFG.brand.charAt(0).toUpperCase())) + '</div>' +
      '<div class="sa-title"><b></b><span></span></div>' +
      '<button class="sa-x" aria-label="Close chat">×</button>' +
    '</div>' +
    '<div class="sa-log" aria-live="polite"></div>' +
    '<form class="sa-form"><input placeholder="Ask a question…" autocomplete="off" aria-label="Your message">' +
      '<button type="submit" aria-label="Send">' + SEND_ICON + '</button></form>' +
    '<div class="sa-foot">Answers come from ' + esc(CFG.brand) + '’s own policies</div>';

  panel.querySelector(".sa-title b").textContent = CFG.brand;
  panel.querySelector(".sa-title span").textContent = CFG.tagline || "";

  root.appendChild(btn);
  root.appendChild(panel);

  var log = panel.querySelector(".sa-log");
  var form = panel.querySelector(".sa-form");
  var input = panel.querySelector("input");
  var send = panel.querySelector('button[type="submit"]');
  var greeted = false;
  var unread = 0;

  function esc(s) {
    return String(s === undefined || s === null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function clock() {
    var d = new Date();
    return ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2);
  }

  function place(el) {
    log.appendChild(el);
    log.scrollTop = log.scrollHeight;
    return el;
  }

  /* ---------- rendering ---------- */

  function textRow(text, who, sources) {
    var row = document.createElement("div");
    row.className = "sa-row " + (who === "me" ? "me sa-me" : who === "note" ? "sa-note" : "sa-bot");
    var msg = document.createElement("div");
    msg.className = "sa-msg";
    msg.textContent = text;
    if (sources && sources.length) {
      var s = document.createElement("span");
      s.className = "sa-src";
      s.textContent = "From: " + sources.map(function (p) {
        return String(p).replace(/\.md$/, "").replace(/[-_]/g, " ");
      }).join(", ");
      msg.appendChild(s);
    }
    row.appendChild(msg);
    var t = document.createElement("div");
    t.className = "sa-time";
    t.textContent = clock();
    row.appendChild(t);
    return place(row);
  }

  function handoffRow(text) {
    var d = document.createElement("div");
    d.className = "sa-hand";
    d.innerHTML = PERSON_ICON + "<span></span>";
    d.querySelector("span").textContent = text;
    return place(d);
  }

  function orderCard(b) {
    var card = document.createElement("div");
    card.className = "sa-card";
    var steps = (b.steps || []).map(function (s) {
      return '<li class="sa-step ' + esc(s.state) + '"><span class="sa-dot"></span>' +
        '<span class="sa-label">' + esc(s.label) + "</span></li>";
    }).join("");
    var rows = "";
    if (b.placed_on) rows += kv("Ordered", b.placed_on);
    if (b.eta) rows += kv("Arriving by", b.eta);
    if (b.carrier) rows += kv("Carrier", b.carrier + (b.tracking_number ? " · " + b.tracking_number : ""));
    card.innerHTML =
      '<div class="sa-card-top"><b>' + esc(b.number) + "</b>" +
        '<span class="sa-pill' + (b.steps && b.steps.length ? "" : " grey") + '">' + esc(b.status_label) + "</span></div>" +
      '<div class="sa-card-body">' +
        (steps ? '<ul class="sa-steps">' + steps + "</ul>" : "") +
        (b.items && b.items.length ? '<p class="sa-items">' + esc(b.items.map(function (i) {
          return i.quantity + " × " + i.name;
        }).join(", ")) + "</p>" : "") +
        rows +
        (b.tracking_url ? '<a class="sa-track" href="' + esc(b.tracking_url) + '" target="_blank" rel="noopener noreferrer">Track package</a>' : "") +
      "</div>";
    return place(card);
  }

  function kv(k, v) {
    return '<div class="sa-kv"><span>' + esc(k) + "</span><span>" + esc(v) + "</span></div>";
  }

  function productCard(b) {
    var card = document.createElement("div");
    card.className = "sa-card";
    card.innerHTML =
      '<div class="sa-card-top"><b>' + esc(b.name) + "</b>" +
        '<span class="sa-pill' + (b.in_stock ? "" : " grey") + '">' + esc(b.stock_label) + "</span></div>" +
      '<div class="sa-card-body">' +
        '<div class="sa-price">' + esc(b.price_label) + "</div>" +
        '<p class="sa-desc">' + esc(b.description) + "</p>" +
        kv("SKU", b.sku) + kv("Category", b.category) +
      "</div>";
    return place(card);
  }

  function chips(options) {
    var wrap = document.createElement("div");
    wrap.className = "sa-chips";
    options.forEach(function (option) {
      var c = document.createElement("button");
      c.type = "button";
      c.className = "sa-chip";
      c.textContent = option;
      c.addEventListener("click", function () {
        wrap.remove();
        ask(option);
      });
      wrap.appendChild(c);
    });
    return place(wrap);
  }

  function render(blocks, fallback, handed) {
    if (!blocks || !blocks.length) return textRow(fallback, handed ? "note" : "bot");
    var first = null;
    blocks.forEach(function (b) {
      var el = null;
      if (b.type === "order_card") el = orderCard(b);
      else if (b.type === "product_card") el = productCard(b);
      else if (b.type === "quick_replies") { if (b.options && b.options.length) el = chips(b.options); }
      else if (b.type === "text") {
        el = b.tone === "handoff" ? handoffRow(b.text)
          : textRow(b.text, b.tone === "notice" ? "note" : "bot", b.sources);
      }
      if (el && !first) first = el;
    });
    return first;
  }

  // Scrolling to the very bottom hides the top of a tall card. Show the reply from its start,
  // unless it already fits, in which case the plain scroll-to-bottom is right.
  function reveal(el) {
    if (!el) return;
    var top = el.offsetTop - log.offsetTop - 8;
    if (log.scrollHeight - top > log.clientHeight) log.scrollTop = top;
  }

  /* ---------- conversation ---------- */

  function toggle(open) {
    panel.classList.toggle("open", open);
    root.classList.toggle("open", open);
    if (open) {
      requestAnimationFrame(function () { panel.classList.add("shown"); });
      unread = 0; badge.classList.remove("on");
      if (!greeted) {
        greeted = true;
        textRow(CFG.greeting, "bot");
        if (CFG.suggestions && CFG.suggestions.length) chips(CFG.suggestions);
      }
      input.focus();
    } else {
      panel.classList.remove("shown");
    }
  }

  btn.addEventListener("click", function () { toggle(!panel.classList.contains("open")); });
  panel.querySelector(".sa-x").addEventListener("click", function () { toggle(false); });
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape" && panel.classList.contains("open")) toggle(false);
  });

  function ask(text) {
    textRow(text, "me");
    send.disabled = true;
    var waiting = place(Object.assign(document.createElement("div"), {
      className: "sa-typing", innerHTML: "<i></i><i></i><i></i>"
    }));

    fetch(base + "/chat/message", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sid, text: text })
    })
      .then(function (r) {
        if (r.ok) return r.json();
        return r.json().catch(function () { return {}; }).then(function (b) { throw new Error(b.detail || "error"); });
      })
      .then(function (data) {
        waiting.remove();
        reveal(render(data.blocks, data.text, data.handed_off));
        if (!panel.classList.contains("open")) { unread += 1; badge.textContent = unread; badge.classList.add("on"); }
      })
      .catch(function (err) {
        waiting.remove();
        textRow(String(err.message || "").indexOf("slow down") > -1
          ? "That’s a lot of questions at once — give me a moment."
          : "Sorry, I couldn’t reach the assistant. Please try again in a moment.", "note");
      })
      .then(function () { send.disabled = false; input.focus(); });
  }

  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    var text = input.value.trim();
    if (!text) return;
    input.value = "";
    ask(text);
  });
})();
