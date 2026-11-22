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
| T15 | M | Wetter-Modell ohne Stations-Korrektur, Streuung geschätzt statt gemessen | Verluste 13.11. | teilweise – `weather.emp` lernt Korrektur und Streuung (Herausforderer, aktiv erst nach Freigabe); am Messtag korrigiert `weather.live` die Vorhersage mit den gemessenen Werten |
| T16 | N | JS/CSS inline in einer 733-Zeilen-Datei | `templates/index.html` | erledigt – `static/*.js`, `static/*.css`, strenge CSP |
| T17 | N | Keine Abhängigkeits-Prüfung in der CI, Deploy nicht an Tests gekoppelt | `ci.yml`, Railway | teilweise – `pip-audit` läuft in der CI (meldet, blockiert nicht); Deploy ist weiter nicht an die Tests gekoppelt |
| T18 | N | Test-Daten sind handgebaut, keine aufgezeichneten echten API-Antworten | `tests/` | erledigt – echte, gekürzte Antworten von Polymarket (Gamma, CLOB) und METAR in `tests/fixtures/`; Open-Meteo bleibt synthetisch (vom Browser aus nicht abrufbar) |
| T19 | N | Zwischenspeicher für Wetter geht bei jedem Deploy verloren | `weather_model._cache` | erledigt – Vorhersagen je Station liegen in der Datenbank und überleben den Neustart |
| T20 | N | Limit-Order-Füllung ist optimistisch simuliert | `engine.do_maker` | dokumentiert |
| T21 | M | Sicherungen liegen auf demselben Volume wie die Datenbank | `guardian.py` | offen – ein zweiter Ort wäre ein externer Dienst (Kosten/Zugangsdaten), deshalb nicht ohne Absprache |
| T22 | N | Dashboard-Skripte ohne automatische Browser-Tests in der CI | `static/` | erledigt – Playwright prüft alle Seiten in zwei Breiten in der GitHub-Prüfung (`tests/browser/`) |
| T23 | N | Nachrichten nur per Stichwort zugeordnet, nur BBC | `agents/news.py` | bewusst – siehe Roadmap „Bewusst nicht gebaut“ |
| T24 | N | Für Politik, Wirtschaft, Kultur usw. gibt es keine eigenen Prognosen, nur Beobachtung und Backtests | `agents/scanner.py`, `agents/strategy.py` | bewusst – ohne Datenmodell wäre es geraten |
| T25 | N | Basisraten nur je Bereich und Kursstufe, noch nicht je Unterbereich oder Markttyp | `base_model.py` | bewusst – erst wenn genug Daten da sind (Roadmap „Nächste Schritte“ 6) |
| T26 | M | Datensatz entschiedener Märkte wird nach dem Endvolumen ausgewählt (≥ 2.000 $). Eingetroffene Außenseiter werden mehr gehandelt und landen eher im Datensatz – verzerrt die Basisraten nach oben | `agents/strategy.py` | erledigt 2.7 – keine Volumen-Auswahl, Kurs zählt nur bei Handel davor (`sel = 2`); Deckel, Stabilität und Wächter bleiben als Sicherung |
| T26 | N | Alte Wetten (vor 14.11.) haben keine vollständigen Einstiegs-Daten | `positions` | bewusst – damals nicht gespeichert; die Detailseite sagt das |
| T27 | M | Lernsystem verglich Herausforderer immer mit dem reinen Modell, auch wenn eine Mischung gehandelt wurde – dieselbe Mischung wurde nach jeder Freigabe neu vorgeschlagen; abgelehnte kamen in der nächsten Stunde wieder | Bericht 18.11.: 9 Wetter-Vorschläge in 3 Tagen | erledigt 2.5 – Maßstab ist die gehandelte Variante, Sperrfrist 7 Tage nach Ablehnung |
| T28 | N | Bericht markierte reines Modell und alle Mischungen als „aktiv“ und warnte deshalb „Handel aussetzen“ für ein Modell, das gar nicht direkt gehandelt wurde | Bericht 18.11. | erledigt 2.5 – Stern nur an der gehandelten Variante |
| T29 | M | Strategie-Agent brach bei Polymarket-Fehler 500 ganz ab (31 von 137 Läufen) | Logs 15.–18.11. | abgefedert 2.5 – Portion endet, Rest läuft; Ursache noch offen (Abfragen werden gezählt) |
| T30 | N | Krypto-Kurse fragten zuerst api.binance.com (sperrt US-Server) – jeder zweite Abruf ein Fehler | Bericht 18.11.: 50 % | erledigt 2.5 – Binance-Vision zuerst |
| T31 | N | Nach jedem Neustart über 1.000 gleiche METAR-Zeilen („Minuten-Limit“) in einer Sekunde – Railway verwarf Log-Meldungen, darunter hätten auch Berichtszeilen sein können | Logs 18.11. 23:02: 169 verworfen | erledigt 2.5.1 – eine Sammelzeile alle 10 Minuten |
| T32 | M | Summen-Prüfung lief auch bei Kurs-Leitern („Bitcoin über 64k / 66k / 68k“), weil `enableNegRisk` am Ereignis als „genau eine trifft zu“ galt – 653 gute Prognosen gesperrt; dieselbe Angabe hätte auch Schein-Arbitrage erzeugen können | Bericht 18.11. | erledigt 2.6 – Angabe am Markt, Prüfung nur bei Marktsumme ≈ 100 % |
| T33 | N | Empfehlungen sammelten sich an (19 offen, meist veraltet oder doppelt) | Dashboard 21.11. | erledigt 2.6 – Abgleich nach jedem Bericht |
| T34 | N | Bericht-Empfehlungen rechneten über alle Daten seit Start – „Summe der Antworten“ (659 alte Fälle) und „Wetten gewinnen seltener als erwartet“ blieben offen, obwohl die Ursache behoben war | Bericht #4, 22.11. | erledigt 2.8 – nur Daten unter den aktuellen Regeln |
