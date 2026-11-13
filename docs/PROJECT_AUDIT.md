# Projekt-Audit – Polymarket-Bot

Stand: 14.11.2026, Commit `57e3432`. Alles hier ist am Code, an den Railway-Logs und an den echten
Schnittstellen geprüft – nichts ist angenommen. Wo etwas nicht prüfbar war, steht das dabei.

## 1. Was das Projekt ist
Ein Forschungs- und Paper-Trading-System für Polymarket. Es liest öffentliche Marktdaten, berechnet
eigene Wahrscheinlichkeiten und handelt **nur mit Spielgeld** in 11 Testkonten. Es gibt keine Wallet,
keine Signaturen, keine Orders an Polymarket – der Code enthält keinen einzigen schreibenden Aufruf
an Polymarket (geprüft: nur `GET` in `polybot/http.py`, einzige `POST`-Stelle ist Telegram).

## 2. Technik
| Bereich | Befund |
|---|---|
| Sprache | Python 3.12 (Backend), HTML/CSS/JavaScript ohne Framework (Dashboard) |
| Umfang | 4.574 Zeilen: `polybot/` 2.857, `app.py` 227, Dashboard 733, Tests 779 |
| Web | Flask 3.1 + Gunicorn (1 Worker, 4 Threads) |
| Abhängigkeiten | nur `flask`, `requests`, `gunicorn`, `tzdata` |
| Datenbank | SQLite, eine Datei auf dem Railway-Volume `/data` |
| Tests | 56 Tests (pytest), laufen in 2 s, GitHub-Action bei jedem Push |
| Betrieb | Railway, Region `sfo`, 1 Replica, Healthcheck `/health`, Neustart bei Absturz |

## 3. Architektur (Ist)
Ein einziger Prozess. Beim Import von `app.py` starten zwei Hintergrund-Threads:
- **Schleife** (`_loop`): alle 10 min `Engine.run_cycle()`.
- **Wachhund** (`_watchdog`): Telegram-Warnung nach 30 min ohne fertigen Lauf.

`polybot/engine.py` (1.047 Zeilen) macht in einem Lauf alles nacheinander: abrechnen → Märkte holen →
Wahrscheinlichkeiten → je Konto Ausstieg/Einstieg → Arbitrage → Bewertung → Zusammenfassungen.
Es gibt keine getrennten Agenten, keine Warteschlange, keinen zweiten Worker.

| Modul | Aufgabe | Zustand |
|---|---|---|
| `polymarket.py` | Gamma-API (Märkte), CLOB (Orderbuch), Kauf/Verkauf-Simulation | funktioniert |
| `crypto_model.py` + `options.py` + `prices.py` | Krypto-Wahrscheinlichkeit aus Deribit-Optionen, Ersatz: Kurshistorie | funktioniert, liegt nah am Markt |
| `sports_model.py` | faire Quote aus Buchmachern (The Odds API) | funktioniert, wenig Daten (500 Abrufe/Monat) |
| `weather_model.py` | Temperatur-Märkte aus Open-Meteo-Ensemble | **läuft, aber schlecht kalibriert** (siehe 6) |
| `engine.py` | Konten, Einsatz, Ausstieg, Arbitrage, Limit-Orders, Risikobremse | funktioniert, zu groß |
| `store.py` | SQLite-Zugriff | funktioniert, ohne Migrations-System |
| `notify.py` | Telegram | funktioniert, ohne Prioritäten |
| `templates/index.html` | Dashboard (eine Seite, 9 Reiter) | funktioniert, alles in einer Datei |

## 4. Datenbank (Ist)
Tabellen: `kv`, `positions`, `orders`, `predictions`, `log`, `equity`, `equity_acc`.
- Spalten werden beim Start per `ALTER TABLE` ergänzt – es gibt **keine Versionsnummer** des Schemas.
- `predictions`: eine Zeile pro Markt (erste Sichtung), ohne Modell, ohne Version, ohne Quellen;
  das Ergebnis wird **in dieselbe Zeile geschrieben** (veränderbar).
- Kein Preisverlauf, kein Marktkatalog, keine Agenten-/Fehler-Historie.
- **Kein Backup.** Das Volume ist die einzige Kopie.

## 5. Datenquellen (alle live geprüft am 14.11.)
| Quelle | Wofür | Kosten/Limit | Befund |
|---|---|---|---|
| Polymarket Gamma `/events`, `/markets` | Märkte | frei | ok; `/markets` max. 100 pro Seite; geschlossene Märkte nur mit `closed=true`; `/events` sehr groß (100 Ereignisse = 29 MB) |
| Polymarket CLOB `/book`, `/prices-history` | Orderbuch, Preisverlauf | frei | ok; Preisverlauf auch für geschlossene Märkte (stündlich) |
| Binance/Coinbase/Kraken | Krypto-Kurse | frei | Binance sperrt US-Server → Ersatz „Binance-Vision“ läuft |
| Deribit | Optionen BTC/ETH/SOL/XRP | frei | ok |
| The Odds API | Buchmacher-Quoten | 500 Abrufe/Monat gratis | ok, 469 übrig |
| Open-Meteo Ensemble | Wetter | 10.000 gewichtete Abrufe/Tag, 600/min | **Kontingent am 13.11. überschritten** |
| Telegram | Meldungen | frei | ok |
| GDELT (Nachrichten) | geprüft für News | frei | **unbrauchbar: 3 Einzelabrufe, alle HTTP 429** |
| BBC RSS (6 Feeds) | geprüft für News | frei | ok, Standard-RSS |

## 6. Befunde aus dem Live-Betrieb (Railway-Logs 12.–14.11.)
1. **Ausfall am 13.11., 10–18 Uhr:** nur 3 statt ~48 Läufe. Ursache: Open-Meteo-Kontingent leer,
   danach für jeden von ~1.800 Wetter-Märkten ein neuer Fehlversuch. Behoben (Cache 3 h, Fehler-Pause),
   aber es gibt **kein zentrales Budget pro Quelle** – derselbe Fehler kann bei jeder Quelle wieder passieren.
2. **Wetter-Modell verliert:** „Nein“ auf den Favoriten-Bereich wurde in Peking, Kapstadt, Helsinki,
   Manila, Chongqing, Singapur, Seoul, Dschidda, Ankara verloren (je bis −60 $ pro Konto), gewonnen in
   Lucknow, Karatschi, Moskau, Kuala Lumpur, Tel Aviv, Istanbul. Das Modell meldet aktuell „Vorsprünge“
   von 27–49 Punkten gegen liquide Märkte – das ist kein Vorsprung, sondern ein Modellfehler
   (Gitterpunkt ≠ Messstation, Streuung zu eng). Es gibt **keine Plausibilitätsprüfung**, die das stoppt.
3. **Risikobremse flattert:** „−8 % heute“ wird am selben Tag mehrfach aus- und wieder eingeschaltet.
4. **Selbstkorrektur wirkt automatisch:** das Mischgewicht Bot/Markt würde ab 200 Märkten ohne
   Freigabe ins Handeln eingreifen.
5. Ressourcen (30 h): RAM Ø 222 MB, max. 279 MB; CPU Ø 0,4 %; Platte 0,26 GB. Reichlich Reserve.
6. Deploys sind nicht an die Tests gekoppelt (`checkSuites: false`).

## 7. Sicherheit
- Dashboard hinter Basic Auth (zeitkonstanter Vergleich). `/health` ist offen und gibt den letzten
  Fehlertext aus.
- **Geheimnisse können in Logs landen:** Fehlertexte von `requests` enthalten die volle URL –
  bei Telegram mit Token, bei The Odds API mit `apiKey`. Sie gehen ungefiltert ins Railway-Log und in
  die Status-Anzeige.
- `/api/scan?key=…`: Geheimnis in der URL (unbenutzt).
- `POST`-Endpunkte ohne Schutz gegen fremde Seiten (CSRF); kein Limit für Fehlanmeldungen.
- JavaScript/CSS inline, keine Sicherheits-Header.
- Keine Geheimnisse im Repository (geprüft: nur Umgebungsvariablen).

## 8. Was gut funktioniert und bleibt
Polymarket-Anbindung · Krypto-Modell mit Optionsdaten · Sport-Modell mit Quoten-Budget ·
Orderbuch-Simulation · Testkonten · Arbitrage-Suche · Telegram · Anmeldung · Railway-Deployment ·
Wachhund · alle 56 Tests.

## 9. Die größten Schwachstellen
1. Prognosen sind nicht nachvollziehbar gespeichert (kein Modell, keine Version, veränderbar).
2. Keine Qualitätsprüfung vor dem Handeln (Wetter-Verluste).
3. Kein Budget/Limit pro Datenquelle (Ausfall 13.11.).
4. Kein Backup, keine Migrationen.
5. Marktabdeckung nur Krypto/Sport/Wetter; kein Preisverlauf.
6. Lernen ist ungeprüft und automatisch statt getestet und freigegeben.
7. Dashboard zeigt Konten gut, aber nichts über Modelle, Daten und Systemzustand.
