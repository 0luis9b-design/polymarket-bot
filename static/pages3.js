/* Zentrale – Paper-Trading: Konten und Lage, Wetten (Historie), eine Wette im Detail, Kalender.
   Alle drei Listen-Seiten teilen denselben Filter: Strategie und Bereich › Unterbereich › Detail (z. B. Sport › Fußball › Champions League).
   Jede Strategie hat je Thema ein eigenes Konto – Strategie + Bereich zusammen ist also genau ein Konto.
   Der Filter steht in der Adresse (#/wetten?cat=sports&sub=Fußball…), damit er beim Wechsel zwischen den Seiten erhalten bleibt. */
(function () {
  "use strict";
  var PB = window.PB, h = PB.h, F = PB.fmt, api = PB.api, UI = PB.ui;
  var OTHER = "Allgemein";
  PB.CAT.other = PB.CAT.other || "Sonstiges";
  var FILTER_KEYS = ["strat", "account", "cat", "sub", "detail"];
  var STATUS = [["", "Alle"], ["offen", "Offen"], ["gewonnen", "Gewonnen"], ["verloren", "Verloren"], ["verkauft", "Verkauft"], ["storniert", "Storniert"]];
  var LANE = { model: "eigenes Datenmodell", base: "Basisraten", rule: "feste Regel", arb: "Arbitrage", maker: "Limit-Order" };

  function qs(o) {
    return Object.keys(o).filter(function (k) { return o[k] != null && o[k] !== ""; }).map(function (k) { return k + "=" + encodeURIComponent(o[k]); }).join("&");
  }
  function pick(q, keys) { var o = {}; keys.forEach(function (k) { if (q[k]) o[k] = q[k]; }); return o; }
  function filterQs(q) { var s = qs(pick(q, FILTER_KEYS)); return s ? "?" + s : ""; }
  function signed(v, cls) {
    if (v == null || isNaN(v)) return PB.val(null);
    return h("span", { class: cls || (Math.abs(v) < 0.005 ? "dim" : v > 0 ? "pos" : "neg") }, F.susd(v));
  }
  function pathText(r) {
    return [PB.CAT[r.category] || r.category, r.sub, r.detail].filter(function (x) { return x && x !== OTHER; }).join(" › ");
  }
  /* Ja grün, Nein rot, alles andere (Mannschaften, Spieler) neutral – die Farbe ist nie die einzige Angabe, der Name steht dabei. */
  function sideChip(name) {
    var cls = /^(yes|ja)$/i.test(name || "") ? "yes" : /^(no|nein)$/i.test(name || "") ? "no" : "other";
    return h("span", { class: "side " + cls }, name || "?");
  }
  function hoursText(v) {
    if (v == null || isNaN(v)) return null;
    return v < 1 ? Math.max(1, Math.round(v * 60)) + " min" : v < 48 ? F.num(v, 1) + " h" : F.num(v / 24, 1) + " Tage";
  }
  var RES = { open: ["offen", "acc"], won: ["gewonnen", "ok"], lost: ["verloren", "bad"], void: ["storniert", null] };
  function resChip(b) {
    if (b.status === "sold") return PB.chip((b.pnl || 0) >= 0 ? "verkauft mit Gewinn" : "verkauft mit Verlust", (b.pnl || 0) >= 0 ? "ok" : "bad");
    var r = RES[b.status] || [b.status, null];
    return PB.chip(r[0], r[1]);
  }
  function pct1(v) { return v == null ? null : F.pct(v, 1); }

  // ---------------------------------------------------------------- Filter in Stufen
  function filterBar(d, r, extra) {
    var q = r.q, tree = d.tree || {};
    var set = function (changes) { PB.setQuery(Object.assign({ page: "", tag: "" }, changes)); };
    /* alle Themen anbieten (jedes ist ein eigenes Konto), auch ohne Wetten – sortiert nach Anzahl Wetten */
    var cats = Object.keys(tree);
    (d.topics || []).forEach(function (t) { if (cats.indexOf(t.id) < 0) cats.push(t.id); });
    var nOf = function (c) { return tree[c] ? tree[c].n : 0; };
    cats.sort(function (a, b) { return nOf(b) - nOf(a); });
    var parts = [
      UI.select(q.strat || "", [["", "Alle Strategien"]].concat((d.strategies || []).map(function (a) { return [a.id, a.name]; })),
                function (v) { set({ strat: v, account: "" }); }, "Strategie"),
      UI.select(q.cat || "", [["", "Alle Bereiche"]].concat(cats.map(function (c) { return [c, (PB.CAT[c] || c) + " (" + nOf(c) + ")"]; })),
                function (v) { set({ cat: v, sub: "", detail: "" }); }, "Bereich")
    ];
    var c = q.cat && tree[q.cat];
    if (c) {
      var subs = Object.keys(c.subs).sort(function (a, b) { return c.subs[b].n - c.subs[a].n; });
      parts.push(UI.select(q.sub || "", [["", "Alle Unterbereiche"]].concat(subs.map(function (s) { return [s, s + " (" + c.subs[s].n + ")"]; })),
                           function (v) { set({ sub: v, detail: "" }); }, "Unterbereich"));
      var sb = q.sub && c.subs[q.sub];
      if (sb) {
        var dets = Object.keys(sb.details).sort(function (a, b) { return sb.details[b] - sb.details[a]; });
        parts.push(UI.select(q.detail || "", [["", "Alle Einträge"]].concat(dets.map(function (x) { return [x, x + " (" + sb.details[x] + ")"]; })),
                             function (v) { set({ detail: v }); }, "Detail"));
      }
    } else if (q.cat) {
      parts.push(h("span", { class: "dim" }, "In diesem Bereich gibt es bei der gewählten Auswahl noch keine Wetten."));
    }
    var crumbs = null;
    if (q.cat || q.account || q.strat) {
      var nameOf = function (list, id) { return (list || []).filter(function (a) { return a.id === id; }).map(function (a) { return a.name; })[0] || id; };
      var trail = [q.account ? "Konto " + nameOf(d.accounts, q.account) : null, q.strat ? nameOf(d.strategies, q.strat) : null,
                   q.cat ? PB.CAT[q.cat] || q.cat : null, q.sub, q.detail].filter(Boolean);
      crumbs = h("div", { class: "crumbs" }, h("span", null, "Auswahl: ", h("b", null, trail.join(" › "))), " ",
                 h("button", { class: "btn small", type: "button", on: { click: function () { set({ account: "", strat: "", cat: "", sub: "", detail: "" }); } } }, "Filter zurücksetzen"));
    }
    return h("div", { class: "stack" }, h("div", { class: "filters" }, parts, extra || null), crumbs);
  }
  /* Kleine Navigation zwischen den drei Paper-Seiten, Filter bleibt erhalten */
  function subnav(cur, q) {
    var f = filterQs(q);
    return h("nav", { class: "subnav", "aria-label": "Paper-Trading" }, [["paper", "Konten und Lage"], ["wetten", "Wetten"], ["kalender", "Kalender"], ["auswertung", "Auswertung"]].map(function (x) {
      return h("a", { href: "#/" + x[0] + f, class: x[0] === cur ? "on" : null, "aria-current": x[0] === cur ? "page" : null }, x[1]);
    }));
  }
  function betsTable(rows, opt) {
    opt = opt || {};
    var cols = [
      { label: opt.closedCol ? "Abgeschlossen" : "Eröffnet", sort: opt.closedCol ? "geschlossen" : "eroeffnet",
        get: function (b) { return h("span", { class: "nowrap" }, F.dt(opt.closedCol ? b.closed_at : b.opened_at)); } },
      { label: "Markt", get: function (b) {
        return h("div", { class: "q" }, b.question, h("small", null, pathText(b) + (opt.noAccount ? "" : " — " + b.account_name)));
      } },
      { label: "Tipp", get: function (b) { return sideChip(b.side_name); } },
      { label: "Unsere W.", num: true, sort: "chance", get: function (b) { return pct1(b.own_p); } },
      { label: "Markt", num: true, cls: "hide-s", get: function (b) { return b.p_mid == null ? null : h("span", { title: b.p_mid_alt ? "Kaufkurs beim Einstieg (ältere Wette)" : "Markt-Wahrscheinlichkeit beim Einstieg" }, pct1(b.p_mid)); } },
      { label: "Einsatz", num: true, sort: "einsatz", cls: "hide-s", get: function (b) { return F.usd(b.cost); } },
      { label: "Ergebnis", get: function (b) { return resChip(b); } },
      { label: "G/V", num: true, sort: "gv", get: function (b) { return b.status === "open" ? (b.gv_offen != null ? h("span", { class: "dim", title: "noch offen, zum aktuellen Verkaufskurs" }, F.susd(b.gv_offen)) : "–") : signed(b.pnl); } }
    ];
    if (opt.short) cols = cols.filter(function (c) { return ["Einsatz"].indexOf(c.label) < 0; });
    var table = PB.table(cols, rows, {
      href: function (b) { return "#/wette/" + b.id; }, empty: opt.empty || "Für diese Auswahl gibt es keine Wetten.", next: opt.next,
      sort: opt.sort, onSort: opt.onSort
    });
    if (!rows || !rows.length) return table;
    /* Auf dem Handy: Karten statt Tabelle (die Marktfrage braucht die volle Breite) */
    var cards = h("div", { class: "betcards" }, rows.map(function (b) {
      var gv = b.status === "open" ? (b.gv_offen != null ? h("span", { class: "dim" }, F.susd(b.gv_offen)) : null) : signed(b.pnl);
      return h("a", { class: "betcard", href: "#/wette/" + b.id },
        h("div", { class: "bc-top" }, h("span", { class: "bc-q" }, b.question), gv),
        h("div", { class: "bc-meta" }, pathText(b) + (opt.noAccount ? "" : " — " + b.account_name)),
        h("div", { class: "bc-line" }, sideChip(b.side_name),
          h("span", null, "unsere ", h("b", null, pct1(b.own_p) || "–")),
          b.p_mid != null ? h("span", null, "Markt ", h("b", null, pct1(b.p_mid))) : null,
          resChip(b), h("span", { class: "dim2" }, F.dt(opt.closedCol ? b.closed_at : b.opened_at))));
    }));
    return h("div", null, h("div", { class: "bets-tw" }, table), cards);
  }

  // ================================================================ Strategie × Thema
  /* Jede Zelle ist ein Konto (1.000 $ Start). Gezeigt wird der Gewinn/Verlust seit Start; Antippen öffnet das Konto. */
  function accountPanels(d, q, resume) {
    var out = [], konten = d.konten || [];
    var byId = {};
    konten.forEach(function (a) { byId[a.strategy + "|" + a.topic] = a; });
    var strats = (d.strategien || []).filter(function (s) { return !q.strat || s.id === q.strat; });
    var topics = (d.topics || []).filter(function (t) { return !q.cat || t.id === q.cat; });
    var touched = function (a) { return a && (a.open_count || a.closed_count || Math.abs(a.pnl_total) >= 0.005); };
    var link = function (sid, tid) { return "#/paper?" + qs({ strat: sid, cat: tid }); };
    var cell = function (a, sid, tid) {
      if (!a) return h("td", { class: "num dim2", title: "Diese Strategie gibt es in diesem Thema nicht" }, "–");
      var v = a.pnl_total, cls = !touched(a) ? "dim2" : Math.abs(v) < 0.005 ? "dim" : v > 0 ? "pos" : "neg";
      var title = a.name + ": Wert " + F.usd(a.equity) + " · " + F.num(a.open_count) + " offen" + (a.settled ? " · Treffer " + a.wins + "/" + a.settled : "") + (a.paused ? " · pausiert" : "");
      return h("td", { class: "num" + (a.paused ? " paused" : "") },
               h("a", { href: link(sid, tid), class: cls, title: title }, touched(a) ? F.susd(v) : "·", a.paused ? h("span", { class: "pz", "aria-label": "pausiert" }, " ●") : null));
    };
    if (strats.length && topics.length) {
      var colSum = topics.map(function () { return 0; }), all = 0;
      var head = h("tr", null, h("th", { class: "sticky" }, "Strategie"), topics.map(function (t) { return h("th", { class: "num" }, h("a", { href: "#/paper?" + qs({ strat: q.strat, cat: t.id }) }, t.name)); }),
                   topics.length > 1 ? h("th", { class: "num" }, "Summe") : null);
      var body = strats.map(function (s) {
        var rowSum = 0;
        var tds = topics.map(function (t, i) {
          var a = byId[s.id + "|" + t.id];
          if (a) { rowSum += a.pnl_total; colSum[i] += a.pnl_total; }
          return cell(a, s.id, t.id);
        });
        all += rowSum;
        return h("tr", null, h("th", { class: "sticky", scope: "row" }, h("a", { href: "#/paper?" + qs({ strat: s.id, cat: q.cat }) }, s.name)), tds,
                 topics.length > 1 ? h("td", { class: "num" }, signed(rowSum)) : null);
      });
      if (strats.length > 1) {
        body.push(h("tr", { class: "sum" }, h("th", { class: "sticky", scope: "row" }, "Summe"), colSum.map(function (v) { return h("td", { class: "num" }, signed(v)); }),
                    topics.length > 1 ? h("td", { class: "num" }, signed(all)) : null));
      }
      out.push(PB.panel("Konten: Strategie × Thema", "Gewinn/Verlust seit Start je Konto (je 1.000 $ Spielgeld) – Feld antippen öffnet das Konto",
        [h("div", { class: "tw" }, h("table", { class: "matrix" }, h("thead", null, head), h("tbody", null, body))),
         h("p", { class: "note", style: { margin: "8px 0 0", padding: "0 14px 12px" } }, "„·“ = noch keine Wette, ", h("span", { class: "warn" }, "●"), " = Risikobremse aktiv (Konto pausiert). Krypto, Sport und Wetter haben ein eigenes Datenmodell; ",
           "alle anderen Themen wetten nur, wenn eine Basisrate statistisch gesichert vom Marktpreis abweicht. Arbitrage gibt es nur in Krypto, Sport und Wetter.")], true));
    }
    if (!q.strat && strats.length > 1) {
      out.push(PB.panel("Strategien", "alle Themen zusammen – Zeile antippen zeigt nur diese Strategie", PB.table([
        { label: "Strategie", get: function (s) { return h("div", { class: "q" }, s.name, h("small", null, s.desc)); } },
        { label: "seit Start", num: true, get: function (s) { return signed(s.pnl_total); } },
        { label: "offen", num: true, cls: "hide-s", get: function (s) { return F.num(s.open_count); } },
        { label: "Treffer", num: true, cls: "hide-s", get: function (s) { return s.settled ? F.num(s.wins) + "/" + F.num(s.settled) : "–"; } },
        { label: "Konten", num: true, get: function (s) { return h("span", null, F.num(s.konten), s.paused ? h("small", { class: "dim2" }, " (" + s.paused + " pausiert)") : null); } }
      ], strats, { href: function (s) { return "#/paper?" + qs({ strat: s.id, cat: q.cat }); } }), true));
    }
    var paused = konten.filter(function (a) { return a.paused && (!q.strat || a.strategy === q.strat) && (!q.cat || a.topic === q.cat); });
    if (paused.length) {
      out.push(PB.panel("Pausierte Konten", "Risikobremse: „−8 % heute“ endet von selbst am nächsten Tag, „−25 % seit Start“ nur mit „Fortsetzen“", PB.table([
        { label: "Konto", get: function (a) { return h("div", { class: "q" }, h("a", { href: "#/paper?" + qs({ strat: a.strategy, cat: a.topic }) }, a.name), h("small", null, a.paused)); } },
        { label: "Wert", num: true, cls: "hide-s", get: function (a) { return F.usd(a.equity); } },
        { label: "seit Start", num: true, get: function (a) { return signed(a.pnl_total); } },
        { label: "", get: function (a) { return resume(a); } }
      ], paused), true));
    }
    return out;
  }

  // ================================================================ Konten und Lage
  PB.pages.paper = {
    title: "Paper-Trading", sub: "Spielgeld: was der Bot gekauft hat, wie es steht und wo er gerade eine Chance sieht", live: true,
    load: function (r) { return api("/api/paper?" + qs(pick(r.q, FILTER_KEYS))); },
    render: function (d, r) {
      var q = r.q, k = d.kennzahlen || {}, out = [subnav("paper", q), filterBar(d, r)];
      var acct = d.konto, single = !!acct;
      var facts = [];
      if (single && k.kontowert != null) {
        facts.push(["Kontowert", F.usd(k.kontowert), h("small", null, " " + F.susd(k.kontowert - d.start) + " seit Start")]);
        facts.push(["Bargeld", F.usd(k.bargeld)]);
      }
      facts.push(["G/V abgeschlossen", signed(k.gv), h("small", null, k.geschlossen ? " aus " + F.num(k.geschlossen) + " Wetten" : "")]);
      facts.push(["G/V offen", signed(k.gv_offen), h("small", null, k.offen ? " " + F.num(k.offen) + " Wetten, " + F.usd(k.gebunden) + " gebunden" : "")]);
      facts.push(["Heute", signed(k.gv_heute)]);
      facts.push(["Trefferquote", k.trefferquote == null ? null : F.pct(k.trefferquote),
                  h("small", null, k.erwartet != null ? " erwartet " + F.pct(k.erwartet) : "")]);
      facts.push(["Rendite je Einsatz", k.rendite == null ? null : h("span", { class: k.rendite >= 0 ? "pos" : "neg" }, F.spct(k.rendite))]);
      var resume = function (a) {
        return h("button", { class: "btn small", type: "button", on: { click: function (e) {
          e.target.disabled = true;
          api("/api/account/" + encodeURIComponent(a.id) + "/resume", {}).then(function () { PB.toast(a.name + " läuft wieder."); PB.reload(); })
            .catch(function (err) { e.target.disabled = false; PB.toast(err.message, true); });
        } } }, "Fortsetzen");
      };
      out.push(PB.panel(single ? "Konto " + acct.name : "Gewählte Wetten", d.letzter_lauf ? "letzter Lauf " + F.ago(d.letzter_lauf) : null,
        [single ? h("p", { class: "note", style: { "margin-bottom": "10px" } }, acct.desc) : null,
         single && acct.paused ? h("div", { class: "callout", style: { "margin-bottom": "10px" } }, PB.chip("pausiert", "warn"), " ", acct.paused, " ", resume(acct)) : null,
         h("div", { class: "facts" }, facts.map(function (f) { return h("div", null, h("div", { class: "l" }, f[0]), h("div", { class: "v" }, PB.val(f[1] == null ? null : f[1]), f[2] || null)); })),
         h("p", { class: "note", style: { "margin-top": "10px" } }, "Trefferquote zählt nur entschiedene Wetten. „Erwartet“ ist der Durchschnitt der Wahrscheinlichkeit, die der Bot beim Einstieg für den eigenen Tipp berechnet hatte – liegt die echte Quote darunter, war er zu optimistisch.")]));

      var pts = (d.kurve.punkte || []).map(function (p) { return [new Date(p[0]).getTime(), p[1]]; });
      var isEq = d.kurve.art === "kontowert";
      out.push(PB.panel(isEq ? "Kontowert" : "Gewinn und Verlust, aufsummiert", isEq ? "stündlich, inklusive offener Wetten zum Verkaufskurs" : "abgeschlossene Wetten der Auswahl, nach Abschlusszeit",
        PB.line({ series: [{ name: isEq ? "Kontowert" : "G/V", color: PB.COL.modell, points: pts }], xType: "time",
                  yFmt: function (v) { return F.num(v, 0) + " $"; }, label: isEq ? "Kontowert" : "Aufsummierter Gewinn und Verlust",
                  ref: isEq && pts.length ? [[pts[0][0], d.start], [pts[pts.length - 1][0], d.start]] : (pts.length ? [[pts[0][0], 0], [pts[pts.length - 1][0], 0]] : null),
                  empty: "Noch keine abgeschlossene Wette in dieser Auswahl.", next: "Die Kurve beginnt mit der ersten entschiedenen oder verkauften Wette." })));

      var lvl = d.aufteilung.stufe, nextKey = { category: "cat", sub: "sub", detail: null }[lvl];
      out.push(PB.panel(lvl === "category" ? "Nach Bereich" : lvl === "sub" ? "Nach Unterbereich" : "Nach Detail", nextKey ? "Zeile antippen zum Eingrenzen" : null, PB.table([
        { label: "Name", get: function (x) { return lvl === "category" ? PB.cat(x.name) : x.name; } },
        { label: "Wetten", num: true, get: function (x) { return F.num(x.n); } },
        { label: "offen", num: true, get: function (x) { return F.num(x.offen); } },
        { label: "Treffer", num: true, cls: "hide-s", get: function (x) { return x.entschieden ? F.num(x.gewonnen) + " von " + F.num(x.entschieden) : "–"; } },
        { label: "Rendite", num: true, cls: "hide-s", get: function (x) { return x.einsatz ? h("span", { class: x.gv >= 0 ? "pos" : "neg" }, F.spct(x.gv / x.einsatz)) : "–"; } },
        { label: "G/V", num: true, get: function (x) { return signed(x.gv); } }
      ], d.aufteilung.rows, { href: nextKey ? function (x) { var o = {}; o[nextKey] = x.name; return "#/paper?" + qs(Object.assign(pick(q, FILTER_KEYS), o)); } : null,
                              empty: "Für diese Auswahl gibt es noch keine Wetten." }), true));

      if (!single) out = out.concat(accountPanels(d, q, resume));

      out.push(PB.panel("Offene Wetten", d.offen.length ? "nach Enddatum, die nächsten zuerst" : null,
        betsTable(d.offen, { empty: "Keine offenen Wetten in dieser Auswahl." }), true));
      if (d.gebote && d.gebote.length) {
        out.push(PB.panel("Offene Kaufgebote", "Limit-Orders, noch nicht gefüllt", PB.table([
          { label: "Gestellt", cls: "hide-s", get: function (o) { return F.dt(o.created_at); } },
          { label: "Markt", get: function (o) { return h("div", { class: "q" }, o.question, h("small", null, pathText(o) + " — " + o.account_name)); } },
          { label: "Tipp", get: function (o) { return sideChip(o.side_name); } },
          { label: "Gebot", num: true, get: function (o) { return F.pct(o.price, 1); } },
          { label: "Unsere W.", num: true, cls: "hide-s", get: function (o) { return F.pct(o.q, 1); } },
          { label: "Betrag", num: true, get: function (o) { return F.usd(o.stake); } }
        ], d.gebote), true));
      }
      out.push(PB.panel("Was der Bot gerade sieht", "aktueller Lauf, nach Vorsprung sortiert", PB.table([
        { label: "Markt", get: function (o) { return h("div", { class: "q" }, h("a", { href: "#/markt/" + encodeURIComponent(o.id) }, o.question), h("small", null, pathText(o) + " · " + (o.model || ""))); } },
        { label: "Tipp", get: function (o) { return sideChip(o.side_name); } },
        { label: "Bot (Ja)", num: true, cls: "hide-s", get: function (o) { return F.pct(o.own_p, 1); } },
        { label: "Markt (Ja)", num: true, cls: "hide-s", get: function (o) { return F.pct(o.market_p, 1); } },
        { label: "Vorsprung", num: true, get: function (o) { return h("span", { class: o.edge > 0 ? "pos" : "dim" }, F.pts(o.edge)); } },
        { label: "Entscheidung", cls: "hide-s", get: function (o) { return h("span", { class: "dim" }, (o.bought && o.bought.length ? "gekauft · " : "") + (o.status || "–")); } }
      ], d.chancen, { empty: "Im letzten Lauf gab es in dieser Auswahl nichts zu bewerten." }), true));
      return out;
    }
  };

  // ================================================================ Wetten (Historie)
  PB.pages.wetten = {
    title: "Wetten", sub: "Jede Spielgeld-Wette mit dem Stand beim Einstieg – antippen für alle Details", live: true,
    load: function (r) { return api("/api/bets?" + qs(pick(r.q, FILTER_KEYS.concat(["status", "q", "von", "bis", "sort", "dir", "page", "spur"])))); },
    render: function (d, r) {
      var q = r.q, s = d.summe || {};
      var dates = h("span", { class: "row" },
        h("label", { class: "dim2" }, "von ", h("input", { type: "date", value: q.von || "", "aria-label": "Eröffnet ab", on: { change: function (e) { PB.setQuery({ von: e.target.value, page: "" }); } } })),
        h("label", { class: "dim2" }, "bis ", h("input", { type: "date", value: q.bis || "", "aria-label": "Eröffnet bis", on: { change: function (e) { PB.setQuery({ bis: e.target.value, page: "" }); } } })));
      var out = [subnav("wetten", q), filterBar(d, r),
        h("div", { class: "filters" }, h("span", { class: "hide-s" }, UI.seg(q.status || "", STATUS, function (v) { PB.setQuery({ status: v, page: "" }); })),
          h("span", { class: "show-s" }, UI.select(q.status || "", STATUS.map(function (x) { return [x[0], x[0] ? x[1] : "Alle Ergebnisse"]; }),
                                                   function (v) { PB.setQuery({ status: v, page: "" }); }, "Ergebnis")),
          UI.select((q.sort || "eroeffnet") + ":" + (q.dir || "desc"), [["eroeffnet:desc", "Neueste zuerst"], ["eroeffnet:asc", "Älteste zuerst"],
            ["geschlossen:desc", "Zuletzt abgeschlossen"], ["gv:desc", "Höchster Gewinn"], ["gv:asc", "Höchster Verlust"], ["einsatz:desc", "Größter Einsatz"],
            ["chance:desc", "Höchste eigene Wahrscheinlichkeit"]], function (v) { var x = v.split(":"); PB.setQuery({ sort: x[0], dir: x[1], page: "" }); }, "Sortierung"),
          UI.select(q.spur || "", [["", "Alle Arten"], ["model", "Eigenes Datenmodell"], ["base", "Basisraten"], ["rule", "Feste Regel"], ["maker", "Limit-Order"], ["arb", "Arbitrage"]],
                    function (v) { PB.setQuery({ spur: v, page: "" }); }, "Art der Entscheidung"),
          UI.search(q.q, "Marktfrage suchen"), dates)];
      out.push(h("div", { class: "sumline" },
        h("span", null, h("b", null, F.num(s.n || 0)), " Wetten"),
        h("span", null, "Einsatz ", h("b", null, F.usd(s.einsatz || 0))),
        h("span", null, "G/V abgeschlossen ", signed(s.gv || 0)),
        s.offen ? h("span", null, F.num(s.offen) + " offen ", signed(s.gv_offen || 0, "dim")) : null,
        (s.gewonnen || s.verloren) ? h("span", null, F.num(s.gewonnen) + " gewonnen, " + F.num(s.verloren) + " verloren") : null,
        s.n ? h("a", { href: "/api/bets.csv?" + qs(pick(q, FILTER_KEYS.concat(["status", "q", "von", "bis", "spur"]))), download: "",
                       title: "Alle Wetten dieser Auswahl als Tabelle (öffnet in Excel)" }, "Als Tabelle herunterladen") : null));
      var sortKey = q.sort || "eroeffnet", dir = q.dir || "desc";
      out.push(PB.panel("Historie", "neueste zuerst", [betsTable(d.rows, {
        sort: { key: sortKey, dir: dir }, closedCol: sortKey === "geschlossen",
        onSort: function (k) { PB.setQuery({ sort: k, dir: k === sortKey && dir === "desc" ? "asc" : "desc", page: "" }); },
        empty: "Für diese Auswahl gibt es keine Wetten.", next: q.status || q.q || q.von || q.bis ? "Filter lockern, um mehr zu sehen." : null
      }), d.total > d.per ? UI.pager(d) : null], true));
      return out;
    }
  };

  // ================================================================ Eine Wette
  PB.pages.wette = {
    title: "Wette", sub: "Alles, was beim Einstieg bekannt war – und wie es ausging",
    load: function (r) { return api("/api/bet/" + encodeURIComponent(r.arg || "")); },
    render: function (d) {
      var b = d.bet, e = b.entry || {}, open = b.status === "open", out = [];
      out.push(h("a", { class: "back", href: "#/wetten", on: { click: function (ev) { if (history.length > 1) { ev.preventDefault(); history.back(); } } } }, "← zurück"));
      var won = b.status === "won" || (b.status === "sold" && (b.pnl || 0) >= 0);
      out.push(PB.panel(b.question, null, [
        h("div", { class: "checks", style: { "margin-bottom": "12px" } }, PB.chip(pathText(b) || "ohne Einordnung"), PB.chip(b.account_name, "acc"),
          PB.chip(LANE[b.lane || "model"] || b.lane), resChip(b)),
        h("div", { class: "facts" },
          h("div", null, h("div", { class: "l" }, "Tipp"), h("div", { class: "v" }, sideChip(b.side_name))),
          h("div", null, h("div", { class: "l" }, "Unsere Wahrscheinlichkeit"), h("div", { class: "v" }, PB.val(pct1(b.own_p)), h("small", null, " beim Einstieg"))),
          h("div", null, h("div", { class: "l" }, "Markt"), h("div", { class: "v" }, PB.val(pct1(b.p_mid)), h("small", null, b.p_mid_alt ? " Kaufkurs" : " beim Einstieg"))),
          h("div", null, h("div", { class: "l" }, "Vorsprung"), h("div", { class: "v" }, PB.val(b.edge == null ? null : F.pts(b.edge)))),
          h("div", null, h("div", { class: "l" }, "Einsatz"), h("div", { class: "v" }, F.usd(b.cost), h("small", null, " " + F.num(b.shares, 1) + " Anteile zu " + F.pct(b.price, 1)))),
          h("div", null, h("div", { class: "l" }, open ? "G/V bisher" : "Ergebnis"), h("div", { class: "v" }, open ? signed(b.gv_offen, "dim") : signed(b.pnl)))),
        !open && b.own_p != null ? h("p", { class: "note", style: { "margin-top": "12px" } },
          "Beim Einstieg rechnete der Bot mit " + F.pct(b.own_p) + " für „" + b.side_name + "“. " +
          (b.status === "void" ? "Der Markt wurde storniert." : b.status === "sold" ? "Die Wette wurde vor der Entscheidung verkauft." : won ? "Der Tipp ist eingetreten." : "Der Tipp ist nicht eingetreten.")) : null
      ]));

      var entry = [
        ["Zeitpunkt", F.dt(b.opened_at)],
        ["Konto", b.account_name],
        ["Entschieden nach", LANE[b.lane || "model"] || b.lane],
        ["Modell", e.modell ? e.modell + (e.modell_id ? " (" + e.modell_id + ")" : "") : b.model],
        ["Begründung", e.begruendung || b.info],
        ["Unsere Wahrscheinlichkeit für den Tipp", pct1(e.p_unser_ausgang != null ? e.p_unser_ausgang : b.own_p)],
        ["Markt-Wahrscheinlichkeit für den Tipp", pct1(e.p_unser_ausgang_markt != null ? e.p_unser_ausgang_markt : b.p_mid)],
        e.p_ja_modell_roh != null && e.p_ja_modell != null && Math.abs(e.p_ja_modell_roh - e.p_ja_modell) > 0.0005 ?
          ["Modell vor Mischung mit dem Markt (Ja)", pct1(e.p_ja_modell_roh)] : null,
        ["Unsicherheit", (e.unsicherheit != null ? e.unsicherheit : b.uncertainty) != null ? "± " + F.num((e.unsicherheit != null ? e.unsicherheit : b.uncertainty) * 100, 1) + " Pkt" : null],
        e.z != null ? ["Abweichung in Standardfehlern", F.num(e.z, 2)] : null,
        ["Kaufkurs / Verkaufskurs", e.kaufkurs != null ? F.pct(e.kaufkurs, 1) + " / " + F.pct(e.verkaufskurs, 1) : F.pct(b.price, 1)],
        ["Gebühr je Anteil", e.gebuehr_je_anteil != null ? F.num(e.gebuehr_je_anteil * 100, 2) + " ¢" : null],
        ["Vorsprung nach Gebühr / Mindestens nötig", b.edge != null ? F.pts(b.edge) + (e.mindest_vorsprung != null && e.mindest_vorsprung < 0.9 ? " / " + F.pts(e.mindest_vorsprung) : "") : null],
        ["Restlaufzeit beim Einstieg", hoursText(e.restlaufzeit_h != null ? e.restlaufzeit_h : b.hours_left)],
        ["Spanne im Orderbuch", (e.spanne != null ? e.spanne : b.spread) != null ? F.num((e.spanne != null ? e.spanne : b.spread) * 100, 1) + " Pkt" : null],
        ["Liquidität / 24-h-Volumen", e.liquiditaet != null ? F.big(e.liquiditaet) + " $ / " + F.big(e.volumen_24h || 0) + " $" : null],
        ["Sicherheit → Einsatz", e.sicherheit != null ? F.pct(e.sicherheit) + " → " + F.pct(e.einsatz_anteil, 1) + " von " + F.usd(e.kontowert) : null],
        ["Alter der Daten", e.datenalter_s != null ? hoursText(e.datenalter_s / 3600) : null],
        ["Quellen", e.quellen && e.quellen.length ? e.quellen.join(" · ") : null],
        ["Ereignis", e.ereignis || null],
        ["Füllung", e.fuellung || b.fill_note]
      ];
      out.push(h("div", { class: "grid two" },
        PB.panel("Beim Einstieg", b.entry ? null : "ältere Wette: nur ein Teil wurde damals gespeichert", [
          PB.kv(entry.filter(function (x) { return x && x[1] != null && x[1] !== ""; }), true),
          e.pruefung && e.pruefung.length ? h("div", { class: "checks", style: { "margin-top": "12px" } }, h("span", { class: "dim2" }, "Prüfung:"),
            e.pruefung.map(function (c) { return PB.chip(({ wert: "Wert gültig", ende: "läuft noch", frische: "Daten frisch", abweichung: "Abweichung plausibel", summe: "Summe stimmig" }[c[0]] || c[0]), c[1] ? "ok" : "bad"); })) : null,
          e.varianten && Object.keys(e.varianten).length > 1 ? h("details", { class: "tbl" }, h("summary", null, "Alle Modell-Varianten zu diesem Zeitpunkt (Ja)"),
            PB.kv(Object.keys(e.varianten).map(function (k) { return [k, F.pct(e.varianten[k], 1)]; }))) : null
        ]),
        PB.panel(open ? "Stand jetzt" : "Ausgang", null, PB.kv(open ? [
          ["Endet", b.end_date ? F.dt(b.end_date) + " (" + F.until(b.end_date) + ")" : null],
          ["Aktueller Verkaufskurs", b.mark != null ? F.pct(b.mark, 1) : null],
          ["Wert jetzt", b.wert != null ? F.usd(b.wert) : null],
          ["G/V, wenn jetzt verkauft", b.gv_offen != null ? signed(b.gv_offen) : null],
          ["Bot jetzt (für den Tipp)", d.live ? F.pct(b.side === 0 ? d.live.own_p : 1 - d.live.own_p, 1) : "wird im aktuellen Lauf nicht bewertet"],
          ["Markt jetzt (für den Tipp)", d.live ? F.pct(b.side === 0 ? d.live.market_p : 1 - d.live.market_p, 1) : null]
        ] : [
          ["Abgeschlossen", F.dt(b.closed_at)],
          ["Wie", b.exit_reason || ({ won: "Markt entschieden – Tipp eingetreten", lost: "Markt entschieden – Tipp nicht eingetreten", sold: "vorzeitig verkauft", void: "storniert" }[b.status])],
          ["Auszahlung", b.payout != null ? F.usd(b.payout) : null],
          b.exit_price != null ? ["Verkaufskurs", F.pct(b.exit_price, 1)] : null,
          ["Gewinn / Verlust", signed(b.pnl)],
          ["Rendite auf den Einsatz", b.cost ? h("span", { class: (b.pnl || 0) >= 0 ? "pos" : "neg" }, F.spct((b.pnl || 0) / b.cost)) : null],
          b.close_p != null ? ["Schlusskurs-Vergleich (Sport)", F.pts(b.close_p - b.price) + " gegenüber dem Kaufkurs"] : null,
          d.outcome ? ["Ergebnis laut Polymarket", d.outcome.outcome === 1 ? "Ja" : "Nein"] : null
        ].filter(Boolean)))));

      /* Nur die Lebenszeit der Wette (6 h davor bis 6 h nach dem Ende) – sonst schrumpft sie im Bild auf einen Punkt */
      var ent = new Date(b.opened_at).getTime(), stop = b.closed_at ? new Date(b.closed_at).getTime() : Date.now();
      var inWin = function (t) { return t >= ent - 6 * 3600e3 && t <= stop + 6 * 3600e3; };
      var series = [{ name: "Markt (für den Tipp)", color: PB.COL.markt,
                      points: d.prices.map(function (p) { return [new Date(p.ts + ":00Z").getTime(), p.p]; }).filter(function (p) { return inWin(p[0]); }) },
                    { name: "Unsere Prognose", color: PB.COL.modell, dotsOnly: true,
                      points: d.forecasts.filter(function (f) { return f.champion; }).map(function (f) { return [new Date(f.created_at).getTime(), f.p_side]; })
                        .filter(function (p) { return inWin(p[0]); }) },
                    { name: "Einstieg", color: "var(--warn)", dotsOnly: true, points: b.own_p != null && !isNaN(ent) ? [[ent, b.own_p]] : [] }];
      out.push(PB.panel("Verlauf für „" + b.side_name + "“", "Marktkurs stündlich gespeichert; Punkte = eigene Prognosen", PB.line({
        series: series, xType: "time", yMin: 0, yMax: 1, yFmt: function (v) { return F.pct(v); }, label: "Verlauf der Wahrscheinlichkeit für den Tipp",
        empty: "Für diesen Markt gibt es keinen gespeicherten Verlauf.", next: "Den Verlauf gibt es für Märkte, die nach dem 14.11.2026 im Katalog waren."
      })));
      if (d.forecasts.length) {
        out.push(PB.panel("Gespeicherte Prognosen zu diesem Markt", "unveränderlich, je Zeithorizont die erste", PB.table([
          { label: "Erstellt", get: function (f) { return F.dt(f.created_at); } },
          { label: "Modell", get: function (f) { return h("span", null, f.model + "@" + f.version, f.champion ? " " : "", f.champion ? PB.chip("aktiv", "acc") : null); } },
          { label: "Horizont", cls: "hide-s", get: function (f) { return f.horizon; } },
          { label: "Unsere (Tipp)", num: true, get: function (f) { return F.pct(f.p_side, 1); } },
          { label: "Markt (Tipp)", num: true, get: function (f) { return F.pct(f.market_side, 1); } },
          { label: "Prüfung", cls: "hide-s", get: function (f) { return !f.champion ? "–" : f.valid ? PB.chip("bestanden", "ok") : PB.chip("gesperrt", "bad"); } }
        ], d.forecasts), true));
      }
      if (d.others.length) {
        out.push(PB.panel("Andere Konten auf diesem Markt", null, PB.table([
          { label: "Konto", get: function (x) { return x.account_name; } },
          { label: "Tipp", get: function (x) { return sideChip(x.side_name); } },
          { label: "Unsere W.", num: true, get: function (x) { return pct1(x.own_p); } },
          { label: "Einsatz", num: true, get: function (x) { return F.usd(x.cost); } },
          { label: "Ergebnis", get: function (x) { return resChip(x); } },
          { label: "G/V", num: true, get: function (x) { return x.status === "open" ? "–" : signed(x.pnl); } }
        ], d.others, { href: function (x) { return "#/wette/" + x.id; } }), true));
      }
      var links = d.market ? [h("a", { href: "#/markt/" + encodeURIComponent(b.market_id) }, "Markt im Katalog")] : [];
      if (b.event_slug) links.push(h("a", { href: "https://polymarket.com/event/" + encodeURIComponent(b.event_slug), target: "_blank", rel: "noopener noreferrer" }, "Bei Polymarket ansehen"));
      out.push(h("div", { class: "row" }, links));
      return out;
    }
  };

  // ================================================================ Kalender
  var WD = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];
  function monthName(ym) {
    var p = ym.split("-");
    return new Date(Date.UTC(+p[0], +p[1] - 1, 15)).toLocaleDateString("de-DE", { month: "long", year: "numeric", timeZone: "UTC" });
  }
  function shiftMonth(ym, n) {
    var p = ym.split("-"), d = new Date(Date.UTC(+p[0], +p[1] - 1 + n, 1));
    return d.getUTCFullYear() + "-" + String(d.getUTCMonth() + 1).padStart(2, "0");
  }
  function compact(v) {
    var a = Math.abs(v);
    return (v >= 0 ? "+" : "−") + (a >= 100 ? F.num(a, 0) : F.num(a, a >= 10 ? 1 : 2));
  }
  // ================================================================ Auswertung nach Quelle
  /* Wo verdient der Bot wirklich? Abgeschlossene Wetten nach Liga/Stadt/Coin, Art der Wette, Seite, Grund und Gewinnchance.
     Entscheidend ist „gewonnen“ gegen „Preis“: Nur wenn die Wetten öfter gewinnen, als der Preis beim Kauf sagte, steckt ein
     echter Vorsprung dahinter. */
  PB.pages.auswertung = {
    title: "Auswertung", sub: "Wo verdient der Bot wirklich? Abgeschlossene Wetten nach Liga, Stadt, Art der Wette und Grund", live: true,
    load: function (r) { return api("/api/breakdown?" + qs(pick(r.q, FILTER_KEYS))); },
    render: function (d, r) {
      var q = r.q, s = d.summe, out = [subnav("auswertung", q), filterBar(d, r)];
      var pc = function (v) { return v == null ? "–" : F.pct(v); };
      out.push(PB.panel("Gewählte Wetten", null, [h("div", { class: "sumline" },
        h("span", null, "G/V ", signed(s.gv)),
        h("span", null, F.num(s.n) + " mit bekanntem Ausgang, davon " + F.num(s.gewonnen) + " für uns ausgegangen" + (s.verkauft ? " · " + F.num(s.verkauft) + " vorher verkauft" + (s.verkauft_mit_ausgang ? " (" + F.num(s.verkauft_mit_ausgang) + " davon mitgezählt)" : "") : "")),
        h("span", null, "Treffer " + pc(s.treffer) + " · erwartet " + pc(s.erwartet) + " · Preis " + pc(s.preis)),
        h("span", null, "Rendite " + (s.roi == null ? "–" : F.pct(s.roi, 1)) + " auf " + F.usd(s.einsatz) + " Einsatz")),
        h("p", { class: "note", style: { margin: "8px 0 0" } }, "„Preis“ = was der Markt beim Kauf für unsere Seite verlangte, „erwartet“ = was der Bot selbst erwartete. ",
          "Ein echter Vorsprung zeigt sich nur, wenn „Treffer“ über „Preis“ liegt – und das bei genug Wetten (ab etwa 30). ",
          "„Treffer“ zählt, wie oft der Markt für die gekaufte Seite ausging – auch bei Wetten, die vorher verkauft wurden, sobald ihr Ausgang bekannt ist.")]));
      ["quelle", "wettart", "seite", "grund", "chance"].forEach(function (k) {
        var g = d.gruppen[k];
        if (!g || !g.zeilen.length) return;
        out.push(PB.panel(g.titel, F.num(g.zeilen.length) + (g.zeilen.length === 1 ? " Gruppe" : " Gruppen"), PB.table([
          { label: g.titel, get: function (x) { return h("div", { class: "q" }, x.name); } },
          { label: "Wetten", num: true, get: function (x) { var rest = x.verkauft - (x.verkauft_mit_ausgang || 0); return h("span", null, F.num(x.n), rest > 0 ? h("small", { class: "dim2" }, " +" + rest + " verk.") : null); } },
          { label: "Treffer", num: true, get: function (x) { return h("span", { class: x.treffer != null && x.preis != null && x.n >= 30 ? (x.treffer > x.preis ? "pos" : "neg") : null }, pc(x.treffer)); } },
          { label: "Preis", num: true, cls: "hide-s", get: function (x) { return pc(x.preis); } },
          { label: "erwartet", num: true, cls: "hide-s", get: function (x) { return pc(x.erwartet); } },
          { label: "G/V", num: true, get: function (x) { return signed(x.gv); } },
          { label: "Rendite", num: true, cls: "hide-s", get: function (x) { return x.roi == null ? "–" : F.pct(x.roi, 1); } }
        ], g.zeilen.slice(0, k === "quelle" ? 40 : 30)), true));
      });
      if (!s.n && !s.verkauft) out.push(PB.panel("Keine Wetten", null, PB.empty("Für diese Auswahl gibt es noch keine abgeschlossene Wette.")));
      var rb = d.rueckblick || {};
      if (rb.bereiche && rb.bereiche.length) {
        var gesperrt = rb.bereiche.filter(function (x) { return x.gesperrt; }).length;
        out.push(PB.panel("Rückblick: Hätten Modell-Wetten Geld gebracht?", gesperrt ? gesperrt + " gesperrt" : "nichts gesperrt", [
          h("p", { class: "note", style: { margin: "0 0 8px" } },
            "Alle entschiedenen Prognosen der letzten " + (rb.tage || 30) + " Tage (unabhängig vom Filter oben), als wäre jedes Mal auf die Seite des Modells " +
            "zum damaligen Marktpreis gewettet worden. Liegt „Treffer“ gesichert unter „Preis“, wettet der Bot in diesem Bereich nicht mehr – " +
            "er rechnet aber weiter mit und gibt den Bereich von selbst wieder frei, wenn er besser wird." + (rb.aktiv ? "" : " (Im Moment ausgeschaltet – nur Anzeige.)")),
          PB.table([
            { label: "Bereich", get: function (x) {
              return h("div", { class: "q" }, x.thema_name + (x.wettart === "alle Wettarten" ? "" : " · " + x.wettart),
                h("small", { class: "dim2", style: { display: "block" } }, "„" + x.seite + "“ · " + x.abweichung + " Abweichung"));
            } },
            { label: "Ereignisse", num: true, cls: "hide-s", get: function (x) { return F.num(x.ereignisse); } },
            { label: "Treffer", num: true, get: function (x) { return h("span", { class: x.ereignisse >= 20 ? (x.gewonnen > x.preis ? "pos" : "neg") : null }, pc(x.gewonnen)); } },
            { label: "Preis", num: true, get: function (x) { return pc(x.preis); } },
            { label: "Rendite", num: true, cls: "hide-s", get: function (x) { return x.rendite == null ? "–" : F.pct(x.rendite, 0); } },
            { label: "Stand", get: function (x) { return h("span", { class: x.gesperrt ? "neg" : "dim2" }, x.gesperrt ? "gesperrt" : "offen"); } }
          ], rb.bereiche.slice(0, 60))], true));
      }
      return out;
    }
  };

  PB.pages.kalender = {
    title: "Kalender", sub: "Gewinn und Verlust je Tag – gezählt am Tag, an dem eine Wette entschieden oder verkauft wurde", live: true,
    load: function (r) { return api("/api/calendar?" + qs(pick(r.q, FILTER_KEYS.concat(["monat", "tag"])))); },
    render: function (d, r) {
      var q = r.q, s = d.summe, out = [subnav("kalender", q), filterBar(d, r)];
      var byDay = {};
      d.tage.forEach(function (t) { byDay[t.tag] = t; });
      var max = 0;
      d.tage.forEach(function (t) { if (t.n) max = Math.max(max, Math.abs(t.gv)); });
      var p = d.monat.split("-"), first = new Date(Date.UTC(+p[0], +p[1] - 1, 1)), days = new Date(Date.UTC(+p[0], +p[1], 0)).getUTCDate();
      var lead = (first.getUTCDay() + 6) % 7, today = new Date().toLocaleDateString("en-CA", { timeZone: "Europe/Berlin" });
      var cells = WD.map(function (w) { return h("div", { class: "wd" }, w); });
      for (var i = 0; i < lead; i++) cells.push(h("div", { class: "d blank", "aria-hidden": "true" }));
      for (var dd = 1; dd <= days; dd++) {
        var key = d.monat + "-" + String(dd).padStart(2, "0"), t = byDay[key];
        var cls = "d" + (t && t.n ? (t.gv > 0.004 ? " up" : t.gv < -0.004 ? " down" : " flat") : "") + (key === d.tag ? " sel" : "") + (key === today ? " today" : "");
        var style = null;
        if (t && t.n && max > 0 && Math.abs(t.gv) > 0.004) {
          var alpha = (0.12 + 0.5 * Math.min(1, Math.abs(t.gv) / max)).toFixed(2);
          style = { background: t.gv > 0 ? "rgba(47,208,127," + alpha + ")" : "rgba(255,93,108," + alpha + ")" };
        }
        var label = dd + ". " + monthName(d.monat) + (t && t.n ? ": " + F.susd(t.gv) + " aus " + t.n + " Wetten" : ": keine abgeschlossene Wette") + (t && t.eroeffnet ? ", " + t.eroeffnet + " eröffnet" : "");
        cells.push(h("button", { type: "button", class: cls, style: style, "aria-label": label, title: label, "aria-pressed": key === d.tag ? "true" : "false",
          on: { click: (function (k) { return function () { PB.setQuery({ tag: k === d.tag ? "" : k }); }; })(key) } },
          h("span", { class: "n" }, dd),
          t && t.n ? h("span", { class: "v" }, compact(t.gv)) : null,
          t && (t.n || t.eroeffnet) ? h("span", { class: "c" }, t.n ? h("span", null, t.n + "×") : null,
            t.eroeffnet ? h("span", { class: "o" }, (t.n ? " · " : "") + "+" + t.eroeffnet) : null) : null));
      }
      var nav = h("div", { class: "calnav" },
        h("button", { class: "btn small", type: "button", "aria-label": "Vormonat", on: { click: function () { PB.setQuery({ monat: shiftMonth(d.monat, -1), tag: "" }); } } }, "‹"),
        h("h2", null, monthName(d.monat)),
        h("button", { class: "btn small", type: "button", "aria-label": "Folgemonat", on: { click: function () { PB.setQuery({ monat: shiftMonth(d.monat, 1), tag: "" }); } } }, "›"),
        q.monat ? h("button", { class: "btn small", type: "button", on: { click: function () { PB.setQuery({ monat: "", tag: "" }); } } }, "Heute") : null);
      out.push(PB.panel("Monat", null, [nav,
        h("div", { class: "sumline", style: { padding: "4px 0 10px" } },
          h("span", null, "G/V im Monat ", signed(s.gv)),
          h("span", null, F.num(s.n) + " Wetten abgeschlossen (" + F.num(s.gewonnen) + " gewonnen, " + F.num(s.verloren) + " verloren)"),
          h("span", null, F.num(s.plus_tage) + " Plus-Tage, " + F.num(s.minus_tage) + " Minus-Tage"),
          h("span", null, F.num(s.eroeffnet) + " Wetten eröffnet")),
        h("div", { class: "cal", role: "group", "aria-label": "Kalender " + monthName(d.monat) }, cells),
        h("p", { class: "note", style: { "margin-top": "10px" } }, "Je Tag: Gewinn oder Verlust in Dollar, darunter die Zahl abgeschlossener Wetten und (+) neu eröffneter. Je kräftiger die Farbe, desto größer der Betrag im Vergleich zum stärksten Tag des Monats. Tag antippen für die Wetten.")]));
      if (d.tag) {
        var t = byDay[d.tag] || { gv: 0, n: 0 };
        out.push(PB.panel(new Date(d.tag + "T12:00:00Z").toLocaleDateString("de-DE", { weekday: "long", day: "numeric", month: "long", timeZone: "UTC" }),
          t.n ? "G/V " + F.susd(t.gv) : null,
          [h("h3", { style: { padding: "0 14px" } }, "Abgeschlossen an diesem Tag"),
           betsTable(d.geschlossen, { closedCol: true, empty: "An diesem Tag wurde keine Wette entschieden oder verkauft." }),
           h("h3", { style: { padding: "0 14px" } }, "Eröffnet an diesem Tag"),
           betsTable(d.eroeffnet, { empty: "An diesem Tag wurde keine Wette eröffnet." })], true));
      }
      if (!d.erster) out.push(PB.empty("Für diese Auswahl gibt es noch keine Wetten."));
      return out;
    }
  };
})();
