# Technische Schulden

Schwere: **H** = kann Geld-/Datenqualität oder Betrieb direkt schädigen · **M** = bremst oder ist riskant · **N** = unschön.
Status wird bei jeder Änderung gepflegt.

| Nr | Schwere | Schuld | Beleg | Status |
|---|---|---|---|---|
| T1 | H | Kein zentrales Budget/Rate-Limit pro Datenquelle | Ausfall 13.11. (Open-Meteo) | offen |
| T2 | H | Keine Validierung der Prognosen vor dem Handeln | Wetter: „Vorsprung“ 27–49 Pkt, Verluste | offen |
| T3 | H | Kein Backup der Datenbank | nur Volume | offen |
| T4 | H | Geheimnisse können in Logs/Status erscheinen (Telegram-Token, Odds-Key in Fehlertexten) | `http.py`, `notify.py` | offen |
| T5 | H | Prognose-Historie unvollständig und veränderbar | Tabelle `predictions` | offen |
| T6 | M | Schema-Änderungen ohne Versionsnummer | `store.py` `NEW_COLUMNS` | offen |
| T7 | M | Selbstkorrektur greift ohne Freigabe ein | `engine.update_weights` | offen |
| T8 | M | Risikobremse flattert innerhalb eines Tages | Logs 13.11. | offen |
| T9 | M | `engine.py` macht alles (1.047 Zeilen) | – | offen |
| T10 | M | `/api/state` schickt alles auf einmal (bis 8.000 Wetten, alle 60 s) | `app.py` | offen |
| T11 | M | Schleifen-Thread wird nach einem Absturz nicht neu gestartet; kein Zeitlimit pro Lauf | `app.py` | offen |
| T12 | M | POST ohne CSRF-Schutz, keine Sperre bei Fehlanmeldungen, keine Sicherheits-Header | `app.py` | offen |
| T13 | M | `/health` gibt Fehlertexte ohne Anmeldung aus; `/api/scan` mit Schlüssel in der URL | `app.py` | offen |
| T14 | M | Open-Meteo: alle Stationen in einem Lauf (Minuten-Limit 600) | `weather_model.py` | offen |
| T15 | M | Wetter-Modell ohne Stations-Korrektur, Streuung geschätzt statt gemessen | Verluste 13.11. | offen |
| T16 | N | JS/CSS inline in einer 733-Zeilen-Datei | `templates/index.html` | offen |
| T17 | N | Keine Abhängigkeits-Prüfung in der CI, Deploy nicht an Tests gekoppelt | `ci.yml`, Railway | offen |
| T18 | N | Test-Daten sind handgebaut, keine aufgezeichneten echten API-Antworten | `tests/` | offen |
| T19 | N | Zwischenspeicher für Wetter geht bei jedem Deploy verloren | `weather_model._cache` | offen |
| T20 | N | Limit-Order-Füllung ist optimistisch simuliert | `engine.do_maker` | dokumentiert |
