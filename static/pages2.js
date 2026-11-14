/* Zentrale – Seiten: Lernen, Strategie-Labor, Performance, Daten, System, Claude-Berichte, Einstellungen */
(function () {
  "use strict";
  var PB = window.PB, h = PB.h, F = PB.fmt, api = PB.api, ui = PB.ui;
  var HZ = { "<6h": "unter 6 h", "6-24h": "6–24 h", "24-72h": "1–3 Tage", ">72h": "über 3 Tage" };
  var HCOL = { p_1h: "1 h", p_6h: "6 h", p_24h: "24 h", p_72h: "3 Tage", p_7d: "7 Tage" };

  function act(promise, okText) {
    return promise.then(function () { PB.toast(okText); PB.reload(); }).catch(function (e) { PB.toast(e.message, true); });
  }
  function verdict(m) {
    if (!m || m.n == null || m.skill == null) return PB.val(null);
    var sure = m.z != null && Math.abs(m.z) >= 1.64;
    if (!sure) return PB.chip("noch offen");
    return m.skill > 0 ? PB.chip("genauer", "ok") : PB.chip("ungenauer", "bad");
  }
  function modelCols(max) {
    return [
      { label: "Modell", get: function (m) { return h("span", { class: "nowrap" }, m.model, m.active ? " " : "", m.active ? PB.chip("aktiv", "acc") : null); } },
      { label: "Prognosen", num: true, get: function (m) { return F.num(m.n); } },
      { label: "Ereignisse", num: true, cls: "hide-s", get: function (m) { return F.num(m.clusters); } },
      { label: "Brier", num: true, get: function (m) { return F.num(m.brier, 3); } },
      { label: "Brier Markt", num: true, get: function (m) { return F.num(m.brier_market, 3); } },
      { label: "kleiner ist besser", cls: "hide-s", get: function (m) { return PB.pair(m.brier, m.brier_market, max); } },
      { label: "Vorsprung", num: true, get: function (m) { return m.skill == null ? null : h("span", { class: m.skill >= 0 ? "pos" : "neg" }, F.spct(m.skill, 0)); } },
      { label: "z", num: true, cls: "hide-s", get: function (m) { return F.z(m.z); } },
      { label: "Treffer", num: true, cls: "hide-s", get: function (m) { return F.pct(m.hit); } },
      { label: "Verzerrung", num: true, cls: "hide-s", get: function (m) { return F.pts(m.bias); } },
      { label: "Gegen den Markt", get: verdict }
    ];
  }
  function legendPair() {
    return h("div", { class: "legend" }, h("span", null, h("i", { class: "box", style: { background: PB.COL.modell } }), "Modell"), h("span", null, h("i", { class: "box", style: { background: PB.COL.markt } }), "Marktpreis"));
  }

  // ================================================================ Lernen
  PB.pages.lernen = {
    title: "Lernen", sub: "Der Bot misst, testet und schlägt vor – umgestellt wird erst nach deiner Freigabe", live: false,
    load: function () { return api("/api/learning"); },
    render: function (d) {
      var L = d.lernen, S = L.zusammenfassung, M = d.modelle, K = d.kriterien, out = [];
      var open = (L["vorschläge"] || []).filter(function (p) { return p.status === "offen"; });
      open.forEach(function (p) {
        var x = p.detail || {}, note = h("input", { type: "text", placeholder: "Notiz (optional)", "aria-label": "Notiz", style: { flex: "1 1 180px", background: "var(--panel)", border: "1px solid var(--line2)", "border-radius": "8px", padding: "7px 10px" } });
        out.push(h("div", { class: "callout" }, h("b", null, "Vorschlag zur Entscheidung: "), p.title,
          h("div", { class: "dim", style: { margin: "4px 0 10px" } },
            x.n_oos != null ? "Im späteren Teil der Daten (" + F.num(x.n_oos) + " Prognosen, " + F.num(x.clusters_oos) + " Ereignisse): Brier " + F.num(x.brier_chal_oos, 3) +
              " statt " + F.num(x.brier_champ_oos, 3) + " (Markt " + F.num(x.brier_market_oos, 3) + "), z = " + F.z(x.z) + "."
            : x.n != null ? "Seit der Aktivierung in " + F.num(x.n) + " Prognosen: Brier " + F.num(x.brier_aktiv, 3) + " gegen " + F.num(x.brier_vorgaenger, 3) + " beim Vorgänger." : ""),
          h("div", { class: "row" }, note,
            h("button", { class: "btn primary", on: { click: function () {
              if (confirm("Freigeben?\n\n" + p.title + "\n\nDas aktive Modell wird sofort umgestellt. Die bisherige Einstellung bleibt gespeichert und lässt sich zurückholen."))
                act(api("/api/proposal/" + p.id + "/approve", { note: note.value }), "Freigegeben");
            } } }, "Freigeben"),
            h("button", { class: "btn", on: { click: function () { act(api("/api/proposal/" + p.id + "/reject", { note: note.value }), "Abgelehnt"); } } }, "Ablehnen"))));
      });

      out.push(PB.panel("Aktive Modelle", "Wechsel nur nach Freigabe, jederzeit rückholbar", PB.table([
        { label: "Bereich", get: function (r) { return PB.cat(r.category); } },
        { label: "Modell", get: function (r) { return h("div", { class: "q" }, r.model + "@" + r.version, h("small", null, r.info)); } },
        { label: "Mischung mit dem Markt", get: function (r) { return r.mix_w != null && r.mix_w < 0.999 ? F.pct(r.mix_w) + " Modell" : "keine"; } },
        { label: "Seit", cls: "hide-s", get: function (r) { return F.dt(r.activated_at); } },
        { label: "", get: function (r) {
          return r.prev_model ? h("button", { class: "btn small", on: { click: function () {
            if (confirm("Zurück zu " + r.prev_model + "@" + r.prev_version + " für " + (PB.CAT[r.category] || r.category) + "?"))
              act(api("/api/model/rollback", { category: r.category }), "Zurückgeholt");
          } } }, "Zurück zu " + r.prev_model + "@" + r.prev_version) : h("span", { class: "dim2" }, "Ausgangsmodell");
        } }
      ], M.aktiv), true));

      if (!S || !S.n) {
        out.push(PB.panel("Modellvergleich", null, PB.empty("Noch keine Prognose mit bekanntem Ergebnis.",
          "Der Learning Agent wertet stündlich aus. Belastbar wird ein Vergleich ab etwa " + K.min_oos + " entschiedenen Prognosen je Bereich.")));
      } else {
        var max = 0.05;
        S.modelle.forEach(function (m) { max = Math.max(max, m.brier || 0, m.brier_market || 0); });
        PB.MODELED.forEach(function (cat) {
          var rows = S.modelle.filter(function (m) { return m.category === cat; });
          if (!rows.length) return;
          var act_ = rows.filter(function (m) { return m.active; })[0], hz = act_ ? Object.keys(act_.horizonte || {}) : [];
          out.push(PB.panel("Modellvergleich " + PB.CAT[cat], "Stand " + F.dt(S.stand), [
            h("div", { style: { padding: "0 14px" } }, legendPair()), PB.table(modelCols(max * 1.05), rows),
            hz.length ? h("div", { style: { padding: "4px 14px 8px" } }, h("h3", null, "Aktives Modell nach Zeithorizont"), PB.table([
              { label: "Restlaufzeit", get: function (k) { return HZ[k] || k; } },
              { label: "Prognosen", num: true, get: function (k) { return F.num(act_.horizonte[k].n); } },
              { label: "Brier", num: true, get: function (k) { return F.num(act_.horizonte[k].brier, 3); } },
              { label: "Brier Markt", num: true, get: function (k) { return F.num(act_.horizonte[k].brier_market, 3); } },
              { label: "", get: function (k) { return PB.pair(act_.horizonte[k].brier, act_.horizonte[k].brier_market, max * 1.05); } }
            ], hz)) : null], true));
        });
        var E = S.fehler || {};
        var findings = [];
        (E.kategorien || []).forEach(function (c) {
          c.befunde.forEach(function (b) { findings.push(h("li", null, h("b", null, PB.CAT[c.category] + ": "), b + " (" + F.num(c.n) + " Prognosen)")); });
          if (c.sicher >= 10) findings.push(h("li", null, h("b", null, PB.CAT[c.category] + ": "), "Von " + c.sicher + " sehr sicheren Prognosen (unter 10 % oder über 90 %) lagen " +
            c.sicher_falsch + " falsch; beim Markt " + c.markt_sicher_falsch + " von " + c.markt_sicher + "."));
        });
        out.push(PB.panel("Fehleranalyse", "systematische Muster, keine Einzelfälle", [
          findings.length ? h("ul", { style: { margin: "0", "padding-left": "20px" } }, findings) : h("p", { class: "dim" }, "Bisher kein systematisches Muster erkennbar (dafür braucht es mindestens 30 entschiedene Prognosen je Bereich)."),
          (E.validierung || []).length ? [h("h3", null, "War die Sperre durch die Prüfung berechtigt?"), PB.table([
            { label: "Grund der Sperre", get: function (v) { return v.grund; } }, { label: "Prognosen", num: true, get: function (v) { return F.num(v.n); } },
            { label: "Brier Modell", num: true, get: function (v) { return F.num(v.brier, 3); } }, { label: "Brier Markt", num: true, get: function (v) { return F.num(v.brier_market, 3); } },
            { label: "Urteil", get: function (v) { return v.brier > v.brier_market ? PB.chip("Sperre berechtigt", "ok") : PB.chip("Modell lag näher", "warn"); } }
          ], E.validierung)] : null,
          (E.stationen || []).length ? [h("h3", null, "Wetter-Stationen mit dem größten Rückstand"), PB.table([
            { label: "Station", get: function (s) { return s.station; } }, { label: "Prognosen", num: true, get: function (s) { return F.num(s.n); } },
            { label: "Brier Modell", num: true, get: function (s) { return F.num(s.brier, 3); } }, { label: "Brier Markt", num: true, get: function (s) { return F.num(s.brier_market, 3); } }
          ], E.stationen.slice(0, 6))] : null,
          (E.schlimmste || []).length ? [h("h3", null, "Größte Fehlprognosen"), PB.table([
            { label: "Markt", get: function (w) { return h("div", { class: "q" }, w.frage, h("small", null, PB.cat(w.category), " · ", w.modell, " · ", HZ[w.horizont] || w.horizont)); } },
            { label: "Eigene", num: true, get: function (w) { return F.pct(w.p); } }, { label: "Markt", num: true, get: function (w) { return F.pct(w.markt); } },
            { label: "Ergebnis", get: function (w) { return w.ergebnis >= 0.5 ? "Ja" : "Nein"; } }
          ], E.schlimmste.slice(0, 8), { href: function (w) { return "#/markt/" + w.market_id; } })] : null]));
      }

      var exps = L.experimente || [];
      out.push(PB.panel("Verbesserungsversuche", "Herausforderer gegen aktives Modell, getrennt nach früherem und späterem Teil der Daten", [
        PB.table([
          { label: "Versuch", get: function (e) { return h("div", { class: "q" }, e.challenger, h("small", null, PB.cat(e.category), " · gegen ", e.champion)); } },
          { label: "Prognosen früh / spät", num: true, cls: "hide-s", get: function (e) { return F.num(e.n_is) + " / " + F.num(e.n_oos); } },
          { label: "Brier spät", num: true, get: function (e) { return F.num(e.brier_chal_oos, 3); } },
          { label: "aktives Modell", num: true, get: function (e) { return F.num(e.brier_champ_oos, 3); } },
          { label: "Gewinn", num: true, cls: "hide-s", get: function (e) { return e.gain == null ? null : h("span", { class: e.gain > 0 ? "pos" : "neg" }, F.spct(e.gain, 0)); } },
          { label: "Ergebnis", get: function (e) { return e.passed ? PB.chip("bestanden", "ok") : h("span", null, PB.chip("nicht bestanden"), " ", h("span", { class: "dim" }, (e.reasons || []).join("; "))); } }
        ], exps, { empty: "Noch kein Test gelaufen.", next: "Tests starten automatisch, sobald ein Bereich 20 entschiedene Prognosen hat." }),
        h("p", { class: "note", style: { padding: "8px 14px 0" } }, "Ein Vorschlag entsteht nur, wenn der Herausforderer im späteren Teil mindestens " + F.num(K.min_oos) + " Prognosen aus " +
          F.num(K.min_cluster) + " unabhängigen Ereignissen hat, dort mindestens " + F.pct(K.min_gewinn) + " besser ist (z ≤ −" + F.num(K.min_z, 2) + ") und auch im früheren Teil nicht schlechter war.")], true));

      var done = (L["vorschläge"] || []).filter(function (p) { return p.status !== "offen"; });
      out.push(h("div", { class: "grid two" },
        PB.panel("Versionshistorie", "jede Umstellung des aktiven Modells", PB.table([
          { label: "Zeit", get: function (r) { return F.dt(r.ts); } }, { label: "Bereich", get: function (r) { return PB.cat(r.category); } },
          { label: "Modell", get: function (r) { return r.model + "@" + r.version + (r.mix_w != null && r.mix_w < 0.999 ? ", " + F.pct(r.mix_w) + " Modell" : ""); } },
          { label: "Anlass", cls: "hide-s", get: function (r) { return r.reason || r.action; } }
        ], M.verlauf), true),
        PB.panel("Entschiedene Vorschläge", null, PB.table([
          { label: "Vorschlag", get: function (p) { return h("div", { class: "q" }, p.title, p.note ? h("small", null, p.note) : null); } },
          { label: "Entscheidung", get: function (p) { return p.status === "freigegeben" ? PB.chip("freigegeben", "ok") : PB.chip("abgelehnt"); } },
          { label: "Am", cls: "hide-s", get: function (p) { return F.dt(p.decided_at); } }
        ], done, { empty: "Bisher wurde kein Vorschlag entschieden." }), true)));
      out.push(PB.panel("Gelernte Parameter", "entstehen aus den eigenen Messungen und laufen zuerst nur als Herausforderer mit", PB.table([
        { label: "Modell", get: function (v) { return v.model + "@" + v.version; } }, { label: "Messungen", num: true, get: function (v) { return F.num(v.n_samples); } },
        { label: "Erstellt", get: function (v) { return F.dt(v.created_at); } }, { label: "Inhalt", cls: "hide-s", get: function (v) { return v.note; } }
      ], M.versionen, { empty: "Noch keine gelernte Version.", next: "Die Stations-Korrektur fürs Wetter entsteht, sobald 30 entschiedene Tage/Stationen vorliegen." }), true));
      return out;
    }
  };

  // ================================================================ Strategie-Labor
  PB.pages.strategie = {
    title: "Strategie-Labor", sub: "Simulation auf entschiedenen Märkten – hier wird nichts gehandelt, auch nicht mit Spielgeld", live: false,
    load: function () { return api("/api/strategy"); },
    render: function (d, r) {
      var L = d.latest, out = [];
      if (!L) return [PB.panel("Datensatz", null, PB.empty("Der Datensatz entschiedener Märkte ist noch leer (" + F.num(d.datensatz) + " Märkte).",
        "Der Strategy Research Agent lädt alle 30 Minuten bis zu 25 entschiedene Märkte samt Kursverlauf nach. Der erste Backtest läuft ab 50 Märkten mit Kurs."))];
      var D = L.datensatz;
      out.push(PB.panel("Datensatz", "Stand " + F.dt(L.stand), [
        h("div", { class: "facts" },
          h("div", null, h("div", { class: "l" }, "Entschiedene Märkte"), h("div", { class: "v" }, F.num(D.zeilen))),
          h("div", null, h("div", { class: "l" }, "mit Kurs 24 h vor Ende"), h("div", { class: "v" }, F.num(D.mit_kurs_24h))),
          h("div", null, h("div", { class: "l" }, "Zeitraum"), h("div", { class: "v" }, D.von ? F.day(D.von) + " – " + F.day(D.bis) : PB.val(null))),
          h("div", null, h("div", { class: "l" }, "vorzeitig entschieden"), h("div", { class: "v" }, F.num(D["früh_entschieden"])))),
        h("h3", null, "Datenlücken: Märkte ohne Kurs zum jeweiligen Zeitpunkt"),
        h("div", { class: "checks" }, Object.keys(D["lücken"]).map(function (k) { return PB.chip(HCOL[k] + " vor Ende: " + F.num(D["lücken"][k]) + " von " + F.num(D.zeilen)); })),
        h("h3", null, "Kategorien"),
        h("div", { class: "checks" }, Object.keys(D.kategorien).map(function (k) { return PB.chip((PB.CAT[k] || k) + ": " + F.num(D.kategorien[k])); })),
        L.hinweis ? h("p", { class: "callout", style: { "margin-top": "12px" } }, L.hinweis) : null]));
      if (!(L.strategien || []).length) return out;

      out.push(PB.panel("Strategien im Vergleich", "früherer Teil = Entwicklung, späterer Teil = Prüfung", [PB.table([
        { label: "Strategie", get: function (s) { return h("div", { class: "q" }, s.name, h("small", null, s.desc)); } },
        { label: "Wetten früh", num: true, cls: "hide-s", get: function (s) { return F.num(s.is.n); } },
        { label: "je Wette früh", num: true, cls: "hide-s", get: function (s) { return s.is.roi == null ? "–" : h("span", { class: s.is.roi >= 0 ? "pos" : "neg" }, F.spct(s.is.roi)); } },
        { label: "Wetten spät", num: true, get: function (s) { return F.num(s.oos.n); } },
        { label: "je Wette spät", num: true, get: function (s) { return s.oos.roi == null ? "–" : h("span", { class: s.oos.roi >= 0 ? "pos" : "neg" }, F.spct(s.oos.roi)); } },
        { label: "t", num: true, cls: "hide-s", get: function (s) { return s.oos.t == null ? "–" : F.z(s.oos.t); } },
        { label: "Urteil", get: function (s) {
          return h("div", null, s.baseline ? PB.chip("Vergleichsgröße") : s.bestanden ? PB.chip("Prüfteil bestanden", "ok") : PB.chip("nicht bestanden"),
                   (s.warnungen || []).map(function (w) { return h("div", { class: /Überanpassung/.test(w) ? "warn" : "dim", style: { "font-size": "12.5px", "margin-top": "3px" } }, w); }));
        } }
      ], L.strategien), h("p", { class: "note", style: { padding: "8px 14px 0" } },
        "Je Wette 1 $ Einsatz. Kosten: Gebühr " + F.pct(L.kosten["gebühr"]) + " (Krypto " + F.pct(L.kosten["gebühr_krypto"]) + ") mal p·(1−p) plus " +
        F.num(L.kosten.halbe_spanne * 100) + " Cent halbe Spanne. Bezugspunkt ist das geplante Ende eines Marktes; Kurse nach der tatsächlichen Entscheidung zählen nicht. " +
        "Bestanden heißt: im Prüfteil mindestens 30 Wetten, im Plus und statistisch gesichert (t ≥ 1,64) – und im Entwicklungsteil ebenfalls im Plus.")], true));

      var cat = r.q.cat && L.markt[r.q.cat] ? r.q.cat : "gesamt", mq = L.markt[cat] || {};
      var opts = Object.keys(L.markt).map(function (k) { return [k, k === "gesamt" ? "Alle Kategorien" : PB.CAT[k] || k]; });
      out.push(h("div", { class: "grid two" },
        PB.panel("Wie gut ist der Marktpreis selbst?", "die Messlatte für jedes Modell", [
          h("div", { style: { padding: "0 14px 8px" } }, ui.select(cat, opts, function (v) { PB.setQuery({ cat: v === "gesamt" ? "" : v }); }, "Kategorie")),
          PB.table([{ label: "Abstand zum Ende", get: function (k) { return HCOL[k]; } }, { label: "Märkte", num: true, get: function (k) { return F.num(mq[k].n); } },
                    { label: "Brier", num: true, get: function (k) { return F.num(mq[k].brier, 3); } }, { label: "Treffer", num: true, get: function (k) { return F.pct(mq[k].treffer); } }],
                   Object.keys(HCOL).filter(function (k) { return mq[k]; }))], true),
        PB.panel("Kalibrierung des Marktes", "24 h vor Ende: Kurs gegen eingetretene Häufigkeit", PB.line({
          series: [{ name: "Markt", color: PB.COL.markt, dots: true, points: (L.kalibrierung || []).map(function (b) { return [b.p, b.ist]; }) }],
          ref: [[0, 0], [1, 1]], xType: "num", yMin: 0, yMax: 1, xTitle: "Kurs", xFmt: function (v) { return F.pct(v); }, yFmt: function (v) { return F.pct(v); },
          label: "Kalibrierung des Marktes", height: 240 }))));
      return out;
    }
  };

  // ================================================================ Performance
  PB.pages.performance = {
    title: "Performance", sub: "Prognosequalität über die Zeit – immer neben dem Marktpreis als Messlatte", live: false,
    load: function () { return api("/api/performance"); },
    render: function (d, r) {
      if (!d.n) return [PB.panel("Prognosequalität", null, PB.empty("Noch keine Prognose mit bekanntem Ergebnis.",
        "Sobald Märkte enden, erscheinen hier Brier-Score, LogLoss und Kalibrierung – ausgewertet wird stündlich."))].concat(quality(d));
      var have = PB.MODELED.filter(function (c) { return d.verlauf.some(function (t) { return t.category === c; }); });
      var cat = have.indexOf(r.q.cat) >= 0 ? r.q.cat : have[0];
      var out = [h("div", { class: "filters" }, ui.seg(cat, have.map(function (c) { return [c, PB.CAT[c]]; }), function (v) { PB.setQuery({ cat: v }); }),
                   h("span", { class: "dim" }, "Stand " + F.dt(d.stand) + ", " + F.num(d.n) + " Prognosen mit Ergebnis"))];
      var tl = d.verlauf.filter(function (t) { return t.category === cat; });
      function cum(key) {     // laufender Mittelwert seit Beginn: zeigt die Entwicklung ohne das Rauschen einzelner Tage
        var s = 0, n = 0;
        return tl.map(function (t) { s += t[key] * t.n; n += t.n; return [new Date(t.tag + "T12:00:00Z").getTime(), s / n]; });
      }
      function chart(title, a, b, hint) {
        return PB.panel(title, hint, PB.line({ series: [{ name: "Modell", color: PB.COL.modell, points: cum(a) }, { name: "Marktpreis", color: PB.COL.markt, dash: true, points: cum(b) }],
                                              xType: "time", xFmt: function (v) { return F.day(new Date(v).toISOString()); }, yFmt: function (v) { return F.num(v, 3); }, label: title }));
      }
      out.push(h("div", { class: "grid two" }, chart("Brier-Score über die Zeit", "brier", "brier_market", "laufender Mittelwert, kleiner ist besser"),
                 chart("LogLoss über die Zeit", "logloss", "logloss_market", "bestraft übertriebene Sicherheit stärker")));
      var days = tl.map(function (t) { return [new Date(t.tag + "T12:00:00Z").getTime(), t.brier - t.brier_market]; });
      var cal = (d.kalibrierung || {})[cat] || {};
      out.push(h("div", { class: "grid two" },
        PB.panel("Kalibrierung", "sagt das Modell 30 %, sollte es in etwa 30 % der Fälle eintreten", PB.line({
          series: [{ name: "Modell", color: PB.COL.modell, dots: true, points: (cal.modell || []).map(function (b) { return [b.p, b.ist]; }) },
                   { name: "Marktpreis", color: PB.COL.markt, dots: true, dash: true, points: (cal.markt || []).map(function (b) { return [b.p, b.ist]; }) }],
          ref: [[0, 0], [1, 1]], xType: "num", yMin: 0, yMax: 1, xTitle: "Vorhergesagt", xFmt: function (v) { return F.pct(v); }, yFmt: function (v) { return F.pct(v); }, label: "Kalibrierung", height: 240 })),
        PB.panel("Fehlerentwicklung", "Tageswert: Brier Modell minus Brier Markt (unter 0 = Modell besser)", PB.line({
          series: [{ name: "Abstand zum Markt", color: PB.COL.modell, dots: days.length < 25, points: days }], ref: days.length ? [[days[0][0], 0], [days[days.length - 1][0], 0]] : null,
          xType: "time", xFmt: function (v) { return F.day(new Date(v).toISOString()); }, yFmt: function (v) { return (v >= 0 ? "+" : "−") + F.num(Math.abs(v), 3); }, label: "Fehlerentwicklung" }))));

      var actives = d.modelle.filter(function (m) { return m.active; });
      out.push(PB.panel("Kategorien im Vergleich", "aktives Modell je Bereich", PB.duel(PB.MODELED.map(function (c) {
        var m = actives.filter(function (x) { return x.category === c; })[0] || {};
        return { label: PB.CAT[c], sub: m.model, skill: m.skill, z: m.z, n: m.n, brier: m.brier, brier_market: m.brier_market };
      }))));
      var max = 0.05;
      d.modelle.forEach(function (m) { max = Math.max(max, m.brier || 0, m.brier_market || 0); });
      out.push(PB.panel("Alle Modelle und Versionen", "auch die, die nur als Herausforderer mitlaufen", [h("div", { style: { padding: "0 14px" } }, legendPair()), PB.table(
        [{ label: "Bereich", get: function (m) { return PB.cat(m.category); } }].concat(modelCols(max * 1.05)), d.modelle)], true));
      return out.concat(quality(d));
    }
  };
  function quality(d) {
    var rows = d.datenqualitaet || [];
    var series = PB.MODELED.map(function (c) {
      return { name: PB.CAT[c], color: PB.COL[c], points: rows.filter(function (q) { return q.category === c && q.n; }).map(function (q) { return [new Date(q.tag + "T12:00:00Z").getTime(), q.gueltig / q.n]; }) };
    });
    return [h("div", { class: "grid two" },
      PB.panel("Datenqualität", "Anteil der Prognosen je Tag, die die Prüfung bestanden haben", PB.line({
        series: series, xType: "time", yMin: 0, yMax: 1, xFmt: function (v) { return F.day(new Date(v).toISOString()); }, yFmt: function (v) { return F.pct(v); }, label: "Datenqualität",
        empty: "Noch keine Prognose gespeichert." })),
      PB.panel("Modellversionen", "wann welches Modell aktiv wurde", PB.table([
        { label: "Zeit", get: function (v) { return F.dt(v.ts); } }, { label: "Bereich", get: function (v) { return PB.cat(v.category); } },
        { label: "Modell", get: function (v) { return v.model + "@" + v.version; } }, { label: "Anlass", cls: "hide-s", get: function (v) { return v.reason || v.action; } }
      ], d.versionen), true))];
  }

  // ================================================================ Daten
  PB.pages.daten = {
    title: "Daten", sub: "Woher die Daten kommen, wie aktuell sie sind und wo etwas fehlt", live: true,
    load: function () { return api("/api/data"); },
    render: function (d) {
      function state(s) {
        if (s.paused_s > 0) return PB.chip("pausiert noch " + F.dur(s.paused_s), "warn");
        if (s.streak >= 5) return PB.chip("gestört", "bad");
        if (!s.calls) return PB.chip("heute ungenutzt");
        return s.error_rate > 0.2 ? PB.chip("fehleranfällig", "warn") : PB.chip("erreichbar", "ok");
      }
      var out = [PB.panel("Datenquellen", "Zähler für heute (UTC); alle Quellen sind kostenlos", PB.table([
        { label: "Quelle", get: function (s) { return h("div", { class: "q" }, s.name, h("small", null, s.use)); } },
        { label: "Status", get: state },
        { label: "Letzter Erfolg", num: true, get: function (s) { return F.ago(s.last_ok); } },
        { label: "Abrufe", num: true, get: function (s) { return F.num(s.calls); } },
        { label: "Fehlerquote", num: true, get: function (s) { return s.error_rate == null ? null : F.pct(s.error_rate, 1); } },
        { label: "Antwortzeit", num: true, cls: "hide-s", get: function (s) { return s.avg_ms == null ? null : F.num(s.avg_ms) + " ms"; } },
        { label: "Eigenes Tagesbudget", cls: "hide-s", get: function (s) { return s.day_budget ? PB.meter(s.units / s.day_budget, F.num(s.units) + " von " + F.num(s.day_budget)) : "–"; } },
        { label: "Limit des Anbieters", cls: "hide-s", get: function (s) { return h("span", { class: "dim" }, s.limit); } }
      ], d.sources), true)];
      var bad = d.sources.filter(function (s) { return s.last_msg && (s.paused_s > 0 || s.streak > 0); });
      if (bad.length) out.push(h("div", { class: "callout" }, h("b", null, "Letzte Fehlermeldungen: "), bad.map(function (s) { return h("div", { class: "dim" }, s.name + ": " + s.last_msg + (s.blocked ? " (" + s.blocked + "× selbst zurückgehalten)" : "")); })));

      var w = d.weather, e = d.engine;
      out.push(h("div", { class: "grid two" },
        PB.panel("Stand der Modell-Daten", null, PB.kv([
          ["Wetter-Vorhersagen", w.stationen ? w.ok + " von " + w.stationen + " Stationen aktuell" : null],
          ["Buchmacher-Quoten", e.odds], ["Restliche Abrufe The Odds API (Monat)", e.odds_credits != null ? F.num(e.odds_credits) : null],
          ["Kurse", Object.keys(e.prices || {}).map(function (k) { return k + ": " + e.prices[k]; }).join(" · ") || null],
          ["Optionen (Deribit)", Object.keys(e.options || {}).map(function (k) { return k + ": " + e.options[k]; }).join(" · ") || null],
          ["Telegram", e.telegram]], true)),
        PB.panel("Fehlende Daten", "was gerade nicht bewertet werden kann", [PB.kv([
          ["Offene Märkte ohne Orderbuch", F.num(d.gaps["märkte_ohne_orderbuch"]) + " von " + F.num(d.gaps["märkte_offen"])],
          ["Offene Märkte ohne Enddatum", F.num(d.gaps["märkte_ohne_ende"])],
          ["Prognosen ohne Quellenangabe", F.num(d.gaps.prognosen_ohne_quelle)],
          ["Wetter-Stationen mit Fehler", F.num(Object.keys(w.fehler || {}).length)]]),
          Object.keys(d.gaps["übersprungen"] || {}).length ? [h("h3", null, "Im letzten Lauf nicht bewertet"), PB.kv(Object.keys(d.gaps["übersprungen"]).slice(0, 10).map(function (k) { return [k, F.num(d.gaps["übersprungen"][k])]; }))] : null])));

      out.push(h("div", { class: "grid two" },
        PB.panel("Marktkatalog nach Kategorie", F.num(d.catalog.offen) + " offene Märkte", PB.table([
          { label: "Kategorie", get: function (c) { return PB.cat(c.category); } }, { label: "Märkte", num: true, get: function (c) { return F.num(c.n); } },
          { label: "Volumen 24 h", num: true, get: function (c) { return c.vol != null ? F.big(c.vol) + " $" : null; } }
        ], d.catalog.kategorien, { empty: "Der Katalog ist noch leer." }), true),
        PB.panel("Schlagzeilen", F.num(d.news.neu_24h) + " neue in 24 h, " + F.num(d.news.zuordnungen) + " Zuordnungen zu Märkten", PB.table([
          { label: "Quelle", get: function (n) { return n.source; } }, { label: "Gespeichert", num: true, get: function (n) { return F.num(n.n); } },
          { label: "Neueste", num: true, get: function (n) { return F.ago(n.letzte); } }
        ], d.news.quellen, { empty: "Noch keine Schlagzeile gespeichert.", next: "Abgerufen werden: " + d.feeds.join(", ") + "." }), true)));

      var byDay = {};
      d.history.forEach(function (r) { (byDay[r.day] = byDay[r.day] || { day: r.day, calls: 0, errors: 0, limited: 0, blocked: 0 }); ["calls", "errors", "limited", "blocked"].forEach(function (k) { byDay[r.day][k] += r[k] || 0; }); });
      var daysArr = Object.keys(byDay).sort().reverse().map(function (k) { return byDay[k]; });
      out.push(PB.panel("Quellenhistorie", "alle Quellen zusammen, letzte 14 Tage", PB.table([
        { label: "Tag", get: function (r) { return F.day(r.day + "T12:00:00Z"); } }, { label: "Abrufe", num: true, get: function (r) { return F.num(r.calls); } },
        { label: "Fehler", num: true, get: function (r) { return F.num(r.errors); } }, { label: "vom Anbieter gebremst", num: true, get: function (r) { return F.num(r.limited); } },
        { label: "selbst zurückgehalten", num: true, get: function (r) { return F.num(r.blocked); } }
      ], daysArr, { empty: "Noch keine Tageswerte gespeichert." }), true));
      return out;
    }
  };

  // ================================================================ System
  PB.pages.system = {
    title: "System", sub: "Gesundheit des Dienstes auf Railway", live: true,
    load: function () { return api("/api/health"); },
    render: function (d) {
      var sy = d.system, g = d.guardian, a = g.aktuell || {}, hist = g.verlauf || [];
      var word = { ok: "Läuft", warnung: "Läuft mit Warnung", fehler: "Störung", startet: "Startet" }[sy.status];
      var pts = function (k) { return hist.filter(function (x) { return x[k] != null; }).map(function (x) { return [new Date(x.ts).getTime(), x[k]]; }); };
      var out = [h("section", { class: "panel" }, h("div", { class: "body" }, h("div", { class: "facts" },
        h("div", null, h("div", { class: "l" }, "Zustand"), h("div", { class: "v" }, h("span", { class: "led " + sy.status, style: { display: "inline-block", "margin-right": "8px" } }), word)),
        h("div", null, h("div", { class: "l" }, "Läuft seit"), h("div", { class: "v" }, F.dur(sy.uptime_s))),
        h("div", null, h("div", { class: "l" }, "Letzter Lauf"), h("div", { class: "v" }, PB.val(F.ago(sy.last_cycle)))),
        h("div", null, h("div", { class: "l" }, "Arbeitsspeicher"), h("div", { class: "v" }, PB.val(a.rss_mb != null ? F.num(a.rss_mb) + " MB" : null))),
        h("div", null, h("div", { class: "l" }, "CPU"), h("div", { class: "v" }, PB.val(a.cpu_pct != null ? F.num(a.cpu_pct, 1) + " %" : null))),
        h("div", null, h("div", { class: "l" }, "Datenbank"), h("div", { class: "v" }, PB.val(a.db_mb != null ? F.num(a.db_mb, 1) + " MB" : null))),
        h("div", null, h("div", { class: "l" }, "Platte frei"), h("div", { class: "v" }, PB.val(a.disk_free_gb != null ? F.num(a.disk_free_gb, 2) + " GB" : null)))),
        sy.last_error ? h("div", { class: "callout bad", style: { "margin-top": "12px" } }, h("b", null, "Letzter Fehler im Handels-Lauf: "), sy.last_error) : null))];
      out.push(h("div", { class: "grid two" },
        PB.panel("Arbeitsspeicher", "Megabyte, je Takt gemessen", PB.line({ series: [{ name: "Arbeitsspeicher", color: PB.COL.modell, points: pts("rss_mb") }], xType: "time", yFmt: function (v) { return F.num(v) + " MB"; },
          label: "Arbeitsspeicher", height: 170, empty: "Noch keine Messung.", next: "Der System Guardian misst in jedem Takt." })),
        PB.panel("CPU", "Anteil eines Kerns zwischen zwei Takten", PB.line({ series: [{ name: "CPU", color: PB.COL.modell, points: pts("cpu_pct") }], xType: "time", yMin: 0, yFmt: function (v) { return F.num(v, 1) + " %"; },
          label: "CPU", height: 170, empty: "Noch keine Messung.", next: "Der Anteil lässt sich erst ab dem zweiten Takt berechnen." }))));
      var L = d.loop, integ = g["integrität"], chain = g.kette;
      out.push(h("div", { class: "grid two" },
        PB.panel("Hintergrundprozesse", null, PB.kv([
          ["Schleife", sy.loop_alive == null ? "ausgeschaltet (Testbetrieb)" : sy.loop_alive ? "läuft" : "steht"],
          ["Takte seit dem Start", F.num(L.cycles)], ["Neustarts der Schleife", F.num(L.restarts)],
          ["Takt gerade aktiv seit", L.running_since ? F.dur(Date.now() / 1000 - L.running_since) : "nein"],
          ["Zeitbudget je Takt", F.dur(L.budget_sec)], ["Warnung bei Stillstand nach", F.num(L.watchdog_min) + " min"],
          ["Selbst-Neustart bei Hänger nach", F.num(L.self_restart_min) + " min"],
          ["Fäden", d.threads.join(", ")], ["Speicher-Limit des Containers", a.mem_limit_mb ? F.num(a.mem_limit_mb) + " MB" : null],
          ["Prozessoren", a.cpus], ["Python", d.python], ["Version", d.version]], true)),
        PB.panel("Datenbank", "SQLite auf dem Railway-Volume", [PB.kv([
          ["Schema-Version", g.schema], ["Schreibmodus", String(d.db.wal).toUpperCase()],
          ["Integritätsprüfung", integ ? integ.ergebnis + " (" + F.ago(integ.ts) + ")" : null],
          ["Prüfsumme der Prognose-Historie", chain ? "stimmt bis Zeile " + F.num(chain.bis) + " (" + F.ago(chain.ts) + ")" : null],
          ["Sicherungen", g.sicherungen.length ? g.sicherungen.length + ", neueste " + F.ago(g.sicherungen[0].zeit) + " (" + F.num(g.sicherungen[0].mb, 1) + " MB)" : null]], true),
          h("details", { class: "tbl" }, h("summary", null, "Zeilen je Tabelle"), PB.kv(Object.keys(d.db.tabellen).map(function (k) { return [k, F.num(d.db.tabellen[k])]; }))),
          g.sicherungen.length ? h("details", { class: "tbl" }, h("summary", null, "Sicherungsdateien"), PB.kv(g.sicherungen.map(function (b) { return [b.datei, F.num(b.mb, 1) + " MB"]; }))) : null])));
      var rw = d.railway;
      out.push(h("div", { class: "grid two" },
        PB.panel("Railway", null, rw ? PB.kv([["Umgebung", rw.RAILWAY_ENVIRONMENT_NAME], ["Dienst", rw.RAILWAY_SERVICE_NAME], ["Region", rw.RAILWAY_REPLICA_REGION],
          ["Deployment", rw.RAILWAY_DEPLOYMENT_ID], ["Git-Stand", rw.RAILWAY_GIT_COMMIT_SHA], ["Zweig", rw.RAILWAY_GIT_BRANCH], ["Volume", rw.RAILWAY_VOLUME_MOUNT_PATH]], true)
          : PB.empty("Dieser Prozess läuft nicht auf Railway oder Railway gibt keine Angaben weiter.")),
        PB.panel("Telegram und Zugang", null, PB.kv([
          ["Telegram", d.telegram.aktiv ? "aktiv" : "nicht eingerichtet"], ["Letzter Versand", d.telegram.status],
          ["Gesendet seit dem Start", F.num(d.telegram.gesendet || 0)], ["Unterdrückt (Dopplung, Ruhezeit, Limit)", F.num(d.telegram["unterdrückt"] || 0)],
          ["Dashboard-Anmeldung", d.auth ? "aktiv" : "AUS – jeder mit dem Link kann zugreifen"]], true))));
      out.push(PB.panel("Warnungen", "Stufe hoch und kritisch", PB.feed(d.events, "Keine Warnung gespeichert."), true));
      out.push(PB.panel("Fehlerprotokoll", "aus dem Handels-Lauf", d.errors.length ? h("ul", { class: "feed" }, d.errors.map(function (l) {
        return h("li", null, h("span", { class: "lvl kritisch" }), h("div", { class: "d", style: { color: "var(--ink)" } }, l.msg), h("time", null, F.dt(l.ts)));
      })) : PB.empty("Im Protokoll steht kein Fehler."), true));
      return out;
    }
  };

  // ================================================================ Claude-Berichte
  PB.pages.berichte = {
    title: "Claude-Berichte", sub: "Lagebericht zweimal pro Woche – Grundlage für die Besprechung mit Claude", live: false,
    load: function (r) {
      return api("/api/reports").then(function (list) {
        var id = r.arg || (list.reports[0] && list.reports[0].id);
        return id ? api("/api/report/" + id).then(function (one) { return { list: list, one: one }; }) : { list: list, one: null };
      });
    },
    render: function (d) {
      var reps = d.list.reports, recs = d.list.recommendations, one = d.one && d.one.report, out = [];
      var openRecs = recs.filter(function (x) { return x.status === "offen"; }), closed = recs.filter(function (x) { return x.status !== "offen"; });
      out.push(h("div", { class: "row", style: { "justify-content": "space-between" } },
        h("span", { class: "dim" }, "Automatisch mittwochs und sonntags ab " + d.list.plan.stunde + " Uhr. Der Bericht ändert nichts am System."),
        h("button", { class: "btn", on: { click: function (e) {
          e.target.disabled = true;
          api("/api/reports/generate", {}).then(function (x) { PB.toast("Bericht erstellt"); location.hash = "#/berichte/" + x.id; }).catch(function (er) { PB.toast(er.message, true); e.target.disabled = false; });
        } } }, "Bericht jetzt erstellen")));
      function recRow(x) {
        var btn = function (label, status) { return h("button", { class: "btn small", on: { click: function () { act(api("/api/recommendation/" + x.id, { status: status, note: x.note || "" }), "Gespeichert"); } } }, label); };
        return h("div", { class: "rec" }, h("div", null, x.text, h("div", { class: "dim2", style: { "font-size": "12.5px" } }, "aus Bericht " + x.report_id + (x.status !== "offen" ? " · " + x.status + " " + (F.ago(x.updated_at) || "") : ""))),
                 h("div", { class: "row" }, x.status === "offen" ? [btn("Erledigt", "erledigt"), btn("Verwerfen", "verworfen")] : btn("Wieder öffnen", "offen")));
      }
      out.push(PB.panel("Offene Empfehlungen", openRecs.length ? openRecs.length + " offen" : "", openRecs.length ? h("div", null, openRecs.map(recRow))
        : PB.empty(reps.length ? "Es gibt keine offene Empfehlung." : "Noch kein Bericht vorhanden.", reps.length ? null : "Der erste entsteht am nächsten Mittwoch oder Sonntag – oder jetzt über den Knopf oben."), true));
      if (one) {
        var note = h("textarea", { class: "in", rows: "3", style: { width: "100%" }, placeholder: "Ergebnis der Besprechung, Entscheidungen, offene Fragen", "aria-label": "Ergebnis der Besprechung" }, one.note || "");
        var status = ui.select(one.status, [["offen", "Offen"], ["besprochen", "Besprochen"], ["abgeschlossen", "Abgeschlossen"]], function () {}, "Status");
        out.push(h("section", { class: "panel" }, h("header", null, h("h2", null, "Bericht " + one.id), h("span", { class: "row" },
            h("span", { class: "hint" }, F.dt(one.created_at) + (one.kind === "manuell" ? ", von Hand erstellt" : "")),
            h("button", { class: "btn small", on: { click: function () {
              (navigator.clipboard ? navigator.clipboard.writeText(one.markdown) : Promise.reject()).then(function () { PB.toast("Bericht kopiert – in Claude einfügen und besprechen"); })
                .catch(function () { PB.toast("Kopieren nicht möglich – Text unten markieren", true); });
            } } }, "Text kopieren"))),
          h("div", { class: "body" }, PB.md(one.markdown),
            h("h3", null, "Besprechung"), h("div", { class: "row", style: { "margin-bottom": "8px" } }, h("span", { class: "dim" }, "Status"), status,
              h("button", { class: "btn small", on: { click: function () { act(api("/api/report/" + one.id, { status: status.value, note: note.value }), "Gespeichert"); } } }, "Speichern")), note)));
      }
      out.push(h("div", { class: "grid two" },
        PB.panel("Alle Berichte", null, PB.table([
          { label: "Bericht", get: function (r) { return h("a", { href: "#/berichte/" + r.id }, r.title); } },
          { label: "Status", get: function (r) { return PB.chip(r.status, r.status === "offen" ? "warn" : "ok"); } }
        ], reps, { empty: "Noch kein Bericht vorhanden." }), true),
        PB.panel("Abgeschlossene Empfehlungen", null, closed.length ? h("div", null, closed.slice(0, 20).map(recRow)) : PB.empty("Noch keine Empfehlung erledigt oder verworfen."), true)));
      return out;
    }
  };

  // ================================================================ Einstellungen
  PB.pages.einstellungen = {
    title: "Einstellungen", sub: "Änderungen gelten sofort und werden protokolliert", live: false,
    load: function () { return api("/api/settings"); },
    render: function (d) {
      var changes = {}, bar = h("div", { class: "savebar", hidden: true }), groups = {}, out = [];
      function touched() {
        var n = Object.keys(changes).length;
        bar.hidden = !n;
        bar.replaceChildren(h("span", null, n + (n === 1 ? " Änderung" : " Änderungen") + " noch nicht gespeichert"),
          h("span", { class: "row" }, h("button", { class: "btn", on: { click: function () { PB.reload(); } } }, "Verwerfen"),
            h("button", { class: "btn primary", on: { click: function () { act(api("/api/settings", changes), "Einstellungen gespeichert"); } } }, "Änderungen speichern")));
      }
      function set(s, v) { if (String(v) === String(s.value)) delete changes[s.key]; else changes[s.key] = v; touched(); }
      d.spec.forEach(function (s) {
        var ctl;
        if (s.type === "bool") {
          ctl = h("button", { type: "button", class: "switch", role: "switch", "aria-checked": s.value ? "true" : "false", "aria-label": s.desc, on: { click: function () {
            var on = ctl.getAttribute("aria-checked") !== "true"; ctl.setAttribute("aria-checked", on ? "true" : "false"); set(s, on);
          } } });
        } else if (s.type === "choice") {
          ctl = ui.select(s.value, s.choices.map(function (c) { return [c, c]; }), function (v) { set(s, v); }, s.desc);
        } else {
          ctl = h("input", { type: "number", value: s.value, min: s.min, max: s.max, step: s.type === "int" ? 1 : "any", "aria-label": s.desc,
                             on: { input: function (e) { set(s, e.target.value === "" ? s.value : Number(e.target.value)); } } });
        }
        (groups[s.group] = groups[s.group] || []).push(h("div", { class: "field" }, h("label", null, s.desc), ctl,
          s.type !== "bool" && s.type !== "choice" ? h("div", { class: "d" }, "erlaubt: " + F.num(s.min, s.min % 1 ? 2 : 0) + " bis " + F.num(s.max, s.max % 1 ? 2 : 0)) : null));
      });
      var names = { Analyse: "Analysefrequenz und Prüfung", Kategorien: "Kategorien im Paper-Trading", Ressourcen: "Ressourcen", Benachrichtigungen: "Benachrichtigungen (Telegram)", Dashboard: "Dashboard" };
      Object.keys(groups).forEach(function (g) { out.push(PB.panel(names[g] || g, null, h("div", null, groups[g]), true)); });
      out.push(bar);
      out.push(h("div", { class: "grid two" },
        PB.panel("Modellversionen", "umstellen im Lernzentrum", [PB.table([
          { label: "Bereich", get: function (m) { return PB.cat(m.category); } }, { label: "Aktives Modell", get: function (m) { return m.model + "@" + m.version; } },
          { label: "Mischung", get: function (m) { return m.mix_w != null && m.mix_w < 0.999 ? F.pct(m.mix_w) + " Modell" : "keine"; } }], d.models),
          h("div", { style: { padding: "8px 14px 4px" } }, h("a", { href: "#/lernen" }, "Vorschläge und Rückholung im Lernzentrum"))], true),
        PB.panel("Zugangsdaten", "werden nur in Railway als Variablen gepflegt, nie hier", PB.kv(Object.keys(d.secrets).map(function (k) {
          return [k, d.secrets[k] ? PB.chip("eingerichtet", "ok") : PB.chip("fehlt", "warn")];
        })))));
      out.push(PB.panel("Datenquellen", "Budgets schützen vor Sperren durch die Anbieter", PB.table([
        { label: "Quelle", get: function (s) { return h("div", { class: "q" }, s.name, h("small", null, s.use)); } },
        { label: "Eigenes Tagesbudget", num: true, get: function (s) { return s.day_budget ? F.num(s.day_budget) : "–"; } },
        { label: "Limit des Anbieters", get: function (s) { return h("span", { class: "dim" }, s.limit); } }], d.sources), true));
      out.push(PB.panel("Feste Systemkonfiguration", "änderbar über Railway-Variablen, siehe README", PB.kv(Object.keys(d.fixed).map(function (k) { return [k, String(d.fixed[k])]; }), true)));
      return out;
    }
  };
})();
