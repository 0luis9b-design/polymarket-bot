/* Zentrale – Seiten: Übersicht, Märkte, Markt-Detail, Prognosen, KI-Zentrale */
(function () {
  "use strict";
  var PB = window.PB, h = PB.h, F = PB.fmt, api = PB.api;
  var FLAG = { kein_orderbuch: "kein Orderbuch", spanne_gross: "große Spanne", wenig_liquiditaet: "wenig Liquidität", kein_ende: "kein Enddatum", abgelaufen: "abgelaufen" };
  var STATE = { ok: "bereit", fehler: "Fehler", wartet: "wartet", "läuft": "läuft" };

  function qs(o) {
    return Object.keys(o).filter(function (k) { return o[k] != null && o[k] !== ""; }).map(function (k) { return k + "=" + encodeURIComponent(o[k]); }).join("&");
  }
  function move(v) { return v == null ? null : h("span", { class: Math.abs(v) < 0.005 ? "dim" : v > 0 ? "pos" : "neg" }, F.pts(v)); }
  function pager(d) {
    var pages = Math.max(1, Math.ceil(d.total / d.per));
    return h("div", { class: "pager" }, h("span", null, F.num(d.total) + " Einträge, Seite " + d.page + " von " + pages),
             h("span", { class: "row" },
               h("button", { class: "btn small", disabled: d.page <= 1, on: { click: function () { PB.setQuery({ page: d.page - 1 }); } } }, "Zurück"),
               h("button", { class: "btn small", disabled: d.page >= pages, on: { click: function () { PB.setQuery({ page: d.page + 1 }); } } }, "Weiter")));
  }
  function search(q, placeholder) {
    var t = null;
    return h("input", { type: "search", value: q || "", placeholder: placeholder, "aria-label": placeholder,
                        on: { input: function (e) { clearTimeout(t); var v = e.target.value; t = setTimeout(function () { PB.setQuery({ q: v, page: "" }); }, 350); } } });
  }
  function select(value, options, onChange, label) {
    return h("select", { "aria-label": label, on: { change: function (e) { onChange(e.target.value); } } },
             options.map(function (o) { return h("option", { value: o[0], selected: String(o[0]) === String(value || "") }, o[1]); }));
  }
  function seg(value, options, onChange) {
    return h("div", { class: "seg", role: "group" }, options.map(function (o) {
      return h("button", { type: "button", class: o[0] === value ? "on" : null, "aria-pressed": o[0] === value ? "true" : "false", on: { click: function () { onChange(o[0]); } } }, o[1]);
    }));
  }
  PB.ui = { select: select, seg: seg, search: search, pager: pager, move: move };

  // ================================================================ Übersicht
  PB.pages[""] = {
    title: "Übersicht", sub: "Sind die eigenen Prognosen genauer als der Markt – und läuft alles?", live: true,
    load: function () { return api("/api/overview"); },
    render: function (d) {
      var sy = d.system, c = d.counts, out = [];
      (d.warnings || []).slice(0, 3).forEach(function (w) {
        out.push(h("div", { class: "callout" + (w.level === "kritisch" ? " bad" : "") }, h("b", null, w.title + " "), h("span", { class: "dim" }, F.ago(w.ts)),
                   w.detail ? h("div", { class: "dim" }, w.detail) : null));
      });
      var rows = PB.MODELED.map(function (cat) {
        var m = (d.quality || []).filter(function (x) { return x.category === cat; })[0] || {};
        return { label: PB.CAT[cat], sub: m.model || null, skill: m.skill, z: m.z, n: m.n, brier: m.brier, brier_market: m.brier_market };
      });
      var any = rows.some(function (r) { return r.n; });
      out.push(PB.panel("Modell gegen Markt", d.quality_stand ? "Stand " + F.dt(d.quality_stand) : "",
        any ? [PB.duel(rows), h("p", { class: "note", style: { "margin-top": "6px" } },
                 "Gemessen am Brier-Score aller entschiedenen Prognosen, verglichen mit dem Marktpreis zum selben Zeitpunkt. Ein hohler Balken heißt: Der Unterschied kann noch Zufall sein. ",
                 h("a", { href: "#/performance" }, "Zur Auswertung"))]
            : PB.empty("Noch ist keine Prognose entschieden, die seit dem Start der neuen Prognose-Historie gespeichert wurde.",
                       "Die ersten Ergebnisse erscheinen, sobald Märkte enden – bei Wetter und Krypto meist nach ein bis zwei Tagen.")));

      var left = PB.panel("Meldungen", null, [PB.feed(d.events), h("div", { style: { padding: "8px 14px 6px" } }, h("a", { href: "#/system" }, "Alle Warnungen und das Fehlerprotokoll"))], true);
      var res = d.resources;
      var right = h("div", { class: "grid" },
        PB.panel("Betrieb", null, PB.kv([
          ["Letzter Lauf", sy.last_cycle ? F.ago(sy.last_cycle) + (sy.cycle_duration != null ? ", Dauer " + F.dur(sy.cycle_duration) : "") : null],
          ["Takt", "alle " + F.num(sy.interval_min) + " min"],
          ["Agenten", d.agents.filter(function (a) { return a.state !== "fehler"; }).length + " von " + d.agents.length + " ohne Fehler"],
          ["Arbeitsspeicher", res && res.rss_mb != null ? F.num(res.rss_mb) + " MB" : null],
          ["CPU", res && res.cpu_pct != null ? F.num(res.cpu_pct, 1) + " % eines Kerns" : null],
          ["Datenbank", res && res.db_mb != null ? F.num(res.db_mb, 1) + " MB" : null],
          ["Läuft seit", F.dur(sy.uptime_s)]
        ])),
        PB.panel("Bestand", null, PB.kv([
          ["Märkte im Katalog", h("a", { href: "#/maerkte" }, F.num(c["märkte_offen"]) + " offen")],
          ["davon mit eigenem Modell", F.num(c["märkte_mit_modell"])],
          ["Gespeicherte Prognosen", h("a", { href: "#/prognosen" }, F.num(c.prognosen))],
          ["Prognosen mit Ergebnis", F.num(c.prognosen_mit_ergebnis)],
          ["Neue Märkte in 24 h", F.num(d.activity.neu_24h)],
          ["Handelsvolumen 24 h im Katalog", d.activity.volumen_24h != null ? F.big(d.activity.volumen_24h) + " $" : null],
          ["Schlagzeilen gespeichert", F.num(c.nachrichten)]
        ])),
        PB.panel("Lernfortschritt", null, PB.kv([
          ["Tests Herausforderer gegen aktives Modell", F.num(d.learning.experimente) + ", davon " + F.num(d.learning.bestanden) + " bestanden"],
          ["Vorschläge zur Entscheidung", d.learning["offene_vorschläge"] ? h("a", { href: "#/lernen" }, d.learning["offene_vorschläge"] + " offen") : "keine"],
          ["Gelernte Parameter-Versionen", F.num(d.learning.modellversionen)],
          ["Entschiedene Märkte im Backtest-Datensatz", F.num(d.learning.datensatz)],
          ["In 24 h geprüft, davon gesperrt", F.num(d.activity.prognosen_24h) + ", " + F.num(d.activity.abgelehnt_24h)]
        ])));
      out.push(h("div", { class: "grid wide" }, left, right));

      out.push(PB.panel("Auffällige Kursbewegungen", "letzte Stunde", PB.table([
        { label: "Markt", get: function (m) { return h("div", { class: "q" }, m.question, h("small", null, PB.cat(m.category))); } },
        { label: "Kurs", num: true, get: function (m) { return F.pct(m.price); } },
        { label: "1 h", num: true, get: function (m) { return move(m.move_1h); } },
        { label: "Volumen 24 h", num: true, cls: "hide-s", get: function (m) { return m.volume24h != null ? F.big(m.volume24h) + " $" : null; } }
      ], d.activity.movers, { href: function (m) { return "#/markt/" + m.id; },
           empty: "In der letzten Stunde hat sich kein liquider Markt um mehr als 2 Punkte bewegt – oder der Preisverlauf ist noch keine Stunde alt." }), true));

      out.push(PB.panel("Paper-Trading", "Simulation mit Spielgeld", [PB.table([
        { label: "Konto", get: function (a) { return a.name; } },
        { label: "Wert", num: true, get: function (a) { return F.usd(a.equity); } },
        { label: "seit Start", num: true, get: function (a) { return h("span", { class: a.pnl_total >= 0 ? "pos" : "neg" }, F.susd(a.pnl_total)); } },
        { label: "offen", num: true, cls: "hide-s", get: function (a) { return F.num(a.open_count); } },
        { label: "Status", get: function (a) { return a.paused ? PB.chip("pausiert", "warn") : PB.chip("aktiv", "ok"); } }
      ], d.accounts), h("div", { style: { padding: "8px 14px 6px" } }, h("a", { href: "#/paper" }, "Alle Konten"), " · ", h("a", { href: "#/wetten" }, "Alle Wetten"), " · ", h("a", { href: "#/kalender" }, "Kalender"))], true));
      return out;
    }
  };

  // ================================================================ Märkte
  PB.pages.maerkte = {
    title: "Märkte", sub: "Alle erfassten Märkte – sortiert nach Aufmerksamkeit, durchsuchbar", live: false,
    load: function (r) {
      var q = r.q;
      return api("/api/markets?" + qs({ q: q.q, cat: q.cat, status: q.status || "open", sort: q.sort, dir: q.dir, page: q.page, flag: q.flag, modeled: q.flag === "modell" ? 1 : "" }));
    },
    render: function (d, r) {
      var q = r.q, sort = { key: q.sort || "priority", dir: q.dir || "desc" };
      var cats = [["", "Alle Kategorien"]].concat(d.categories.map(function (c) { return [c.category, (PB.CAT[c.category] || c.category) + " (" + c.n + ")"]; }));
      var filters = h("div", { class: "filters" },
        PB.ui.search(q.q, "Markt suchen"),
        select(q.cat, cats, function (v) { PB.setQuery({ cat: v, page: "" }); }, "Kategorie"),
        select(q.flag, [["", "Alle"], ["modell", "Mit eigenem Modell"], ["news", "Mit Schlagzeilen"], ["ok", "Daten vollständig"], ["mangel", "Daten unvollständig"]],
               function (v) { PB.setQuery({ flag: v, page: "" }); }, "Auswahl"),
        seg(q.status || "open", [["open", "Offen"], ["closed", "Beendet"]], function (v) { PB.setQuery({ status: v, page: "" }); }));
      var cols = [
        { label: "Markt", sort: "priority", get: function (m) {
          var flags = (m.quality || "").split(",").filter(Boolean);
          return h("div", { class: "q" }, m.question, h("small", null, PB.cat(m.category), m.modeled ? " · eigenes Modell" : "",
                   m.news_48h ? " · " + m.news_48h + " Schlagzeile" + (m.news_48h > 1 ? "n" : "") : "",
                   flags.length ? " · " + flags.map(function (f) { return FLAG[f] || f; }).join(", ") : ""));
        } },
        { label: "Kurs", num: true, sort: "price", get: function (m) { return F.pct(m.price); } },
        { label: "1 h", num: true, sort: "move_1h", get: function (m) { return move(m.move_1h); } },
        { label: "24 h", num: true, sort: "move_24h", cls: "hide-s", get: function (m) { return move(m.move_24h); } },
        { label: "Volumen 24 h", num: true, sort: "volume24h", get: function (m) { return m.volume24h != null ? F.big(m.volume24h) + " $" : null; } },
        { label: "Liquidität", num: true, sort: "liquidity", cls: "hide-s", get: function (m) { return m.liquidity != null ? F.big(m.liquidity) + " $" : null; } },
        { label: "Ende", num: true, sort: "end_date", cls: "hide-s", get: function (m) { return F.until(m.end_date); } },
        { label: "Aktualisiert", num: true, sort: "last_seen", cls: "hide-s", get: function (m) { return F.ago(m.last_seen); } }
      ];
      var tbl = PB.table(cols, d.rows, {
        sort: sort, href: function (m) { return "#/markt/" + m.id; },
        onSort: function (k) { PB.setQuery({ sort: k, dir: sort.key === k && sort.dir === "desc" ? "asc" : "desc", page: "" }); },
        empty: d.catalog.gesamt ? "Kein Markt passt zu dieser Auswahl." : "Der Katalog ist noch leer.",
        next: d.catalog.gesamt ? "Filter lockern oder Suche leeren." : "Er füllt sich mit dem ersten Lauf (Modell-Märkte) und dem stündlichen Durchlauf aller Kategorien."
      });
      var hint = F.num(d.catalog.offen) + " offene Märkte, " + F.num(d.catalog.preispunkte) + " Preispunkte" +
        (d.last_sweep ? ", letzter Durchlauf aller Kategorien " + F.ago(d.last_sweep) : "");
      return [filters, h("section", { class: "panel" }, h("header", null, h("h2", null, "Katalog"), h("span", { class: "hint" }, hint)),
                         h("div", { class: "body flush" }, tbl, d.total > d.per ? pager(d) : null)),
              h("p", { class: "note" }, "Standard-Reihenfolge: Aufmerksamkeit – Handelsaktivität, Liquidität, Nähe zur Entscheidung, Schlagzeilen und Datenqualität. " +
                "Je Kategorie stehen die aktivsten Märkte im Katalog; Polymarket führt deutlich mehr, die meisten ohne Handel.")];
    }
  };

  // ================================================================ Markt-Detail
  PB.pages.markt = {
    title: "Markt", sub: "", live: false,
    load: function (r) { return api("/api/market/" + encodeURIComponent(r.arg)); },
    render: function (d) {
      var m = d.market || {}, fc = d.forecasts || [], champ = fc.filter(function (f) { return f.champion; });
      var lastC = champ[champ.length - 1], out = [];
      out.push(h("div", null, h("a", { class: "back", href: "#/maerkte" }, "Zurück zu den Märkten"),
                 h("h2", { style: { "font-size": "18px", "line-height": "1.3", "max-width": "60ch" } }, m.question || "Markt " + (fc[0] ? fc[0].market_id : "")),
                 h("div", { class: "dim", style: { "margin-top": "4px" } }, PB.cat(m.category), m.event_title ? " · " + m.event_title : "")));
      var flags = (m.quality || "").split(",").filter(Boolean);
      var oc = d.outcome;
      out.push(h("section", { class: "panel" }, h("div", { class: "body" }, h("div", { class: "facts" },
        h("div", null, h("div", { class: "l" }, "Marktpreis für Ja"), h("div", { class: "v" }, PB.val(F.pct(m.price, 1)))),
        h("div", null, h("div", { class: "l" }, "Eigene Prognose"), h("div", { class: "v" }, lastC ? [F.pct(lastC.p, 1), h("small", null, lastC.uncertainty != null ? " ± " + F.num(lastC.uncertainty * 100, 0) : "")] : PB.val(null))),
        h("div", null, h("div", { class: "l" }, "Unterschied"), h("div", { class: "v" }, lastC && m.price != null ? move(lastC.p - m.price) : PB.val(null))),
        h("div", null, h("div", { class: "l" }, "Ergebnis"), h("div", { class: "v" }, oc ? (oc.outcome >= 0.5 ? "Ja" : "Nein") : h("small", null, m.status === "open" ? "offen" : "noch nicht gemeldet"))),
        h("div", null, h("div", { class: "l" }, "Volumen 24 h"), h("div", { class: "v" }, PB.val(m.volume24h != null ? F.big(m.volume24h) + " $" : null))),
        h("div", null, h("div", { class: "l" }, "Liquidität"), h("div", { class: "v" }, PB.val(m.liquidity != null ? F.big(m.liquidity) + " $" : null))),
        h("div", null, h("div", { class: "l" }, "Ende"), h("div", { class: "v" }, PB.val(F.dt(m.end_date)), h("small", null, m.end_date ? " " + F.until(m.end_date) : "")))),
        h("div", { class: "checks", style: { "margin-top": "12px" } },
          m.bid != null && m.ask != null ? PB.chip("Geld " + F.pct(m.bid, 1) + " / Brief " + F.pct(m.ask, 1)) : null,
          flags.length ? flags.map(function (f) { return PB.chip(FLAG[f] || f, "warn"); }) : (m.id ? PB.chip("Daten vollständig", "ok") : null),
          m.last_seen ? PB.chip("aktualisiert " + F.ago(m.last_seen)) : null,
          m.slug ? h("a", { href: "https://polymarket.com/event/" + encodeURIComponent(m.slug), target: "_blank", rel: "noopener noreferrer" }, "Bei Polymarket ansehen") : null))));

      var hist = (d.history || []).map(function (p) { return [new Date(p.ts + ":00Z").getTime(), p.price]; });
      var own = champ.map(function (f) { return [new Date(f.created_at).getTime(), f.p]; });
      out.push(PB.panel("Preisverlauf und eigene Prognosen", "stündlich gespeichert", PB.line({
        series: [{ name: "Marktpreis", color: PB.COL.markt, points: hist }, { name: "Eigene Prognose", color: PB.COL.modell, points: own, dotsOnly: true }],
        xType: "time", yMin: 0, yMax: 1, yFmt: function (v) { return F.pct(v); }, label: "Preisverlauf",
        empty: "Zu diesem Markt ist noch kein Preispunkt gespeichert.", next: "Der Verlauf beginnt mit der ersten vollen Stunde nach der Aufnahme in den Katalog."
      })));

      out.push(PB.panel("Prognosen zu diesem Markt", champ.length ? "je Zeithorizont wird die erste Prognose festgehalten und nie verändert" : "", PB.table([
        { label: "Erstellt", get: function (f) { return F.dt(f.created_at); } },
        { label: "Horizont", get: function (f) { return f.horizon; } },
        { label: "Modell", get: function (f) { return h("span", null, f.model + "@" + f.version, f.champion ? " " : "", f.champion ? PB.chip("aktiv", "acc") : null); } },
        { label: "Eigene", num: true, get: function (f) { return F.pct(f.p, 1); } },
        { label: "Markt", num: true, get: function (f) { return F.pct(f.market_p, 1); } },
        { label: "Unterschied", num: true, get: function (f) { return move(f.p - f.market_p); } },
        { label: "Unsicherheit", num: true, cls: "hide-s", get: function (f) { return f.uncertainty != null ? "± " + F.num(f.uncertainty * 100, 0) + " Pkt" : "–"; } },
        { label: "Prüfung", get: function (f) { return !f.champion ? "–" : f.valid ? PB.chip("bestanden", "ok") : h("span", null, PB.chip("gesperrt", "bad"), " ", h("span", { class: "dim" }, f.reject_reason)); } },
        { label: "Fehler", num: true, get: function (f) { return oc ? F.num(Math.abs(f.p - oc.outcome) * 100, 0) + " Pkt" : "–"; } }
      ], fc, { empty: "Für diesen Markt gibt es keine eigene Prognose.", next: "Eigene Modelle gibt es derzeit für Krypto-Kurse, Sport-Sieger und Temperaturen." }), true));

      if (lastC) {
        var src = lastC.sources || [], feat = lastC.features;
        out.push(PB.panel("Grundlage der letzten Prognose", null, [
          PB.kv([["Modell", lastC.model + "@" + lastC.version], ["Datenquellen", src.length ? src.join(", ") : null],
                 ["Restlaufzeit bei Erstellung", lastC.hours_left != null ? F.dur(lastC.hours_left * 3600) : null],
                 ["Gruppe zusammenhängender Märkte", lastC.cluster]], true),
          feat ? h("details", { class: "tbl" }, h("summary", null, "Merkmale, die ins Modell eingingen"), h("pre", { class: "raw" }, JSON.stringify(feat, null, 1))) : null]));
      }
      out.push(PB.panel("Schlagzeilen", "Stichwort-Abgleich, keine Bewertung", (d.news || []).length ? h("ul", { class: "feed" }, d.news.map(function (n) {
        return h("li", null, h("span", { class: "lvl" }),
                 h("div", null, h("div", { class: "t" }, h("a", { href: n.url, target: "_blank", rel: "noopener noreferrer" }, n.title)),
                   h("div", { class: "d" }, n.source, n.flags ? [" · ", PB.chip(n.flags, "warn")] : null)),
                 h("time", { title: n.published ? "veröffentlicht " + F.dt(n.published) : "abgerufen " + F.dt(n.fetched_at) }, F.ago(n.published || n.fetched_at)));
      })) : PB.empty("Keine gespeicherte Schlagzeile nennt die Namen aus diesem Markt.", "Schlagzeilen kommen halbstündlich aus den BBC-Feeds und werden nur den 400 wichtigsten Märkten zugeordnet."), true));
      if ((d.positions || []).length) {
        out.push(PB.panel("Paper-Trading auf diesem Markt", "Simulation", PB.table([
          { label: "Konto", get: function (p) { return p.account; } }, { label: "Seite", get: function (p) { return p.side_name; } },
          { label: "Kurs", num: true, get: function (p) { return F.pct(p.price, 1); } }, { label: "Einsatz", num: true, get: function (p) { return F.usd(p.cost); } },
          { label: "Stand", get: function (p) { return { open: "offen", won: "gewonnen", lost: "verloren", sold: "verkauft", void: "storniert" }[p.status] || p.status; } },
          { label: "Ergebnis", num: true, get: function (p) { return p.pnl == null ? "–" : h("span", { class: p.pnl >= 0 ? "pos" : "neg" }, F.susd(p.pnl)); } }
        ], d.positions), true));
      }
      return out;
    }
  };

  // ================================================================ Prognosen
  PB.pages.prognosen = {
    title: "Prognosen", sub: "Jede gespeicherte Prognose mit Marktpreis, Prüfung und – später – dem Ergebnis", live: false,
    load: function (r) { return api("/api/predictions?" + qs({ q: r.q.q, cat: r.q.cat, status: r.q.status, sort: r.q.sort, page: r.q.page })); },
    render: function (d, r) {
      var q = r.q, c = d.counts;
      var filters = h("div", { class: "filters" },
        PB.ui.search(q.q, "Markt suchen"),
        select(q.cat, [["", "Alle Bereiche"]].concat(PB.MODELED.map(function (k) { return [k, PB.CAT[k]]; })), function (v) { PB.setQuery({ cat: v, page: "" }); }, "Bereich"),
        seg(q.status || "all", [["all", "Alle"], ["offen", "Offen"], ["entschieden", "Entschieden"], ["abgelehnt", "Gesperrt"]], function (v) { PB.setQuery({ status: v === "all" ? "" : v, page: "" }); }),
        select(q.sort, [["", "Neueste zuerst"], ["abstand", "Größter Abstand zum Markt"], ["fehler", "Größter Fehler (nur entschiedene)"]], function (v) { PB.setQuery({ sort: v, page: "" }); }, "Reihenfolge"));
      var tbl = PB.table([
        { label: "Markt", get: function (f) { return h("div", { class: "q" }, f.question || "Markt " + f.market_id, h("small", null, PB.cat(f.category), " · ", f.model + "@" + f.version, " · ", F.dt(f.created_at))); } },
        { label: "Horizont", cls: "hide-s", get: function (f) { return f.horizon; } },
        { label: "Eigene", num: true, get: function (f) { return F.pct(f.p, 1); } },
        { label: "Markt", num: true, get: function (f) { return F.pct(f.market_p, 1); } },
        { label: "Unterschied", num: true, cls: "hide-s", get: function (f) { return move(f.p - f.market_p); } },
        { label: "Prüfung", get: function (f) { return f.valid ? PB.chip("bestanden", "ok") : PB.chip("gesperrt", "bad"); } },
        { label: "Ergebnis", get: function (f) {
          if (f.outcome == null) return h("span", { class: "dim" }, "offen");
          var err = Math.abs(f.p - f.outcome), merr = Math.abs(f.market_p - f.outcome);
          return h("span", null, f.outcome >= 0.5 ? "Ja" : "Nein", h("span", { class: err <= merr ? "pos" : "neg" }, " · " + (err <= merr ? "näher als der Markt" : "Markt war näher")));
        } }
      ], d.rows, { href: function (f) { return "#/markt/" + f.market_id; },
                   empty: c.gesamt ? "Keine Prognose passt zu dieser Auswahl." : "Es ist noch keine Prognose gespeichert.",
                   next: c.gesamt ? "Filter lockern." : "Die Prognose-Historie beginnt mit dem ersten Lauf nach dem Update." });
      var hint = F.num(c.gesamt) + " Prognosen des aktiven Modells, " + F.num(c.varianten - c.gesamt) + " von Herausforderern, " + F.num(c.entschieden) + " entschieden, " + F.num(c.abgelehnt) + " gesperrt";
      return [filters, h("section", { class: "panel" }, h("header", null, h("h2", null, "Prognose-Historie"), h("span", { class: "hint" }, hint)),
                         h("div", { class: "body flush" }, tbl, d.total > d.per ? pager(d) : null)),
              h("p", { class: "note" }, "Ein Klick öffnet die Detailansicht mit Modell, Unsicherheit, Datenquellen, Prüfergebnis und Verlauf. " +
                "Gesperrte Prognosen werden gespeichert, aber nicht gehandelt – so lässt sich später messen, ob die Sperre richtig war.")];
    }
  };

  // ================================================================ KI-Zentrale
  PB.pages.agenten = {
    title: "KI-Zentrale", sub: "Die Agenten sind Programm-Module mit festen Aufgaben – kein Sprachmodell, keine laufenden Kosten", live: true,
    load: function () { return api("/api/agents"); },
    render: function (d) {
      var list = h("div", null, d.agents.map(function (a) {
        var word = STATE[a.state] || a.state;
        return h("div", { class: "agent" },
          h("span", { class: "led " + a.state, title: word }),
          h("div", null, h("b", null, a.name), h("div", { class: "role" }, a.role)),
          h("div", { class: "num" }, PB.chip(a.task ? a.task : word, a.state === "fehler" ? "bad" : a.state === "ok" ? "ok" : "")),
          h("div", { class: "meta" },
            h("span", null, "Letzte Aktivität: " + (a.last_end ? F.ago(a.last_end) : "noch keine")),
            h("span", null, "Takt: " + (a.interval ? "alle " + F.dur(a.interval) : "in jedem Lauf")),
            h("span", null, "Dauer: " + (a.last_duration != null ? F.num(a.last_duration, 1) + " s (Budget " + a.budget + " s)" : "keine Daten")),
            h("span", null, "Läufe: " + a.runs + ", Fehler: " + a.errors + (a.skipped ? ", zurückgestellt: " + a.skipped : "")),
            h("span", null, "Anteil an der Rechenzeit: " + F.pct(a.load_share))),
          a.last_error ? h("div", { class: "err" }, "Letzter Fehler " + (F.ago(a.last_error_at) || "") + ": " + a.last_error) : null,
          a.last_summary ? h("details", null, h("summary", null, "Letztes Ergebnis"), h("pre", null, JSON.stringify(a.last_summary, null, 1))) : null);
      }));
      var t = d.tick, v = d.validation.letzter_lauf || {};
      var reasons = Object.keys(v).filter(function (k) { return k !== "geprüft" && k !== "gültig"; });
      return [
        PB.panel("Agenten", d.agents.length + " Module, gesteuert vom Koordinator", list, true),
        h("div", { class: "grid two" },
          PB.panel("Letzter Takt", t ? F.ago(t.zeit) : "", t ? PB.kv([
            ["Dauer", F.dur(t.dauer) + " von höchstens " + F.dur(t.budget)],
            ["Gelaufen", t.gelaufen.length ? t.gelaufen.join(", ") : "nur der Handels-Lauf"],
            ["Zurückgestellt", t["zurückgestellt"].length ? t["zurückgestellt"].join(", ") : "nichts"]]) : PB.empty("Seit dem Start ist noch kein Takt fertig geworden.", "Der erste Takt startet wenige Sekunden nach dem Hochfahren.")),
          PB.panel("Prüfung im letzten Lauf", "Validation Agent", v["geprüft"] != null ? PB.kv([
            ["Geprüfte Prognosen", F.num(v["geprüft"])], ["Bestanden", F.num(v["gültig"])]].concat(reasons.map(function (k) { return ["Gesperrt: " + k, F.num(v[k])]; })))
            : PB.empty("Im letzten Lauf wurde keine Prognose geprüft."))),
        PB.panel("Widersprüche zwischen Modell und Markt", "entgegengesetzte Seiten bei liquidem Markt", PB.table([
          { label: "Markt", get: function (c) { return h("div", { class: "q" }, c.question, h("small", null, PB.cat(c.category))); } },
          { label: "Eigene", num: true, get: function (c) { return F.pct(c.p); } }, { label: "Markt", num: true, get: function (c) { return F.pct(c.market_p); } },
          { label: "Prüfung", get: function (c) { return c.valid ? PB.chip("bestanden", "ok") : h("span", null, PB.chip("gesperrt", "bad"), " ", h("span", { class: "dim" }, c.reject || "")); } }
        ], d.contradictions, { href: function (c) { return "#/markt/" + c.id; }, empty: "Im letzten Lauf lagen Modell und Markt bei keinem liquiden Markt auf entgegengesetzten Seiten." }), true),
        PB.panel("Letzte Läufe", null, PB.table([
          { label: "Agent", get: function (r) { return r.agent; } }, { label: "Start", get: function (r) { return F.dt(r.started_at); } },
          { label: "Dauer", num: true, get: function (r) { return F.num(r.duration, 1) + " s"; } },
          { label: "Ergebnis", get: function (r) { return r.ok ? PB.chip("ok", "ok") : h("span", null, PB.chip("Fehler", "bad"), " ", h("span", { class: "dim" }, r.error)); } }
        ], d.runs.slice(0, 30), { empty: "Noch kein Lauf aufgezeichnet." }), true)];
    }
  };
})();
