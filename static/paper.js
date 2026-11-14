/* Paper-Trading-Ansicht (bis v1.3 die ganze Oberfläche). Liest die Daten aus #data und frischt sie über /api/state auf. */
(function () {
  "use strict";
  var D = JSON.parse(document.getElementById("data").textContent);
  var TZ = "Europe/Berlin";
  var state = { filter: "all", account: "", tab: "overview", hist: "all", ana: "all", calMonth: null, calDay: null };
  try { state.filter = localStorage.getItem("pb_filter") || "all"; state.account = localStorage.getItem("pb_acc") || ""; } catch (e) {}
  function accObj(id) { return (D.accounts || []).filter(function (a) { return a.id === (id || state.account); })[0] || (D.accounts || [])[0] || {}; }
  function inA(p) { return p.account === state.account; }

  // ---------- Helfer ----------
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]; }); }
  function num(v, d) { return Number(v).toLocaleString("de-DE", { minimumFractionDigits: d, maximumFractionDigits: d }); }
  function usd(v) { return v == null || isNaN(v) ? "–" : num(v, 2) + " $"; }
  function susd(v) { return v == null || isNaN(v) ? "–" : (v >= 0 ? "+" : "−") + num(Math.abs(v), 2) + " $"; }
  function pct(v, d) { return v == null || isNaN(v) ? "–" : num(v * 100, d == null ? 1 : d) + " %"; }
  function cent(v) { return v == null ? "–" : num(v * 100, 1) + " ¢"; }
  function short(v) { var a = Math.abs(v); return (v >= 0 ? "+" : "−") + (a >= 100 ? num(a, 0) : num(a, 1)); }
  function cls(v) { return v > 0.0049 ? "pos" : (v < -0.0049 ? "neg" : "dim"); }
  function dt(iso) { if (!iso) return "–"; var d = new Date(iso); return d.toLocaleString("de-DE", { timeZone: TZ, day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }); }
  function dayKey(iso) { return new Intl.DateTimeFormat("en-CA", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(iso)); }
  function inF(cat) { return state.filter === "all" || state.filter === cat; }
  function mine(p) { return inA(p) && inF(p.category); }
  function untilText(iso) {
    if (!iso) return "";
    var h = (new Date(iso) - new Date()) / 36e5;
    if (h < 0) return "wartet auf Entscheidung";
    if (h < 1) return "endet in " + Math.max(1, Math.round(h * 60)) + " min";
    if (h < 48) return "endet in " + Math.round(h) + " h";
    return "endet in " + Math.round(h / 24) + " Tagen";
  }
  function sideBadge(name) {
    var n = String(name || "").toLowerCase();
    if (n === "yes" || n === "ja") return '<span class="badge b-yes">JA</span>';
    if (n === "no" || n === "nein") return '<span class="badge b-no">NEIN</span>';
    return '<span class="badge b-team">' + esc(name) + "</span>";
  }
  var CATN = { crypto: "Krypto", sports: "Sport", weather: "Wetter" };
  function catTag(c) { return '<span class="tag ' + c + '">' + (CATN[c] || c) + "</span>"; }
  function resChip(s) {
    return { won: '<span class="chip win">Gewonnen</span>', lost: '<span class="chip lose">Verloren</span>',
             void: '<span class="chip void">Storniert</span>', sold: '<span class="chip go">Verkauft</span>' }[s] || '<span class="chip go">Offen</span>';
  }
  function betClass(p) { if (p.status === "sold") return (p.pnl || 0) >= 0 ? "win" : "lose"; return { won: "win", lost: "lose", void: "void" }[p.status] || ""; }
  function openVal(p) { return p.shares * (p.mark != null ? p.mark : p.price); }

  var OPEN = D.open || [], CLOSED = D.closed || [];

  // ---------- Kennzahlen ----------
  function figures() {
    var op = OPEN.filter(mine);
    var cl = CLOSED.filter(mine);
    var settled = cl.filter(function (p) { return (p.status === "won" || p.status === "lost") && p.kind !== "arb"; });
    var wins = settled.filter(function (p) { return p.status === "won"; });
    var realized = cl.reduce(function (a, p) { return a + (p.pnl || 0); }, 0);
    var unreal = op.reduce(function (a, p) { return a + openVal(p) - p.cost; }, 0);
    var staked = cl.reduce(function (a, p) { return a + p.cost; }, 0);
    var invested = op.reduce(function (a, p) { return a + p.cost; }, 0);
    var today = dayKey(new Date().toISOString());
    var todayPnl = cl.filter(function (p) { return p.closed_at && dayKey(p.closed_at) === today; })
                     .reduce(function (a, p) { return a + (p.pnl || 0); }, 0);
    return { op: op, cl: cl, settled: settled, wins: wins, realized: realized, unreal: unreal, staked: staked,
             invested: invested, todayPnl: todayPnl,
             winrate: settled.length ? wins.length / settled.length : null,
             expected: settled.length ? settled.reduce(function (a, p) { return a + p.own_p; }, 0) / settled.length : null };
  }

  function renderKpis() {
    var f = figures(), s = accObj();
    var total = state.filter === "all" ? s.equity - s.start : f.realized + f.unreal;
    var k = [];
    k.push('<div class="kpi big"><div class="l">Kontowert · ' + esc(s.name) + '</div><div class="v">' + usd(s.equity) +
           '</div><div class="s">Start ' + usd(s.start) + ' · Bargeld ' + usd(s.cash) + "</div></div>");
    k.push('<div class="kpi"><div class="l">Gewinn/Verlust' + (state.filter === "all" ? "" : " · " + CATN[state.filter]) +
           '</div><div class="v ' + cls(total) + '">' + susd(total) + '</div><div class="s">' +
           (state.filter === "all" ? pct(total / s.start) + " aufs Startkapital" : "inkl. offener Wetten") + "</div></div>");
    k.push('<div class="kpi"><div class="l">Heute abgerechnet</div><div class="v ' + cls(f.todayPnl) + '">' + susd(f.todayPnl) + "</div></div>");
    k.push('<div class="kpi"><div class="l">Offene Wetten</div><div class="v">' + f.op.length + '</div><div class="s">' + usd(f.invested) +
           ' im Markt · <span class="' + cls(f.unreal) + '">' + susd(f.unreal) + "</span></div></div>");
    k.push('<div class="kpi"><div class="l">Trefferquote</div><div class="v">' + pct(f.winrate, 0) + '</div><div class="s">erwartet ' +
           pct(f.expected, 0) + " · " + f.wins.length + "/" + f.settled.length + "</div></div>");
    k.push('<div class="kpi"><div class="l">Rendite auf Einsatz</div><div class="v ' + cls(f.realized) + '">' +
           (f.staked ? pct(f.realized / f.staked) : "–") + '</div><div class="s">' + usd(f.staked) + " eingesetzt</div></div>");
    var clvRows = OPEN.concat(CLOSED).filter(function (p) { return mine(p) && p.category === "sports" && p.close_p != null && p.kind !== "arb"; });
    if (state.filter === "all" || state.filter === "sports") {
      var clv = clvRows.length ? clvRows.reduce(function (a, p) { return a + p.close_p - p.price; }, 0) / clvRows.length : null;
      k.push('<div class="kpi"><div class="l">Schlusskurs-Vorteil Sport</div><div class="v ' + (clv == null ? "" : cls(clv)) + '">' +
             (clv == null ? "–" : (clv >= 0 ? "+" : "−") + num(Math.abs(clv) * 100, 1) + " Pkt") + '</div><div class="s">' + clvRows.length + " Wetten · positiv = besser als der Markt kurz vor Anpfiff</div></div>");
    }
    var sold = f.cl.filter(function (p) { return p.status === "sold"; });
    if (sold.length) {
      var sp = sold.reduce(function (a, p) { return a + (p.pnl || 0); }, 0);
      k.push('<div class="kpi"><div class="l">Früh verkauft</div><div class="v ' + cls(sp) + '">' + susd(sp) + '</div><div class="s">' + sold.length + " Wetten vor der Entscheidung verkauft</div></div>");
    }
    if (s.paused) {
      k.unshift('<div class="kpi big" style="border-color:var(--neg)"><div class="l neg">⛔ Risikobremse aktiv</div><div class="s">' + esc(s.paused) +
                '</div><button class="pausebtn" data-resume="' + s.id + '">Fortsetzen</button></div>');
    }
    document.getElementById("kpis").innerHTML = k.join("");
    bindResume(document.getElementById("kpis"));
  }

  // ---------- Chart ----------
  function renderChart() {
    var pts, label, base;
    if (state.filter === "all") {
      pts = ((D.equity || {})[state.account] || []).map(function (r) { return { t: r.ts, v: r.equity }; });
      label = "Kontowert · " + accObj().name; base = D.cfg.start;
    } else {
      var cl = CLOSED.filter(function (p) { return inA(p) && p.category === state.filter && p.closed_at; })
                     .sort(function (a, b) { return a.closed_at < b.closed_at ? -1 : 1; });
      var acc = 0; pts = [{ t: accObj().started_at, v: 0 }];
      cl.forEach(function (p) { acc += p.pnl || 0; pts.push({ t: p.closed_at, v: acc }); });
      label = "Gewinn/Verlust " + CATN[state.filter] + " (abgerechnet)"; base = 0;
    }
    document.getElementById("chart-title").textContent = label;
    var el = document.getElementById("chart");
    if (pts.length < 2) { el.innerHTML = '<div class="empty">Noch zu wenig Verlauf – kommt mit den ersten Stunden/Abrechnungen.</div>'; return; }
    var W = 600, H = 170, vals = pts.map(function (p) { return p.v; });
    var lo = Math.min.apply(null, vals.concat([base])), hi = Math.max.apply(null, vals.concat([base]));
    var pad = (hi - lo) * 0.08 || 1; lo -= pad; hi += pad;
    function y(v) { return H - (v - lo) / (hi - lo) * H; }
    var line = pts.map(function (p, i) { return (i * W / (pts.length - 1)).toFixed(1) + "," + y(p.v).toFixed(1); });
    var last = vals[vals.length - 1], col = last >= base ? "var(--pos)" : "var(--neg)";
    el.innerHTML = '<svg class="chart" viewBox="0 0 ' + W + " " + H + '" preserveAspectRatio="none">' +
      '<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="' + col + '" stop-opacity=".28"/>' +
      '<stop offset="1" stop-color="' + col + '" stop-opacity="0"/></linearGradient></defs>' +
      '<line class="base" x1="0" x2="' + W + '" y1="' + y(base).toFixed(1) + '" y2="' + y(base).toFixed(1) + '"/>' +
      '<polygon fill="url(#g)" points="0,' + H + " " + line.join(" ") + " " + W + "," + H + '"/>' +
      '<polyline class="ln" stroke="' + col + '" points="' + line.join(" ") + '"/></svg>' +
      '<div class="axis"><span>' + dt(pts[0].t) + '</span><span>' + (state.filter === "all" ? usd(last) : susd(last)) +
      '</span><span>' + dt(pts[pts.length - 1].t) + "</span></div>";
  }

  // ---------- Übersicht ----------
  function renderOverview() {
    var rows = ['<tr><th>Bereich</th><th class="n">Offen</th><th class="n">Abgerechn.</th><th class="n">Treffer</th><th class="n">G/V</th></tr>'];
    [["crypto", "Krypto"], ["sports", "Sport"], ["weather", "Wetter"]].forEach(function (c) {
      var op = OPEN.filter(function (p) { return inA(p) && p.category === c[0]; });
      var cl = CLOSED.filter(function (p) { return inA(p) && p.category === c[0]; });
      var st = cl.filter(function (p) { return (p.status === "won" || p.status === "lost") && p.kind !== "arb"; });
      var w = st.filter(function (p) { return p.status === "won"; }).length;
      var pnl = cl.reduce(function (a, p) { return a + (p.pnl || 0); }, 0);
      rows.push("<tr><td>" + catTag(c[0]) + '</td><td class="n">' + op.length + '</td><td class="n">' + cl.length +
                '</td><td class="n">' + (st.length ? pct(w / st.length, 0) : "–") + '</td><td class="n ' + cls(pnl) + '">' + susd(pnl) + "</td></tr>");
    });
    document.getElementById("cats").innerHTML = rows.join("");
    var soon = OPEN.filter(mine)
                   .sort(function (a, b) { return (a.end_date || "") < (b.end_date || "") ? -1 : 1; }).slice(0, 3);
    document.getElementById("soon").innerHTML = soon.length ? soon.map(betCard).join("") : '<div class="empty">Keine offenen Wetten.</div>';
    var c = D.cfg;
    document.getElementById("rules").innerHTML = "<b>" + esc(accObj().name) + ":</b> " + esc(accObj().desc || "") + ". Der Bot wettet nur, wenn seine <b>eigene</b> Wahrscheinlichkeit nach Gebühren deutlich vom Polymarket-Kurs abweicht (Krypto ≥ " +
      num(c.min_edge_crypto * 100, 0) + " Pkt, Sport ≥ " + num(c.min_edge_sports * 100, 0) + " Pkt, Wetter ≥ " + num(c.min_edge_weather * 100, 0) + " Pkt). Einsatz " +
      (c.stake_mode === "kelly" ? "nach Kelly" : num(c.stake_min * 100, 0) + "–" + num(c.stake_max * 100, 0) + " % des Kontos je nach Sicherheit") +
      ", max. " + num(c.max_group * 100, 0) + " % für alles am selben Coin+Stichtag bzw. Spiel und " + num(c.max_exposure * 100, 0) + " % insgesamt.";
  }

  // ---------- Wetten-Karte ----------
  function betCard(p) {
    var isOpen = p.status === "open";
    var val = isOpen ? openVal(p) : null;
    var right = isOpen ? '<span class="res ' + cls(val - p.cost) + '">' + susd(val - p.cost) + "</span>"
                       : '<span class="res ' + cls(p.pnl) + '">' + susd(p.pnl) + "</span>";
    var meta = [
      ["Kauf", cent(p.price)],
      [isOpen ? "Jetzt" : (p.status === "sold" ? "Verkauft zu" : "Ergebnis"), isOpen ? cent(p.mark != null ? p.mark : p.price) : (p.status === "sold" ? cent(p.exit_price) : resChip(p.status))],
      ["Bot sagt", pct(p.own_p, 0)],
      ["Einsatz", usd(p.cost)],
      [isOpen ? "Wert jetzt" : (p.status === "sold" ? "Erlös" : "Auszahlung"), usd(isOpen ? val : p.payout)],
      [isOpen ? "Ende" : "Abgerechnet", isOpen ? dt(p.end_date) : dt(p.closed_at)]
    ].map(function (m) { return "<div><b>" + m[0] + "</b>" + m[1] + "</div>"; }).join("");
    var clv = "";
    if (p.category === "sports" && p.close_p != null && p.kind !== "arb") {
      var d = p.close_p - p.price;
      clv = '<div class="clv">Schlusskurs (Buchmacher kurz vor Anpfiff): ' + pct(p.close_p) + ' · <span class="' + cls(d) + '">' +
            (d >= 0 ? "+" : "−") + num(Math.abs(d) * 100, 1) + " Pkt besser als Kaufkurs</span></div>";
    }
    return '<div class="bet ' + betClass(p) + '"><div class="top">' + catTag(p.category) + (p.kind === "arb" ? '<span class="tag arb">Arbitrage</span>' : "") +
      sideBadge(p.side_name) + (p.status === "sold" ? resChip("sold") : "") +
      (isOpen ? '<span class="chip">' + untilText(p.end_date) + "</span>" : "") + right + '</div><div class="q">' + esc(p.question) +
      '</div><div class="meta">' + meta + "</div>" + clv + '<div class="info">' + esc(p.info || "") +
      (p.fill_note ? " · " + esc(p.fill_note) : "") + "</div></div>";
  }

  function renderOpen() {
    var op = OPEN.filter(mine)
                 .sort(function (a, b) { return (a.end_date || "") < (b.end_date || "") ? -1 : 1; });
    document.getElementById("n-open").textContent = op.length;
    document.getElementById("open-list").innerHTML = op.length ? op.map(betCard).join("") : '<div class="empty">Keine offenen Wetten in diesem Bereich.</div>';
    var ords = (D.orders || []).filter(function (o) { return o.status === "open" && mine(o); });
    document.getElementById("orders-wrap").innerHTML = ords.length ? '<h2>Offene Kaufgebote (Limit-Orders)</h2>' + ords.map(function (o) {
      return '<div class="bet"><div class="top">' + catTag(o.category) + sideBadge(o.side_name) + '<span class="chip">Gebot</span><span class="res">' + cent(o.price) +
             '</span></div><div class="q">' + esc(o.question) + '</div><div class="meta"><div><b>Gebot</b>' + cent(o.price) + '</div><div><b>Bot sagt</b>' + pct(o.q, 0) +
             '</div><div><b>Einsatz</b>' + usd(o.stake) + '</div></div><div class="info">gestellt ' + dt(o.created_at) + " · gilt als gekauft, sobald der Briefkurs das Gebot erreicht</div></div>";
    }).join("") : "";
  }

  function renderHistory() {
    var cl = CLOSED.filter(function (p) { return mine(p) && (state.hist === "all" || p.status === state.hist || (state.hist === "won" && p.status === "sold" && p.pnl >= 0) || (state.hist === "lost" && p.status === "sold" && p.pnl < 0)); });
    document.getElementById("n-hist").textContent = CLOSED.filter(mine).length;
    document.getElementById("hist-list").innerHTML = cl.length ? cl.slice(0, 300).map(betCard).join("") : '<div class="empty">Noch nichts abgerechnet.</div>';
  }

  // ---------- Kalender ----------
  var MONTHS = ["Januar","Februar","März","April","Mai","Juni","Juli","August","September","Oktober","November","Dezember"];
  function dayMaps() {
    var closed = {}, opened = {};
    CLOSED.forEach(function (p) { if (!mine(p) || !p.closed_at) return; var k = dayKey(p.closed_at); (closed[k] = closed[k] || []).push(p); });
    OPEN.concat(CLOSED).forEach(function (p) { if (!mine(p) || !p.opened_at) return; var k = dayKey(p.opened_at); (opened[k] = opened[k] || []).push(p); });
    return { closed: closed, opened: opened };
  }
  function renderCalendar() {
    if (!state.calMonth) { var t = dayKey(new Date().toISOString()); state.calMonth = t.slice(0, 7); }
    var y = +state.calMonth.slice(0, 4), m = +state.calMonth.slice(5, 7);
    document.getElementById("cal-title").textContent = MONTHS[m - 1] + " " + y;
    var maps = dayMaps(), today = dayKey(new Date().toISOString());
    var first = new Date(Date.UTC(y, m - 1, 1)), days = new Date(Date.UTC(y, m, 0)).getUTCDate();
    var offset = (first.getUTCDay() + 6) % 7;
    var html = ["Mo","Di","Mi","Do","Fr","Sa","So"].map(function (d) { return '<div class="wd">' + d + "</div>"; });
    for (var i = 0; i < offset; i++) html.push('<div class="day out"></div>');
    var mPnl = 0, mN = 0, mW = 0, best = null, worst = null;
    for (var d = 1; d <= days; d++) {
      var key = y + "-" + String(m).padStart(2, "0") + "-" + String(d).padStart(2, "0");
      var list = maps.closed[key] || [], op = maps.opened[key] || [];
      var pnl = list.reduce(function (a, p) { return a + (p.pnl || 0); }, 0);
      mPnl += pnl; mN += list.length; mW += list.filter(function (p) { return p.status === "won" || (p.status === "sold" && p.pnl >= 0); }).length;
      if (list.length) { if (!best || pnl > best.v) best = { d: d, v: pnl }; if (!worst || pnl < worst.v) worst = { d: d, v: pnl }; }
      var c = "day" + (list.length ? (pnl >= 0 ? " up" : " down") : "") + (key === today ? " today" : "") + (key === state.calDay ? " sel" : "");
      var dots = op.length ? '<span class="dots">' + new Array(Math.min(op.length, 3) + 1).join("<i></i>") + "</span>" : "";
      html.push('<div class="' + c + '" data-day="' + key + '"><span class="d">' + d + "</span>" + dots +
                '<span class="p ' + (list.length ? cls(pnl) : "") + '">' + (list.length ? short(pnl) : "") + "</span></div>");
    }
    document.getElementById("cal-grid").innerHTML = html.join("");
    document.getElementById("cal-sum").innerHTML =
      '<div><b>Monat G/V</b><span class="' + cls(mPnl) + '">' + susd(mPnl) + "</span></div>" +
      "<div><b>Wetten · Treffer</b>" + mN + " · " + (mN ? pct(mW / mN, 0) : "–") + "</div>" +
      "<div><b>Bester / schwächster</b>" + (best ? '<span class="pos">' + best.d + ".</span> / <span class=\"neg\">" + worst.d + ".</span>" : "–") + "</div>";
    Array.prototype.forEach.call(document.querySelectorAll("#cal-grid .day[data-day]"), function (el) {
      el.addEventListener("click", function () { state.calDay = state.calDay === el.dataset.day ? null : el.dataset.day; renderCalendar(); });
    });
    renderDayPanel(maps);
  }
  function renderDayPanel(maps) {
    var panel = document.getElementById("daypanel"), inner = document.getElementById("daypanel-in");
    if (!state.calDay) { panel.classList.remove("open"); return; }
    var list = maps.closed[state.calDay] || [], op = maps.opened[state.calDay] || [];
    var pnl = list.reduce(function (a, p) { return a + (p.pnl || 0); }, 0);
    var stake = list.reduce(function (a, p) { return a + p.cost; }, 0);
    var w = list.filter(function (p) { return p.status === "won" || (p.status === "sold" && p.pnl >= 0); }).length;
    var d = new Date(state.calDay + "T12:00:00Z");
    var title = d.toLocaleDateString("de-DE", { weekday: "long", day: "numeric", month: "long", timeZone: "UTC" });
    inner.innerHTML = '<div class="hd"><span class="t">' + title + '</span><button class="close" id="dp-close">Schließen</button></div>' +
      '<div class="stats3"><div><b>G/V</b><span class="' + cls(pnl) + '">' + susd(pnl) + "</span></div><div><b>Abgerechnet</b>" + list.length +
      "</div><div><b>Gewonnen</b>" + w + " / " + list.length + "</div><div><b>Eingesetzt</b>" + usd(stake) + "</div></div>" +
      '<div class="sub2">Abgerechnete Wetten</div>' + (list.length ? list.map(betCard).join("") : '<div class="empty">An diesem Tag wurde nichts abgerechnet.</div>') +
      (op.length ? '<div class="sub2">Neu eröffnet (' + op.length + ")</div>" + op.map(betCard).join("") : "");
    panel.classList.add("open");
    document.getElementById("dp-close").addEventListener("click", function () { state.calDay = null; renderCalendar(); });
  }

  // ---------- Analyse ----------
  function renderAnalysis() {
    var all = (D.state.opportunities || []).filter(function (o) { return inF(o.category); });
    var list = all.filter(function (o) {
      if (state.ana === "plus") return o.edge > 0;
      if (state.ana === "traded") return (o.bought || []).length > 0 || /eröffnet|schon gehandelt/.test(o.status);
      return true;
    });
    var seen = D.state.markets_seen || {};
    document.getElementById("ana-note").innerHTML = "Letzter Lauf " + dt(D.state.last_scan) + ": " +
      Object.keys(seen).map(function (k) { return seen[k] + " " + (CATN[k] || k) + "-Märkte"; }).join(", ") +
      " gesehen, " + all.length + " selbst berechnet. <b>Vorsprung</b> = eigene Wahrscheinlichkeit minus Kaufkurs inkl. Gebühr.";
    document.getElementById("ana-list").innerHTML = list.length ? list.slice(0, 150).map(function (o) {
      var traded = /eröffnet/.test(o.status);
      var by = (o.bought || []).map(function (id) { return accObj(id).name; });
      var lo = Math.min(o.own_p, o.market_p) * 100, hi = Math.max(o.own_p, o.market_p) * 100;
      var col = o.own_p >= o.market_p ? "rgba(47,208,127,.35)" : "rgba(255,93,108,.35)";
      return '<div class="bet' + (traded ? " win" : "") + '"><div class="top">' + catTag(o.category) + sideBadge(o.side_name) +
        (traded ? '<span class="chip win">gekauft</span>' : '<span class="chip">' + esc(o.status) + "</span>") +
        '<span class="res ' + cls(o.edge) + '">' + (o.edge >= 0 ? "+" : "−") + num(Math.abs(o.edge) * 100, 1) + " Pkt</span></div>" +
        '<div class="q">' + esc(o.question) + "</div>" +
        '<div class="pbar"><span class="fill" style="left:' + lo + "%;width:" + (hi - lo) + "%;background:" + col + '"></span>' +
        '<span class="mk m" style="left:' + o.market_p * 100 + '%"></span><span class="mk b" style="left:' + o.own_p * 100 + '%"></span></div>' +
        '<div class="legend"><span><i style="background:var(--acc)"></i>Bot ' + pct(o.own_p) + '</span><span><i style="background:#c9d2e3"></i>Markt ' +
        pct(o.market_p) + "</span><span>Kurs " + cent(o.price) + "</span></div>" +
        (by.length ? '<div class="clv">Gekauft von: ' + by.map(esc).join(", ") + "</div>" : "") +
        '<div class="info">' + (o.model ? "<b>" + esc(o.model) + ":</b> " : "") + esc(o.info || "") + "</div></div>";
    }).join("") : '<div class="empty">Keine passenden Märkte im letzten Lauf.</div>';
    var arbs = D.state.arbs || [];
    document.getElementById("arbs").innerHTML = arbs.length ? arbs.map(function (a) {
      var st = Object.keys(a.status || {}).map(function (k) { return esc(accObj(k).name) + ": " + esc(a.status[k]); }).join(" · ");
      return '<div class="bet win"><div class="top"><span class="tag arb">' + esc(a.type) + '</span><span class="res pos">+' + num(a.profit * 100, 1) +
             ' ¢ pro Paket</span></div><div class="q">' + esc(a.title) + '</div><div class="info">' + a.legs + " Teile · Kosten " + cent(a.cost) +
             " · sichere Auszahlung " + num(a.payout, 0) + " $ · " + st + "</div></div>";
    }).join("") : '<div class="empty">Keine Widersprüche gefunden – das ist der Normalfall. Polymarket ist meist in sich stimmig; wenn nicht, kauft das Konto „Nur Arbitrage“ (und das Hauptkonto) sofort.</div>';
    var sk = D.state.skips || {}, keys = Object.keys(sk), max = Math.max.apply(null, keys.map(function (k) { return sk[k]; }).concat([1]));
    document.getElementById("skips").innerHTML = keys.length ? keys.map(function (k) {
      return '<div><span class="bl">' + esc(k) + '</span><span class="bt"><i style="width:' + (sk[k] / max * 100) + '%"></i></span><span class="bv">' + sk[k] + "</span></div>";
    }).join("") : '<div class="dim">–</div>';
  }

  // ---------- Treffsicherheit ----------
  function renderCalib() {
    var key = state.filter === "all" ? "all" : state.filter;
    var c = (D.stats.calibration || {})[key] || {};
    function f4(v) { return v == null ? "–" : num(v, 4); }
    var better = c.brier_bot != null && c.brier_market != null ? c.brier_bot < c.brier_market : null;
    var W = D.state.weights || {}, R = D.registry || {};
    var wtxt = ["crypto", "sports", "weather"].map(function (k) {
      var x = W[k] || {}, r = R[k] || {};
      var act = r.mix_w != null && r.mix_w < 0.999 ? num(r.mix_w * 100, 0) + " % Modell" : "ohne Mischung";
      return CATN[k] + ": aktiv " + act + (x.w == null ? "" : " (gemessen am besten: " + num(x.w * 100, 0) + " % Modell)");
    }).join(" · ");
    document.getElementById("calib-kpis").innerHTML =
      '<div class="kpi"><div class="l">Entschiedene Märkte</div><div class="v">' + (c.n || 0) + '</div><div class="s">von ' + (c.n_all || 0) + " insgesamt</div></div>" +
      '<div class="kpi"><div class="l">Brier Bot</div><div class="v ' + (better === true ? "pos" : better === false ? "neg" : "") + '">' + f4(c.brier_bot) + '</div><div class="s">LogLoss ' + f4(c.logloss_bot) + "</div></div>" +
      '<div class="kpi"><div class="l">Brier Markt</div><div class="v">' + f4(c.brier_market) + '</div><div class="s">LogLoss ' + f4(c.logloss_market) + "</div></div>" +
      '<div class="kpi big"><div class="l">Urteil</div><div class="v" style="font-size:16px">' +
      (better === null || (c.n || 0) < 50 ? "Noch zu wenig Daten (ab ~200 Märkten aussagekräftig)" : better ? "Bot genauer als der Markt" : "Markt genauer als der Bot") + "</div></div>" +
      '<div class="kpi big"><div class="l">Mischung mit dem Marktpreis</div><div class="s" style="font-size:13px;color:var(--txt)">' + wtxt +
      '</div><div class="s">Die Mischung wird nicht mehr von allein angewendet. Der Bot misst, was geholfen hätte, und schlägt es im <a href="/#/lernen" style="color:var(--acc)">Lernzentrum</a> vor – aktiv wird es erst nach deiner Freigabe. Die genauere Auswertung je Modell steht dort und unter Performance.</div></div>';
    var b = D.stats.calibration_buckets || [];
    document.getElementById("buckets").innerHTML = '<tr><th>Bot sagt</th><th class="n">Märkte</th><th class="n">Ø Bot</th><th class="n">Ø Markt</th><th class="n">Eingetreten</th></tr>' +
      (b.length ? b.map(function (r) {
        return "<tr><td>" + r.range + '</td><td class="n">' + r.n + '</td><td class="n">' + pct(r.bot, 0) + '</td><td class="n">' + pct(r.market, 0) + '</td><td class="n"><b>' + pct(r.actual, 0) + "</b></td></tr>";
      }).join("") : '<tr><td colspan="5" class="dim">Noch keine entschiedenen Märkte.</td></tr>');
  }

  // ---------- Testkonten ----------
  var COLORS = ["#5b8cff", "#2fd07f", "#f5b84a", "#ff5d6c", "#38bdf8", "#c084fc", "#f472b6"];
  function renderAccounts() {
    var accs = D.accounts || [];
    var sorted = accs.slice().sort(function (a, b) { return b.equity - a.equity; });
    var series = accs.map(function (a, i) { return { a: a, c: COLORS[i % COLORS.length], pts: ((D.equity || {})[a.id] || []).map(function (r) { return r.equity; }) }; });
    var all = []; series.forEach(function (s) { all = all.concat(s.pts); });
    var el = document.getElementById("acc-chart");
    if (all.length < 4) { el.innerHTML = '<div class="empty">Der Vergleich füllt sich mit den nächsten Stunden.</div>'; }
    else {
      var W = 600, H = 170, lo = Math.min.apply(null, all.concat([D.cfg.start])), hi = Math.max.apply(null, all.concat([D.cfg.start]));
      var pad = (hi - lo) * 0.08 || 1; lo -= pad; hi += pad;
      var y = function (v) { return H - (v - lo) / (hi - lo) * H; };
      var n = Math.max.apply(null, series.map(function (s) { return s.pts.length; }));
      el.innerHTML = '<svg class="chart" viewBox="0 0 ' + W + " " + H + '" preserveAspectRatio="none"><line class="base" x1="0" x2="' + W + '" y1="' + y(D.cfg.start) + '" y2="' + y(D.cfg.start) + '"/>' +
        series.map(function (s) {
          if (s.pts.length < 2) return "";
          var off = n - s.pts.length;
          return '<polyline class="ln" stroke="' + s.c + '" points="' + s.pts.map(function (v, i) { return ((i + off) * W / (n - 1)).toFixed(1) + "," + y(v).toFixed(1); }).join(" ") + '"/>';
        }).join("") + "</svg>";
    }
    document.getElementById("acc-legend").innerHTML = series.map(function (s) { return '<span><i style="background:' + s.c + '"></i>' + esc(s.a.name) + "</span>"; }).join("");
    var hs = accs.filter(function (a) { return /^haupt_/.test(a.id); });
    var hsum = hs.reduce(function (x, a) { return x + a.equity; }, 0), hstart = hs.reduce(function (x, a) { return x + a.start; }, 0);
    var head = hs.length ? '<div class="acc" style="cursor:default"><div class="row"><span class="nm">Hauptkonten zusammen</span><span class="' + cls(hsum - hstart) + '">' +
      susd(hsum - hstart) + '</span></div><div class="ds">' + hs.map(function (a) { return esc(a.name.replace("Haupt · ", "")) + " " + usd(a.equity); }).join(" · ") +
      " · gesamt " + usd(hsum) + " von " + usd(hstart) + "</div></div>" : "";
    document.getElementById("acc-list").innerHTML = head + sorted.map(function (a, i) {
      var meta = [
        ["Kontowert", usd(a.equity)], ["G/V", '<span class="' + cls(a.pnl_total) + '">' + susd(a.pnl_total) + "</span>"],
        ["Offen / abgerechnet", a.open_count + " / " + a.closed_count], ["Treffer", pct(a.winrate, 0) + ' <span class="dim">(erw. ' + pct(a.expected_winrate, 0) + ")</span>"],
        ["Rendite auf Einsatz", pct(a.roi)], a.id === "limit" ? ["Offene Gebote", a.orders] : [a.id === "arbitrage" ? "Arbitrage-G/V" : "Früh verkauft", a.id === "arbitrage" ? susd(a.arb_pnl) : a.sold + " (" + susd(a.sold_pnl) + ")"]
      ].map(function (m) { return "<div><b>" + m[0] + "</b>" + m[1] + "</div>"; }).join("");
      return '<div class="acc' + (a.id === state.account ? " cur" : "") + '" data-acc="' + a.id + '"><div class="row"><span class="nm">' + esc(a.name) +
             (i === 0 && a.closed_count ? '<span class="rank">vorne</span>' : "") + '</span><span class="' + cls(a.pnl_total) + '">' + pct(a.pnl_total / a.start) + "</span></div>" +
             '<div class="ds">' + esc(a.desc) + '</div><div class="meta">' + meta + "</div>" +
             (a.paused ? '<div class="clv neg">⛔ ' + esc(a.paused) + '</div><button class="pausebtn" data-resume="' + a.id + '">Fortsetzen</button>' : "") + "</div>";
    }).join("");
    Array.prototype.forEach.call(document.querySelectorAll("#acc-list .acc"), function (el) {
      el.addEventListener("click", function (ev) { if (ev.target.dataset.resume) return; setAccount(el.dataset.acc); showTab("overview"); window.scrollTo(0, 0); });
    });
    bindResume(document.getElementById("acc-list"));
  }
  function renderAccSel() {
    var sel = document.getElementById("accsel");
    sel.innerHTML = (D.accounts || []).map(function (a) {
      return '<option value="' + a.id + '"' + (a.id === state.account ? " selected" : "") + ">" + esc(a.name) + " · " + usd(a.equity) + "</option>";
    }).join("");
  }
  function bindResume(root) {
    Array.prototype.forEach.call(root.querySelectorAll("[data-resume]"), function (b) {
      b.addEventListener("click", function (ev) {
        ev.stopPropagation(); b.disabled = true; b.textContent = "…";
        fetch("api/account/" + b.dataset.resume + "/resume", { method: "POST", headers: { "X-Requested-With": "polybot" } }).then(function () { return fetch("api/state"); })
          .then(function (r) { return r.json(); }).then(function (d) { D = d; OPEN = d.open || []; CLOSED = d.closed || []; renderAll(); })
          .catch(function () { b.textContent = "Fehler"; });
      });
    });
  }
  function setAccount(id) {
    state.account = id; state.calDay = null;
    try { localStorage.setItem("pb_acc", id); } catch (e) {}
    renderAll();
  }

  // ---------- Status ----------
  function renderStatus() {
    var st = D.state, src = D.sources;
    var pill = document.getElementById("status");
    var dc = st.running ? "run" : (st.last_error ? "err" : "");
    pill.innerHTML = '<span class="dot ' + dc + '"></span>' + (st.running ? "Lauf aktiv" : st.last_error ? "Fehler" : "Läuft · " + dt(st.last_scan).slice(-5));
    document.getElementById("hsub").textContent = "Spielgeld · alle " + num(D.cfg.interval, 0) + " min";
    var pr = src.prices || {};
    document.getElementById("sources").innerHTML =
      Object.keys(pr).map(function (k) { return '<div class="row"><span>' + k + '</span><span class="' + (/^FEHLER/.test(pr[k]) ? "err" : "dim") + '">' + esc(pr[k]) + "</span></div>"; }).join("") +
      Object.keys(src.options || {}).map(function (k) { return '<div class="row"><span>Optionen ' + k + '</span><span class="dim">' + esc(src.options[k]) + "</span></div>"; }).join("") +
      (function () { var w = src.weather || {}, ks = Object.keys(w), ok = ks.filter(function (k) { return /^OK/.test(w[k]); }).length;
        return ks.length ? '<div class="row"><span>Wetter-Vorhersagen</span><span class="dim">' + ok + " von " + ks.length + " Stationen OK" +
          (ok < ks.length ? " · Fehler: " + esc(ks.filter(function (k) { return !/^OK/.test(w[k]); }).slice(0, 3).join(", ")) : "") + "</span></div>" : ""; })() +
      '<div class="row" style="margin-top:8px"><span>Buchmacher-Quoten</span><span class="dim">' + esc(src.odds) + "</span></div>" +
      '<div class="row"><span>Telegram</span><span class="dim" style="text-align:right">' + esc(src.telegram || "") + "</span></div>" +
      '<div class="row"><span>Ligen</span><span class="dim" style="text-align:right">' + esc((D.cfg.leagues || []).join(", ")) + "</span></div>" +
      (st.last_error ? '<div class="err" style="margin-top:8px">' + esc(st.last_error) + "</div>" : "");
    document.getElementById("log").innerHTML = (D.logs || []).map(function (l) {
      return '<div><span class="t">' + dt(l.ts) + '</span><span class="' + (l.level === "error" ? "err" : "") + '">' + esc(l.msg) + "</span></div>";
    }).join("") || '<div class="dim">–</div>';
  }

  // ---------- Steuerung ----------
  function renderAll() {
    if (!(D.accounts || []).some(function (a) { return a.id === state.account; })) state.account = ((D.accounts || [])[0] || {}).id || "";
    renderAccSel(); renderKpis(); renderChart(); renderOverview(); renderOpen(); renderHistory(); renderCalendar(); renderAccounts(); renderAnalysis(); renderCalib(); renderStatus();
  }
  function setOn(sel, attr, val) { Array.prototype.forEach.call(document.querySelectorAll(sel), function (b) { b.classList.toggle("on", b.dataset[attr] === val); }); }
  function showTab(t) {
    state.tab = t; setOn("#tabs button", "t", t);
    var tb = document.querySelector('#tabs button[data-t="' + t + '"]');
    if (tb && tb.scrollIntoView) { try { tb.scrollIntoView({ inline: "center", block: "nearest" }); } catch (e) {} }
    Array.prototype.forEach.call(document.querySelectorAll("section"), function (s) { s.classList.toggle("on", s.id === t); });
    try { history.replaceState(null, "", "#tab-" + t); } catch (e) {}
  }
  function bind(sel, attr, fn) { Array.prototype.forEach.call(document.querySelectorAll(sel), function (b) { b.addEventListener("click", function () { fn(b.dataset[attr]); }); }); }
  bind("#filter button", "f", function (f) { state.filter = f; try { localStorage.setItem("pb_filter", f); } catch (e) {} setOn("#filter button", "f", f); renderAll(); });
  bind("#tabs button", "t", showTab);
  document.getElementById("accsel").addEventListener("change", function (e) { setAccount(e.target.value); });
  bind("#hist-filter button", "h", function (h) { state.hist = h; setOn("#hist-filter button", "h", h); renderHistory(); });
  bind("#ana-filter button", "a", function (a) { state.ana = a; setOn("#ana-filter button", "a", a); renderAnalysis(); });
  function shiftMonth(n) { var y = +state.calMonth.slice(0, 4), m = +state.calMonth.slice(5, 7) - 1 + n; y += Math.floor(m / 12); m = ((m % 12) + 12) % 12;
    state.calMonth = y + "-" + String(m + 1).padStart(2, "0"); state.calDay = null; renderCalendar(); }
  document.getElementById("cal-prev").addEventListener("click", function () { shiftMonth(-1); });
  document.getElementById("cal-next").addEventListener("click", function () { shiftMonth(1); });

  setOn("#filter button", "f", state.filter);
  var h = (location.hash || "").replace(/^#(tab-)?/, "");
  showTab(document.getElementById(h) && document.querySelector('#tabs button[data-t="' + h + '"]') ? h : "overview");
  renderAll();
  window.scrollTo(0, 0);

  // Daten alle 60 s im Hintergrund auffrischen (ohne Neuladen, Auswahl bleibt erhalten)
  setInterval(function () {
    fetch("api/state").then(function (r) { return r.json(); }).then(function (d) {
      D = d; OPEN = d.open || []; CLOSED = d.closed || []; renderAll();
    }).catch(function () {});
  }, 60000);
})();
