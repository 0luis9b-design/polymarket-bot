# Technische Schulden

Schwere: **H** = kann Geld-/Datenqualität oder Betrieb direkt schädigen · **M** = bremst oder ist riskant · **N** = unschön.
Status wird bei jeder Änderung gepflegt.

| Nr | Schwere | Schuld | Beleg | Status |
|---|---|---|---|---|
| T1 | H | Kein zentrales Budget/Rate-Limit pro Datenquelle | Ausfall 13.11. (Open-Meteo) | erledigt – `sources.py`: Tages-/Minutenbudget, Pause nach 429, Statistik je Tag |
| T2 | H | Keine Validierung der Prognosen vor dem Handeln | Wetter: „Vorsprung“ 27–49 Pkt, Verluste | erledigt – Validation Agent (Abweichungs-Deckel 25 Pkt, Frische, Summen) |
| T3 | H | Kein Backup der Datenbank | nur Volume | erledigt – täglich, 7 Stück, plus Sicherung vor jeder Migration. Rest: liegt auf demselben Volume (R3) |
| T4 | H | Geheimnisse können in Logs/Status erscheinen (Telegram-Token, Odds-Key in Fehlertexten) | `http.py`, `notify.py` | erledigt – alle Fehlertexte laufen durch `redact` |
| T5 | H | Prognose-Historie unvollständig und veränderbar | Tabelle `predictions` | erledigt – Tabelle `forecasts`: nur Anfügen (Trigger), Prüfsummen-Kette. `predictions` bleibt für die Paper-Auswertung |
| T6 | M | Schema-Änderungen ohne Versionsnummer | `store.py` `NEW_COLUMNS` | erledigt – `migrations.py`, 8 Schritte |
| T7 | M | Selbstkorrektur greift ohne Freigabe ein | `engine.update_weights` | erledigt – Mischung nur nach Freigabe; der Stand aus v1.3 wurde einmalig ins Register übernommen |
| T8 | M | Risikobremse flattert innerhalb eines Tages | Logs 13.11. | erledigt – rastet bis zum nächsten Tag ein |
| T9 | M | `engine.py` macht alles (1.047 Zeilen) | – | teilweise – Katalog, Modellwahl, Prüfung, Historie, Lernen sind eigene Agenten; `engine.py` hat weiter ~1.080 Zeilen |
| T10 | M | `/api/state` schickt alles auf einmal (bis 8.000 Wetten, alle 60 s) | `app.py` | teilweise – Zentrale lädt je Seite; `/paper` lädt weiterhin alles auf einmal |
| T11 | M | Schleifen-Thread wird nach einem Absturz nicht neu gestartet; kein Zeitlimit pro Lauf | `app.py` | erledigt – Faden wird neu gestartet, Notfall-Neustart nach 45 min Hänger, Zeitbudget je Takt |
| T12 | M | POST ohne CSRF-Schutz, keine Sperre bei Fehlanmeldungen, keine Sicherheits-Header | `app.py` | erledigt |
| T13 | M | `/health` gibt Fehlertexte ohne Anmeldung aus; `/api/scan` mit Schlüssel in der URL | `app.py` | erledigt |
| T14 | M | Open-Meteo: alle Stationen in einem Lauf (Minuten-Limit 600) | `weather_model.py` | erledigt – Minutenbudget 480 Einheiten in `sources.py` |
| T15 | M | Wetter-Modell ohne Stations-Korrektur, Streuung geschätzt statt gemessen | Verluste 13.11. | teilweise – `weather.emp` lernt Korrektur und Streuung und läuft als Herausforderer, sobald 30 Messungen da sind; aktiv erst nach Freigabe |
| T16 | N | JS/CSS inline in einer 733-Zeilen-Datei | `templates/index.html` | erledigt – `static/*.js`, `static/*.css`, strenge CSP |
| T17 | N | Keine Abhängigkeits-Prüfung in der CI, Deploy nicht an Tests gekoppelt | `ci.yml`, Railway | teilweise – `pip-audit` läuft in der CI (meldet, blockiert nicht); Deploy ist weiter nicht an die Tests gekoppelt |
| T18 | N | Test-Daten sind handgebaut, keine aufgezeichneten echten API-Antworten | `tests/` | offen |
| T19 | N | Zwischenspeicher für Wetter geht bei jedem Deploy verloren | `weather_model._cache` | offen |
| T20 | N | Limit-Order-Füllung ist optimistisch simuliert | `engine.do_maker` | dokumentiert |
| T21 | M | Sicherungen liegen auf demselben Volume wie die Datenbank | `guardian.py` | offen – ein zweiter Ort wäre ein externer Dienst (Kosten/Zugangsdaten), deshalb nicht ohne Absprache |
| T22 | N | Dashboard-Skripte ohne automatische Browser-Tests in der CI | `static/` | offen – geprüft von Hand mit Playwright (alle Seiten, zwei Breiten) |
| T23 | N | Nachrichten nur per Stichwort zugeordnet, nur BBC | `agents/news.py` | bewusst – siehe Roadmap „Bewusst nicht gebaut“ |
| T24 | N | Für Politik, Wirtschaft, Kultur usw. gibt es keine eigenen Prognosen, nur Beobachtung und Backtests | `agents/scanner.py`, `agents/strategy.py` | bewusst – ohne Datenmodell wäre es geraten |
