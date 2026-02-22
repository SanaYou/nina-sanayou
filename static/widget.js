(function () {
  // Auto-detect API URL from script src
  var scriptEl =
    document.currentScript ||
    (function () {
      var scripts = document.getElementsByTagName("script");
      return scripts[scripts.length - 1];
    })();
  var NINA_API = new URL(scriptEl.src).origin;

  // Load Google Fonts
  var fontLink = document.createElement("link");
  fontLink.rel = "stylesheet";
  fontLink.href =
    "https://fonts.googleapis.com/css2?family=Bellota:wght@400;700&family=Raleway:wght@400;500;600&display=swap";
  document.head.appendChild(fontLink);

  // Inject styles
  var css = [
    "#nina-btn{position:fixed;bottom:24px;right:24px;width:56px;height:56px;border-radius:50%;background:#66B0B2;border:none;cursor:pointer;box-shadow:0 4px 16px rgba(102,176,178,0.4);display:flex;align-items:center;justify-content:center;z-index:99999;transition:transform .2s,box-shadow .2s;}",
    "#nina-btn:hover{transform:scale(1.06);box-shadow:0 6px 20px rgba(102,176,178,0.5);}",
    "#nina-win{position:fixed;bottom:90px;right:24px;width:360px;height:540px;background:#fff;border-radius:16px;box-shadow:0 8px 40px rgba(0,0,0,0.14);z-index:99998;display:flex;flex-direction:column;overflow:hidden;font-family:'Raleway',sans-serif;transform:scale(0.94) translateY(12px);opacity:0;pointer-events:none;transition:transform .25s ease,opacity .25s ease;}",
    "#nina-win.open{transform:scale(1) translateY(0);opacity:1;pointer-events:all;}",
    ".n-hdr{background:#66B0B2;padding:16px 20px;color:#fff;display:flex;align-items:center;gap:12px;}",
    ".n-hdr-icon{width:36px;height:36px;background:rgba(255,255,255,0.2);border-radius:50%;display:flex;align-items:center;justify-content:center;flex-shrink:0;}",
    ".n-hdr-name{font-family:'Bellota',sans-serif;font-size:17px;font-weight:700;margin:0;line-height:1.2;}",
    ".n-hdr-sub{font-size:11px;opacity:.85;margin:2px 0 0;}",
    ".n-msgs{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:10px;}",
    ".n-msgs::-webkit-scrollbar{width:4px;} .n-msgs::-webkit-scrollbar-thumb{background:#ddd;border-radius:4px;}",
    ".n-msg{max-width:86%;padding:10px 14px;border-radius:14px;font-size:13.5px;line-height:1.55;color:#7A7A7A;word-break:break-word;}",
    ".n-msg.bot{background:#f0f9f9;border-bottom-left-radius:3px;align-self:flex-start;}",
    ".n-msg.user{background:#66B0B2;color:#fff;border-bottom-right-radius:3px;align-self:flex-end;}",
    ".n-msg a{color:#66B0B2;} .n-msg.user a{color:#fff;}",
    ".n-typing{display:flex;gap:5px;padding:12px 14px;background:#f0f9f9;border-radius:14px;border-bottom-left-radius:3px;align-self:flex-start;}",
    ".n-typing span{width:7px;height:7px;background:#66B0B2;border-radius:50%;animation:nbounce 1.2s infinite;}",
    ".n-typing span:nth-child(2){animation-delay:.2s;} .n-typing span:nth-child(3){animation-delay:.4s;}",
    "@keyframes nbounce{0%,80%,100%{transform:translateY(0)}40%{transform:translateY(-6px)}}",
    ".n-foot{padding:10px 14px;border-top:1px solid #f2f2f2;display:flex;gap:8px;align-items:flex-end;}",
    ".n-inp{flex:1;border:1.5px solid #e8e8e8;border-radius:20px;padding:9px 15px;font-family:'Raleway',sans-serif;font-size:13.5px;color:#7A7A7A;resize:none;outline:none;max-height:80px;overflow-y:auto;line-height:1.45;background:#fff;transition:border-color .2s;}",
    ".n-inp:focus{border-color:#66B0B2;}",
    ".n-send{width:36px;height:36px;background:#66B0B2;border:none;border-radius:50%;cursor:pointer;display:flex;align-items:center;justify-content:center;flex-shrink:0;transition:background .2s;}",
    ".n-send:hover{background:#5a9ea0;} .n-send:disabled{background:#ccc;cursor:not-allowed;}",
    ".n-credit{text-align:center;font-size:10.5px;color:#c8c8c8;padding:5px;font-family:'Raleway',sans-serif;}",
  ].join("");

  var styleEl = document.createElement("style");
  styleEl.textContent = css;
  document.head.appendChild(styleEl);

  // State
  var isOpen = false;
  var history = [];
  var isTyping = false;

  // Chat button
  var btn = document.createElement("button");
  btn.id = "nina-btn";
  btn.setAttribute("aria-label", "Chat met Nina");
  btn.innerHTML =
    '<svg width="22" height="22" viewBox="0 0 24 24" fill="none"><path d="M20 2H4C2.9 2 2 2.9 2 4V22L6 18H20C21.1 18 22 17.1 22 16V4C22 2.9 21.1 2 20 2Z" fill="white"/></svg>';

  // Chat window
  var win = document.createElement("div");
  win.id = "nina-win";
  win.innerHTML =
    '<div class="n-hdr">' +
    '<div class="n-hdr-icon"><svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M20 2H4C2.9 2 2 2.9 2 4V22L6 18H20C21.1 18 22 17.1 22 16V4C22 2.9 21.1 2 20 2Z" fill="white"/></svg></div>' +
    '<div><p class="n-hdr-name">Nina</p><p class="n-hdr-sub">Digitale assistent van SanaYou YOGAcademy</p></div>' +
    "</div>" +
    '<div class="n-msgs" id="nina-msgs"></div>' +
    '<div class="n-foot">' +
    '<textarea class="n-inp" id="nina-inp" placeholder="Stel je vraag..." rows="1"></textarea>' +
    '<button class="n-send" id="nina-send"><svg width="15" height="15" viewBox="0 0 24 24" fill="none"><path d="M2 21L23 12L2 3V10L17 12L2 14V21Z" fill="white"/></svg></button>' +
    "</div>" +
    '<div class="n-credit">Mogelijk gemaakt door AI</div>';

  document.body.appendChild(btn);
  document.body.appendChild(win);

  var msgsEl = document.getElementById("nina-msgs");
  var inpEl = document.getElementById("nina-inp");
  var sendEl = document.getElementById("nina-send");

  function formatText(text) {
    return text
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
      .replace(
        /https?:\/\/[^\s<>"]+/g,
        function (url) {
          return '<a href="' + url + '" target="_blank" rel="noopener">' + url + "</a>";
        }
      )
      .replace(/\n/g, "<br>");
  }

  function addMsg(role, text) {
    var el = document.createElement("div");
    el.className = "n-msg " + role;
    el.innerHTML = role === "bot" ? formatText(text) : escapeHtml(text);
    msgsEl.appendChild(el);
    msgsEl.scrollTop = msgsEl.scrollHeight;
  }

  function escapeHtml(t) {
    return t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/\n/g, "<br>");
  }

  function showTyping() {
    var el = document.createElement("div");
    el.className = "n-typing";
    el.id = "nina-typing";
    el.innerHTML = "<span></span><span></span><span></span>";
    msgsEl.appendChild(el);
    msgsEl.scrollTop = msgsEl.scrollHeight;
  }

  function hideTyping() {
    var el = document.getElementById("nina-typing");
    if (el) el.remove();
  }

  async function send() {
    var text = inpEl.value.trim();
    if (!text || isTyping) return;

    inpEl.value = "";
    inpEl.style.height = "auto";
    addMsg("user", text);

    isTyping = true;
    sendEl.disabled = true;
    showTyping();

    try {
      var res = await fetch(NINA_API + "/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, history: history }),
      });
      var data = await res.json();
      hideTyping();
      addMsg("bot", data.response);
      history.push({ role: "user", content: text });
      history.push({ role: "assistant", content: data.response });
      if (history.length > 20) history = history.slice(-20);
    } catch (e) {
      hideTyping();
      addMsg(
        "bot",
        "Er ging iets mis. Probeer het opnieuw of stuur een mail naar academy@sanayou.com"
      );
    }

    isTyping = false;
    sendEl.disabled = false;
    inpEl.focus();
  }

  btn.addEventListener("click", function () {
    isOpen = !isOpen;
    win.classList.toggle("open", isOpen);
    if (isOpen && msgsEl.children.length === 0) {
      addMsg(
        "bot",
        "Hoi! Ik ben Nina, de digitale assistent van SanaYou YOGAcademy.\n\nHeb je een vraag over een opleiding, planning of iets technisch? Stel je vraag gerust — ik help je graag verder."
      );
      inpEl.focus();
    }
  });

  sendEl.addEventListener("click", send);

  inpEl.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  });

  inpEl.addEventListener("input", function () {
    inpEl.style.height = "auto";
    inpEl.style.height = Math.min(inpEl.scrollHeight, 80) + "px";
  });
})();
