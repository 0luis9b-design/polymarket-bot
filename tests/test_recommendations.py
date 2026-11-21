"""Empfehlungen räumen sich selbst auf (21.11.): Im Dashboard standen 19 offene Empfehlungen, fast alle veraltet –
dieselbe Binance-Meldung dreimal, „Konto steht still“ für Konten, die längst wieder liefen, schon freigegebene Vorschläge."""
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("AUTOSTART", "0")

from polybot import reports  # noqa: E402
from polybot.store import Store  # noqa: E402

NOW = datetime(2026, 11, 21, 17, 0, tzinfo=timezone.utc)


def _open(st):
    return {r["text"]: r for r in st.q("SELECT * FROM recommendations WHERE status='offen'")}


def test_same_topic_with_new_numbers_is_one_recommendation():
    a = "Datenquelle Binance: Fehlerquote 50 % bei 968 Abrufen – Ursache prüfen."
    b = "Datenquelle Binance: Fehlerquote 50 % bei 4032 Abrufen – Ursache prüfen."
    assert reports.rec_key(a) == reports.rec_key(b)
    assert reports.rec_key("Offener Vorschlag #8 wartet auf Entscheidung: mischen (50 % Modell)") != \
        reports.rec_key("Offener Vorschlag #9 wartet auf Entscheidung: mischen (25 % Modell)")
    assert reports.rec_key("Wetter: erst 77 Prognosen") != reports.rec_key("Sport: erst 77 Prognosen")


def test_new_report_replaces_closes_and_keeps(tmp_path):
    st = Store(str(tmp_path / "r.sqlite"))
    binance_old = "Datenquelle Binance: Fehlerquote 50 % bei 1072 Abrufen – Ursache prüfen."
    still = "Konto „Haupt · E-Sport“ steht seit dem Verlust-Limit still. Erst die Ursache klären."
    summe = "Die Prüfregel „Summe der Antworten“ hat 653 Prognosen gesperrt, bei denen das Modell besser lag als der Markt."
    reports.sync_recommendations(st, 1, [binance_old, still, summe, summe], NOW - timedelta(days=6))
    assert len(_open(st)) == 3                                                       # doppelte Zeile nur einmal
    st.x("UPDATE recommendations SET status='verworfen', updated_at=? WHERE text=?", ((NOW - timedelta(days=2)).isoformat(), still))
    binance_new = "Datenquelle Binance: Fehlerquote 50 % bei 968 Abrufen – Ursache prüfen."
    n_new, n_repl, n_done = reports.sync_recommendations(st, 2, [binance_new, summe, still], NOW)
    op = _open(st)
    assert set(op) == {binance_new, summe} and op[summe]["report_id"] == 1          # unveränderte bleibt aus Bericht 1
    assert (n_new, n_repl, n_done) == (1, 1, 0)                                      # „still“ verworfen: kommt 7 Tage nicht wieder
    old = st.q1("SELECT * FROM recommendations WHERE text=?", (binance_old,))
    assert old["status"] == "erledigt" and old["note"] == "automatisch: ersetzt durch Bericht #2"
    # nächster Bericht nennt Binance nicht mehr -> trifft nicht mehr zu
    reports.sync_recommendations(st, 3, [summe], NOW + timedelta(days=3))
    gone = st.q1("SELECT * FROM recommendations WHERE text=?", (binance_new,))
    assert gone["status"] == "erledigt" and "trifft im Bericht #3 nicht mehr zu" in gone["note"]
    assert set(_open(st)) == {summe}


def test_one_time_tidy_uses_the_latest_report(tmp_path):
    st = Store(str(tmp_path / "t.sqlite"))
    st.x("INSERT INTO reports(created_at, kind, title, markdown) VALUES(?,?,?,?)",
         ((NOW - timedelta(days=3)).isoformat(), "geplant", "B1", "## 12. Verbesserungsvorschläge\n- alt"))
    md = ("# Claude-Review\n## 11. Offene technische Probleme\n- Keine bekannten.\n\n## 12. Verbesserungsvorschläge\n"
          "- Sport: erst 75 Prognosen mit Ergebnis.\n- Datenquelle Binance: Fehlerquote 50 % bei 968 Abrufen – Ursache prüfen.\n\n"
          "## 13. Empfehlungen für die nächsten Entwicklungsschritte\n- Roadmap durchgehen.")
    st.x("INSERT INTO reports(created_at, kind, title, markdown) VALUES(?,?,?,?)", ((NOW - timedelta(days=1)).isoformat(), "geplant", "B2", md))
    for rid, text in ((1, "Konto „Haupt · E-Sport“ steht seit dem Verlust-Limit still."), (1, "Sport: erst 19 Prognosen mit Ergebnis."),
                      (1, "Datenquelle Binance: Fehlerquote 50 % bei 1072 Abrufen – Ursache prüfen."),
                      (2, "Sport: erst 75 Prognosen mit Ergebnis."), (2, "Datenquelle Binance: Fehlerquote 50 % bei 968 Abrufen – Ursache prüfen.")):
        st.x("INSERT INTO recommendations(report_id, text, updated_at) VALUES(?,?,?)", (rid, text, NOW.isoformat()))
    assert reports.recs_from_markdown(md) == ["Sport: erst 75 Prognosen mit Ergebnis.",
                                             "Datenquelle Binance: Fehlerquote 50 % bei 968 Abrufen – Ursache prüfen."]
    reports.tidy_once(st, NOW)
    assert set(_open(st)) == {"Sport: erst 75 Prognosen mit Ergebnis.", "Datenquelle Binance: Fehlerquote 50 % bei 968 Abrufen – Ursache prüfen."}
    assert st.kv_get("recs_tidy_v1")
    st.x("UPDATE recommendations SET status='offen' WHERE report_id=1")
    reports.tidy_once(st, NOW)                                                       # nur einmal
    assert len(_open(st)) == 5
