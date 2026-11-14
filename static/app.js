/* Zentrale – Kern: DOM-Helfer, Formatierung, Abrufe, Diagramme, Seitenwechsel.
   Grundsatz: Texte aus dem Netz (Marktfragen, Schlagzeilen, Fehlermeldungen) werden immer als Text eingefügt, nie als HTML. */
(function () {
  "use strict";
  var PB = window.PB = { pages: {} };
  var TZ = "Europe/Berlin", SVGNS = "http://www.w3.org/2000/svg";
  PB.CAT = { crypto: "Krypto", sports: "Sport", weather: "Wetter", esports: "E-Sport", politics: "Politik", world: "Welt",
             economy: "Wirtschaft", tech: "Technik", science: "Wissenschaft", culture: "Kultur" };
  PB.COL = { crypto: "var(--s-krypto)", sports: "var(--s-sport)", weather: "var(--s-wetter)", modell: "var(--s-modell)", markt: "var(--s-markt)" };
  PB.MODELED = ["crypto", "sports", "weather"];

  // ---------------------------------------------------------------- DOM
  function add(el, kids) {
    for (var i = 0; i < kids.length; i++) {
      var k = kids[i];
      if (k == null || k === false) continue;
      if (Array.isArray(k)) add(el, k);
      else el.appendChild(k.nodeType ? k : document.createTextNode(String(k)));
    }
    return el;
  }
  function make(ns, tag, attrs, kids) {
    var el = ns ? document.createElementNS(ns, tag) : document.createElement(tag);
    for (var k in attrs || {}) {
      var v = attrs[k];
      if (v == null || v === false) continue;
      if (k === "class") el.setAttribute("class", v);
      else if (k === "on") for (var ev in v) el.addEventListener(ev, v[ev]);
      else if (k === "style") for (var s in v) el.style.setProperty(s, v[s]);
      else if (k === "href" && !/^(#|\/|https?:\/\/)/.test(v)) continue;          // nur eigene Seiten und http(s)-Adressen
      else el.setAttribute(k, v === true ? "" : v);
    }
    return add(el, kids);
  }
  function h(tag, attrs) { return make(null, tag, attrs, Array.prototype.slice.call(arguments, 2)); }
  function s(tag, attrs) { return make(SVGNS, tag, attrs, Array.prototype.slice.call(arguments, 2)); }
  PB.h = h;

  // ---------------------------------------------------------------- Formatierung
  var F = PB.fmt = {};
  F.num = function (v, d) { return v == null || isNaN(v) ? null : Number(v).toLocaleString("de-DE", { minimumFractionDigits: d || 0, maximumFractionDigits: d || 0 }); };
  F.pct = function (v, d) { return v == null || isNaN(v) ? null : F.num(v * 100, d == null ? 0 : d) + " %"; };
  F.spct = function (v, d) { return v == null || isNaN(v) ? null : (v >= 0 ? "+" : "−") + F.num(Math.abs(v) * 100, d == null ? 1 : d) + " %"; };
  F.pts = function (v) { return v == null || isNaN(v) ? null : (v >= 0 ? "+" : "−") + F.num(Math.abs(v) * 100, 0) + " Pkt"; };
  F.usd = function (v) { return v == null || isNaN(v) ? null : F.num(v, 2) + " $"; };
  F.susd = function (v) { return v == null || isNaN(v) ? null : (v >= 0 ? "+" : "−") + F.num(Math.abs(v), 2) + " $"; };
  F.big = function (v) {
    if (v == null || isNaN(v)) return null;
    var a = Math.abs(v);
    return a >= 1e6 ? F.num(v / 1e6, 1) + " Mio." : a >= 1e4 ? F.num(v / 1e3, 0) + " Tsd." : F.num(v, 0);
  };
  F.z = function (v) { return v == null || isNaN(v) ? null : (v >= 0 ? "+" : "−") + F.num(Math.abs(v), 1); };
  F.dt = function (iso) {
    if (!iso) return null;
    var d = typeof iso === "number" ? new Date(iso * 1000) : new Date(iso);
    return isNaN(d) ? String(iso) : d.toLocaleString("de-DE", { timeZone: TZ, day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  };
  F.day = function (iso) { var d = new Date(iso); return isNaN(d) ? String(iso) : d.toLocaleDateString("de-DE", { timeZone: TZ, day: "2-digit", month: "2-digit" }); };
  F.dur = function (sec) {
    if (sec == null || isNaN(sec)) return null;
    sec = Math.max(0, Math.round(sec));
    if (sec < 90) return sec + " s";
    if (sec < 5400) return Math.round(sec / 60) + " min";
    if (sec < 172800) return F.num(sec / 3600, 1) + " h";
    return Math.round(sec / 86400) + " Tagen";
  };
  F.ago = function (iso) {
    if (!iso) return null;
    var t = typeof iso === "number" ? iso * 1000 : new Date(iso).getTime();
    return isNaN(t) ? null : "vor " + F.dur((Date.now() - t) / 1000);
  };
  F.until = function (iso) {
    if (!iso) return null;
    var sec = (new Date(iso).getTime() - Date.now()) / 1000;
    return sec < 0 ? "beendet" : "in " + F.dur(sec);
  };
  /* Wert oder ausdrücklich „keine Daten“ – nie eine erfundene Null. */
  PB.val = function (text, cls) { return text == null ? h("span", { class: "dim2" }, "keine Daten") : h("span", { class: cls || null }, text); };
  PB.cat = function (c) {
    return h("span", { class: "nowrap" }, PB.COL[c] ? h("span", { class: "sw", style: { background: PB.COL[c] } }) : null, PB.CAT[c] || c || "ohne Kategorie");
  };

  // ---------------------------------------------------------------- Bausteine
  PB.panel = function (title, hint, body, flush) {
    return h("section", { class: "panel" }, h("header", null, h("h2", null, title), hint ? h("span", { class: "hint" }, hint) : null),
             h("div", { class: "body" + (flush ? " flush" : "") }, body));
  };
  PB.empty = function (why, next) { return h("div", { class: "empty" }, h("b", null, "Keine Daten verfügbar"), why || "", next ? h("div", { class: "dim2" }, next) : null); };
  PB.chip = function (text, kind) { return h("span", { class: "chip" + (kind ? " " + kind : "") }, text); };
  PB.kv = function (rows, left) {
    return h("dl", { class: "kv" + (left ? " left" : "") }, rows.map(function (r) { return r ? [h("dt", null, r[0]), h("dd", null, r[1] == null ? PB.val(null) : r[1])] : null; }));
  };
  /* Tabelle. cols: {label, num, cls, get(row) -> Knoten|Text, sort} */
  PB.table = function (cols, rows, opt) {
    opt = opt || {};
    if (!rows || !rows.length) return PB.empty(opt.empty, opt.next);
    var head = h("tr", null, cols.map(function (c) {
      var cls = (c.num ? "num " : "") + (c.cls || "") + (opt.sort && opt.sort.key === c.sort ? " sorted" : "");
      if (c.sort && opt.onSort) {
        var arrow = opt.sort && opt.sort.key === c.sort ? (opt.sort.dir === "asc" ? " ▲" : " ▼") : "";
        return h("th", { class: cls }, h("button", { type: "button", on: { click: function () { opt.onSort(c.sort); } } }, c.label + arrow));
      }
      return h("th", { class: cls }, c.label);
    }));
    var body = rows.map(function (r) {
      var tr = h("tr", { class: opt.href ? "click" : null }, cols.map(function (c) {
        var v = c.get(r);
        return h("td", { class: (c.num ? "num " : "") + (c.cls || "") }, v == null ? PB.val(null) : v);
      }));
      if (opt.href) {
        tr.tabIndex = 0;
        var go = function () { location.hash = opt.href(r); };
        tr.addEventListener("click", function (e) { if (!e.target.closest("a,button")) go(); });
        tr.addEventListener("keydown", function (e) { if (e.key === "Enter") go(); });
      }
      return tr;
    });
    return h("div", { class: "tw" }, h("table", null, h("thead", null, head), h("tbody", null, body)));
  };
  PB.meter = function (share, text) {
    var k = share == null ? "" : share >= 0.9 ? " bad" : share >= 0.7 ? " warn" : "";
    return h("div", { class: "row", style: { "flex-wrap": "nowrap" } },
             h("div", { class: "meter" + k, style: { flex: "1" }, role: "img", "aria-label": text || "" }, h("i", { style: { width: Math.min(100, (share || 0) * 100) + "%" } })),
             text ? h("span", { class: "dim nowrap" }, text) : null);
  };
  /* Zwei Marken auf einer Leiste: Brier Modell (blau) und Markt (grau). Links = besser. */
  PB.pair = function (model, market, max) {
    if (model == null || market == null) return null;
    max = max || 0.5;
    var pos = function (v) { return Math.min(100, Math.max(0, v / max * 100)) + "%"; };
    return h("div", { class: "pair", role: "img", "aria-label": "Modell " + F.num(model, 3) + ", Markt " + F.num(market, 3) },
             h("i", { style: { left: pos(market) } }), h("i", { class: "m", style: { left: pos(model) } }));
  };
  /* Bot gegen Markt: Balken nach rechts = Modell genauer, nach links = Markt genauer. Hohl = statistisch nicht gesichert. */
  PB.duel = function (rows) {
    var max = 0.1;
    rows.forEach(function (r) { if (r.skill != null) max = Math.max(max, Math.min(1, Math.abs(r.skill))); });
    var out = h("div", { class: "duel" }, h("div", { class: "axis-l" }, h("span", null, "◄ Markt genauer"), h("span", null, "Modell genauer ►")));
    rows.forEach(function (r) {
      var sure = r.z != null && Math.abs(r.z) >= 1.64, w = r.skill == null ? 0 : Math.min(1, Math.abs(r.skill)) / max * 50;
      var left = r.skill != null && r.skill < 0;
      var verdict = r.skill == null ? "keine Daten" : (left ? "Markt genauer" : "Modell genauer") + (sure ? ", gesichert" : ", noch nicht gesichert");
      add(out, [
        h("div", { class: "name" }, r.label, r.sub ? h("small", null, r.sub) : null),
        h("div", { class: "track", role: "img", "aria-label": r.label + ": " + verdict },
          r.skill == null ? null : h("div", { class: "bar" + (left ? " left" : "") + (sure ? "" : " unsure"),
                                              style: left ? { right: "50%", width: w + "%" } : { left: "50%", width: w + "%" } })),
        h("div", { class: "read" },
          h("span", null, verdict + (r.skill == null ? "" : " (" + F.spct(r.skill, 0) + ")")),
          h("span", { class: "nowrap" }, r.n == null ? "" : F.num(r.n) + " Prognosen" + (r.brier != null ? " · Brier " + F.num(r.brier, 3) + " gegen " + F.num(r.brier_market, 3) : "")))
      ]);
    });
    return out;
  };
  PB.feed = function (rows, emptyText) {
    if (!rows || !rows.length) return PB.empty(emptyText || "Seit dem Start gab es keine Meldungen.");
    return h("ul", { class: "feed" }, rows.map(function (e) {
      return h("li", null, h("span", { class: "lvl " + e.level, title: e.level }),
               h("div", null, h("div", { class: "t" }, e.title + (e.count > 1 ? " (×" + e.count + ")" : "")), e.detail ? h("div", { class: "d" }, e.detail) : null),
               h("time", { title: F.dt(e.ts) }, F.ago(e.ts)));
    }));
  };

  // ---------------------------------------------------------------- Hinweis-Fähnchen
  var tip = null;
  function showTip(x, y, head, rows) {
    if (!tip) { tip = h("div", { class: "tip", role: "status" }); document.body.appendChild(tip); }
    tip.replaceChildren(head ? h("div", { class: "h" }, head) : "", h("div", null, rows.map(function (r) {
      return h("div", { class: "r" }, h("span", null, r.color ? h("i", { style: { "border-color": r.color, "border-top-style": r.dash ? "dashed" : "solid" } }) : null, r.name), h("b", null, r.value));
    })));
    tip.style.display = "block";
    var w = tip.offsetWidth, hh = tip.offsetHeight;
    tip.style.left = Math.max(6, Math.min(window.innerWidth - w - 6, x + 14)) + "px";
    tip.style.top = Math.max(6, y - hh - 12) + "px";
  }
  function hideTip() { if (tip) tip.style.display = "none"; }
  window.addEventListener("hashchange", hideTip);

  // ---------------------------------------------------------------- Liniendiagramm
  /* opt: { series:[{name,color,dash,dots,points:[[x,y],…]}], xType:"time"|"num"|"cat", cats:[…], yFmt, xFmt, yMin, yMax, height, mini, ref:[[x,y],[x,y]], table } */
  PB.line = function (opt) {
    var series = opt.series.filter(function (x) { return x.points && x.points.length; });
    if (!series.length) return PB.empty(opt.empty, opt.next);
    var H = opt.mini ? 64 : (opt.height || 220), L = opt.mini ? 2 : 44, R = opt.mini ? 6 : 14, T = 8, B = opt.mini ? 4 : 22;
    var xs = [], ys = [];
    series.forEach(function (se) { se.points.forEach(function (p) { xs.push(p[0]); ys.push(p[1]); }); });
    if (opt.ref) opt.ref.forEach(function (p) { xs.push(p[0]); ys.push(p[1]); });
    var x0 = Math.min.apply(null, xs), x1 = Math.max.apply(null, xs), y0 = opt.yMin != null ? opt.yMin : Math.min.apply(null, ys), y1 = opt.yMax != null ? opt.yMax : Math.max.apply(null, ys);
    if (x1 === x0) { x0 -= 1; x1 += 1; }
    if (y1 === y0) { y0 -= Math.abs(y0) * 0.1 || 0.1; y1 += Math.abs(y1) * 0.1 || 0.1; }
    if (opt.yMin == null && opt.yMax == null) { var pad = (y1 - y0) * 0.08; y0 -= pad; y1 += pad; }
    var py = function (y) { return T + (1 - (y - y0) / (y1 - y0)) * (H - T - B); };
    var yFmt = opt.yFmt || function (v) { return F.num(v, 2); };
    var xFmt = opt.xFmt || (opt.xType === "time" ? function (v) { return F.dt(new Date(v).toISOString()); } : opt.xType === "cat" ? function (v) { return opt.cats[v]; } : function (v) { return F.num(v, 2); });
    var uniq = Array.from(new Set(xs)).sort(function (a, b) { return a - b; });
    /* Gezeichnet wird in echten Bildpunkten der Kachel (W = ihre Breite) – so bleiben Schrift und Linien auf jedem Gerät gleich groß. */
    function build(W) {
    var px = function (x) { return L + (x - x0) / (x1 - x0) * (W - L - R); };
    var svg = s("svg", { viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": opt.label || "Diagramm" });
    if (!opt.mini) {
      for (var i = 0; i <= 3; i++) {
        var yv = y0 + (y1 - y0) * i / 3;
        add(svg, [s("line", { class: i ? "grid-l" : "axis", x1: L, x2: W - R, y1: py(yv), y2: py(yv) }),
                  s("text", { x: L - 6, y: py(yv) + 4, "text-anchor": "end" }, yFmt(yv))]);
      }
      [[x0, "start"], [(x0 + x1) / 2, "middle"], [x1, "end"]].forEach(function (t) {
        var v = opt.xType === "cat" ? Math.round(t[0]) : t[0];
        add(svg, [s("text", { x: px(t[0]), y: H - 5, "text-anchor": t[1] }, opt.xType === "time" ? F.day(new Date(v).toISOString()) : xFmt(v))]);
      });
    }
    if (opt.ref) svg.appendChild(s("line", { class: "axis", x1: px(opt.ref[0][0]), y1: py(opt.ref[0][1]), x2: px(opt.ref[1][0]), y2: py(opt.ref[1][1]) }));
    series.forEach(function (se) {
      if (!se.dotsOnly) {
        svg.appendChild(s("polyline", { fill: "none", stroke: se.color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round",
                                        "stroke-dasharray": se.dash ? "5 4" : null, points: se.points.map(function (p) { return px(p[0]).toFixed(1) + "," + py(p[1]).toFixed(1); }).join(" ") }));
      }
      var marks = se.dots || se.dotsOnly ? se.points : (opt.mini ? [] : [se.points[se.points.length - 1]]);
      marks.forEach(function (p) { svg.appendChild(s("circle", { cx: px(p[0]), cy: py(p[1]), r: 4, fill: se.color, stroke: "var(--panel)", "stroke-width": 2 })); });
    });
    var cross = s("line", { class: "cross", y1: T, y2: H - B, visibility: "hidden" });
    svg.appendChild(cross);
    function at(clientX, clientY) {
      var r = svg.getBoundingClientRect(), x = x0 + ((clientX - r.left) / r.width * W - L) / (W - L - R) * (x1 - x0), best = uniq[0];
      uniq.forEach(function (u) { if (Math.abs(u - x) < Math.abs(best - x)) best = u; });
      cross.setAttribute("x1", px(best)); cross.setAttribute("x2", px(best)); cross.setAttribute("visibility", "visible");
      var rows = [];
      series.forEach(function (se) { se.points.forEach(function (p) { if (p[0] === best) rows.push({ name: se.name, value: yFmt(p[1]), color: se.color, dash: se.dash }); }); });
      showTip(clientX, clientY, (opt.xTitle ? opt.xTitle + " " : "") + xFmt(best), rows);
    }
    svg.addEventListener("pointermove", function (e) { at(e.clientX, e.clientY); });
    svg.addEventListener("pointerdown", function (e) { at(e.clientX, e.clientY); });
    svg.addEventListener("pointerleave", function () { cross.setAttribute("visibility", "hidden"); hideTip(); });
    return svg;
    }
    var svg = build(640), lastW = 640;
    var legend = series.length > 1 && !opt.mini ? h("div", { class: "legend" }, series.map(function (se) {
      return h("span", null, h("i", { class: se.dash ? "dash" : null, style: { "border-color": se.color } }), se.name);
    })) : null;
    var tbl = null;
    if (opt.table !== false && !opt.mini) {
      tbl = h("details", { class: "tbl" }, h("summary", null, "Werte als Tabelle"),
              h("div", { class: "tw" }, h("table", null,
                h("thead", null, h("tr", null, h("th", null, opt.xTitle || ""), series.map(function (se) { return h("th", { class: "num" }, se.name); }))),
                h("tbody", null, uniq.map(function (u) {
                  return h("tr", null, h("td", null, xFmt(u)), series.map(function (se) {
                    var p = se.points.filter(function (q) { return q[0] === u; })[0];
                    return h("td", { class: "num" }, p ? yFmt(p[1]) : "–");
                  }));
                })))));
    }
    var box = h("div", { class: "chart" }, legend, svg, tbl);
    if (window.ResizeObserver) {
      new ResizeObserver(function (entries) {
        var w = Math.round(entries[0].contentRect.width);
        if (w < 80 || Math.abs(w - lastW) < 4) return;
        lastW = w;
        var fresh = build(w);
        box.replaceChild(fresh, svg);
        svg = fresh;
      }).observe(box);
    }
    return box;
  };

  // ---------------------------------------------------------------- Mini-Markdown (nur für die eigenen Berichte)
  PB.md = function (text) {
    var root = h("div", { class: "md" }), lines = String(text || "").split("\n"), list = null, table = null;
    function inline(el, str) {
      str.split(/(\*\*[^*]+\*\*)/).forEach(function (part) {
        if (/^\*\*.+\*\*$/.test(part)) el.appendChild(h("b", null, part.slice(2, -2))); else if (part) el.appendChild(document.createTextNode(part));
      });
      return el;
    }
    lines.forEach(function (ln) {
      if (/^\|/.test(ln)) {
        var cells = ln.replace(/^\||\|$/g, "").split("|").map(function (c) { return c.trim(); });
        if (/^[-: ]+$/.test(cells.join(""))) return;
        if (!table) { table = h("tbody"); root.appendChild(h("div", { class: "tw" }, h("table", null, table))); table._head = true; }
        var tag = table._head ? "th" : "td";
        table._head = false;
        table.appendChild(h("tr", null, cells.map(function (c) { return inline(h(tag), c); })));
        list = null;
        return;
      }
      table = null;
      var m;
      if ((m = /^(#{1,3}) (.*)/.exec(ln))) { root.appendChild(h(m[1].length === 1 ? "h1" : "h2", null, m[2])); list = null; }
      else if ((m = /^(\s*)- (.*)/.exec(ln))) {
        if (!list) { list = h("ul"); root.appendChild(list); }
        var li = inline(h("li", m[1].length ? { style: { "margin-left": "18px" } } : null), m[2]);
        list.appendChild(li);
      }
      else if (ln.trim()) { root.appendChild(inline(h("p", /^_.*_$/.test(ln.trim()) ? { class: "dim2" } : null), ln.trim().replace(/^_|_$/g, ""))); list = null; }
      else list = null;
    });
    return root;
  };

  // ---------------------------------------------------------------- Abrufe
  PB.api = function (path, body) {
    var opt = { headers: { "X-Requested-With": "polybot" }, credentials: "same-origin" };
    if (body !== undefined) { opt.method = "POST"; opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
    return fetch(path, opt).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok) throw new Error(j.error || (r.status === 401 ? "Anmeldung nötig – Seite neu laden" : "Abruf fehlgeschlagen (" + r.status + ")"));
        return j;
      });
    });
  };
  var toastT = null;
  PB.toast = function (msg, bad) {
    var old = document.querySelector(".toast");
    if (old) old.remove();
    var el = h("div", { class: "toast" + (bad ? " bad" : ""), role: "status" }, msg);
    document.body.appendChild(el);
    clearTimeout(toastT);
    toastT = setTimeout(function () { el.remove(); }, bad ? 7000 : 3500);
  };

  // ---------------------------------------------------------------- Seitenwechsel
  var main, title, sub, pill, seq = 0, cur = null, refreshSec = 60;
  PB.route = function () {
    var raw = (location.hash || "#/").replace(/^#\/?/, ""), parts = raw.split("?"), segs = parts[0].split("/"), q = {};
    (parts[1] || "").split("&").forEach(function (kv) { if (kv) { var i = kv.indexOf("="); q[decodeURIComponent(kv.slice(0, i))] = decodeURIComponent(kv.slice(i + 1)); } });
    return { name: segs[0] || "", arg: segs[1] ? decodeURIComponent(segs[1]) : null, q: q };
  };
  PB.setQuery = function (changes) {
    var r = PB.route(), q = Object.assign({}, r.q, changes), str = Object.keys(q).filter(function (k) { return q[k] !== "" && q[k] != null; })
      .map(function (k) { return encodeURIComponent(k) + "=" + encodeURIComponent(q[k]); }).join("&");
    location.hash = "#/" + r.name + (r.arg ? "/" + encodeURIComponent(r.arg) : "") + (str ? "?" + str : "");
  };
  function show(silent) {
    var r = PB.route(), page = PB.pages[r.name] || PB.pages[""], my = ++seq;
    if (!silent) {
      title.textContent = page.title;
      sub.textContent = page.sub || "";
      document.title = page.title + " · Polymarket-Bot";
      main.classList.add("loading");
      if (cur !== r.name) window.scrollTo(0, 0);
    }
    cur = r.name;
    page.load(r).then(function (d) {
      if (my !== seq) return;
      main.replaceChildren.apply(main, [].concat(page.render(d, r)).filter(Boolean));
      main.classList.remove("loading");
    }).catch(function (e) {
      if (my !== seq) return;
      main.classList.remove("loading");
      if (silent) return;
      main.replaceChildren(h("div", { class: "callout bad" }, h("b", null, "Die Seite konnte nicht geladen werden. "), e.message,
                             h("div", { style: { "margin-top": "8px" } }, h("button", { class: "btn small", on: { click: function () { show(); } } }, "Erneut versuchen"))));
    });
  }
  PB.reload = function () { show(true); };
  function status() {
    PB.api("/api/overview").then(function (d) {
      var sy = d.system, word = { ok: "Läuft", warnung: "Läuft mit Warnung", fehler: "Störung", startet: "Startet" }[sy.status] || sy.status;
      pill.replaceChildren(h("span", { class: "led " + (sy.running ? "läuft" : sy.status) }),
                           sy.running ? "Lauf aktiv" : word, !sy.running && sy.last_cycle ? h("span", { class: "hide-s" }, "· letzter Lauf " + F.ago(sy.last_cycle)) : null);
      pill.title = sy.last_error || "";
      var b = document.getElementById("nav-vorschlag"), n = (d.learning || {}).offene_vorschläge || 0;
      if (b) { b.hidden = !n; b.textContent = n; }
      refreshSec = d.refresh || 60;
    }).catch(function () { pill.replaceChildren(h("span", { class: "led fehler" }), "Keine Verbindung"); });
  }
  document.addEventListener("DOMContentLoaded", function () {
    main = document.getElementById("main"); title = document.getElementById("title"); sub = document.getElementById("sub"); pill = document.getElementById("state");
    window.addEventListener("hashchange", function () { show(); });
    show(); status();
    var last = Date.now();
    setInterval(function () {
      if (document.hidden || Date.now() - last < refreshSec * 1000) return;
      last = Date.now();
      status();
      var page = PB.pages[PB.route().name] || PB.pages[""], a = document.activeElement;
      var busy = a && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName);
      if (page.live && !busy) show(true);
    }, 5000);
  });
})();
