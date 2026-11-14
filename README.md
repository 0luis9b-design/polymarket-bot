# Polymarket-Bot – Forschung und Paper-Trading (Version 2)

Ein System, das Polymarket-Märkte beobachtet, **eigene Wahrscheinlichkeiten** berechnet, sie gegen den Marktpreis und das
tatsächliche Ergebnis misst und daraus lernt. Gehandelt wird **nur mit Spielgeld** auf 11 Testkonten.
Kein Konto, kein Wallet, keine Signatur, kein echtes Geld – es liest ausschließlich öffentliche Daten.

Ausführlich: [`docs/SYSTEM_ARCHITECTURE.md`](docs/SYSTEM_ARCHITECTURE.md) · [`docs/DEVELOPMENT_ROADMAP.md`](docs/DEVELOPMENT_ROADMAP.md) ·
[`docs/PROJECT_AUDIT.md`](docs/PROJECT_AUDIT.md) · [`docs/TECHNICAL_DEBT.md`](docs/TECHNICAL_DEBT.md) · [`docs/RISK_REGISTER.md`](docs/RISK_REGISTER.md)

## Was das System tut
| Baustein | Aufgabe |
|---|---|
| **Market Scanner** | Katalog der aktivsten Märkte aller Kategorien, stündlicher Preisverlauf, neue Märkte, auffällige Bewegungen, Datenqualität |
| **Probability** | eigene Prognosen für Krypto, Sport und Wetter; mehrere Modell-Varianten laufen parallel; jede Prognose wird unveränderlich gespeichert |
| **Validation** | sperrt Prognosen fürs Handeln, wenn Daten veraltet sind oder die Abweichung vom liquiden Markt unplausibel groß ist |
| **Learning** | Brier, LogLoss, Kalibrierung, Verzerrung je Modell/Bereich/Zeithorizont gegen den Marktpreis; testet Herausforderer an späteren Daten; **schlägt vor, aktiviert nie selbst** |
| **Strategy Research** | Datensatz entschiedener Märkte aus der Polymarket-Historie, Backtests mit Gebühren und zeitlicher Trennung, Warnung bei Überanpassung |
| **News** | BBC-Schlagzeilen, per Stichwort Märkten zugeordnet (mit Quelle und Zeit, ohne Bewertung) |
| **Guardian** | Ressourcen, Quellen-Ausfälle, tägliches Backup, Integrität, Aufräumen |
| **Koordinator** | Takt alle 10 min, Zeitbudget, stuft fehlerhafte Agenten zurück, schreibt Mi + So den Claude-Review-Bericht |
| **Paper Trading** | virtuelle Wetten nach den geprüften Prognosen, Risikobremse je Konto |

Die „Agenten“ sind Programm-Module, keine Sprachmodelle – das Budget für externe Dienste ist 0 €.

## Dashboard
Übersicht · Märkte (Suche, Filter, Detail mit Preisverlauf) · Prognosen · KI-Zentrale · Lernen (Vorschläge freigeben/ablehnen,
Modell zurückholen) · Strategie-Labor · Performance · Daten · System · Claude-Berichte · Einstellungen · Paper-Trading.
Angezeigt werden nur gemessene Werte; fehlt etwas, steht dort „Keine Daten verfügbar“.

## Regeln, die im Code fest verankert sind
- **Nichts lernt unkontrolliert.** Ein neues Modell oder eine Mischung mit dem Marktpreis wird erst aktiv, wenn es an
  späteren Daten klar besser war (≥ 100 Prognosen aus ≥ 30 Ereignissen, Brier ≥ 2 % besser, z ≥ 1,64) **und** im Dashboard
  freigegeben wurde. Jede Aktivierung ist rückholbar.
- **Prognose-Historie ist unveränderlich.** Die Datenbank selbst lehnt Ändern und Löschen ab; eine Prüfsummen-Kette macht Eingriffe sichtbar.
- **Jede Datenquelle hat ein Budget.** Ist es leer oder antwortet die Quelle mit „zu viele Abrufe“, wird pausiert statt weiter angefragt.
- **Geheimnisse** stehen nur in Umgebungsvariablen und werden aus allen Fehlertexten entfernt.
- **Externe Texte** (Marktfragen, Schlagzeilen) werden nur gespeichert und als Text angezeigt, nie ausgeführt.

## Wie der Bot seine Wahrscheinlichkeit berechnet

**Krypto-Märkte** (z. B. „Bitcoin über 104.000 $ am 14. November?“, „Bitcoin erreicht 120.000 $ im November?“)
- Erwartete Schwankung aus dem Deribit-Optionsmarkt (BTC, ETH, SOL, XRP; inkl. Smile, also höhere Wahrscheinlichkeit für große Sprünge).
  Ohne Optionsdaten (z. B. DOGE): Stundenkurse der letzten 30 Tage (Binance → Coinbase → Kraken) mit Aufschlag.
- Aus Kurs, Ziel-Preis, Restzeit und Schwankung ergibt sich die Wahrscheinlichkeit. Die Variante „nur Kurshistorie“ läuft als Herausforderer mit.
- Stichtag-Märkte: Binance-Schlusskurs um 12:00 Uhr New Yorker Zeit. Berühren-Märkte: Ziel irgendwann vor Ablauf erreicht.
- Unterstützt: Bitcoin, Ethereum, Solana, XRP, Dogecoin. „Up or Down“-Minutenmärkte werden bewusst ignoriert.

**Sport-Märkte** – nur „Wer gewinnt“ in: Bundesliga, 2. Bundesliga, Premier League, La Liga, Serie A, Ligue 1, Champions League, Europa League, Championship, La Liga 2, Ligue 2, Serie B, Schottland, League One, NFL, UFC (änderbar über `SPORTS_LEAGUES`)
- Holt die Quoten vieler Buchmacher (The Odds API), rechnet deren Marge heraus und bildet den Mittelwert
  (Pinnacle – der genaueste Buchmacher – zählt doppelt).
- Nur Spiele, die in den nächsten 24 Stunden beginnen, nur vor Anpfiff.

## Wann und wie viel (virtuell) gewettet wird
- Vorsprung = eigene Wahrscheinlichkeit − Kaufkurs − Gebühr. Gekauft wird ab 6 Punkten (Krypto), 3 (Sport), 10 (Wetter) –
  und nur, wenn die Prognose die Prüfung bestanden hat.
- Einsatz 2–6 % des Kontos je nach Sicherheit, höchstens 10 % pro Ereignis/Gruppe, 60 % insgesamt.
- Kauf und Verkauf über das echte Orderbuch inkl. Gebühr. Früher Ausstieg, wenn der Verkaufserlös die eigene Wahrscheinlichkeit erreicht.
- Risikobremse: −8 % an einem Tag → Pause bis zum nächsten Tag; −25 % gesamt → Pause bis „Fortsetzen“ im Dashboard.
- Konten (je 1.000 $ Spielgeld): Haupt·Krypto, Haupt·Sport, Haupt·Wetter, Schwelle 4/2/7, Schwelle 8/4/14, Fest 10 $,
  Ohne Ausstieg, Viertel-Kelly, Limit-Orders, Außenseiter verkaufen, Nur Arbitrage.

**Wetter-Märkte:** Höchst-/Tiefsttemperatur an ~60 Flughäfen mit dem Open-Meteo-Ensemble (ECMWF, GFS, ICON, ~120 Szenarien).
Ein gelerntes Modell (Stations-Korrektur + gemessene Streuung) läuft als Herausforderer mit.

## Claude als Supervisor
Mittwoch und Sonntag um 17 Uhr schreibt der Bot einen Review-Bericht (13 Abschnitte, nur gemessene Werte), speichert ihn,
zeigt ihn im Dashboard und schreibt ihn ins Railway-Log. Eine geplante Claude-Sitzung liest ihn dort und bespricht
Verbesserungen – ohne kostenpflichtige API, und ohne dass Claude selbst etwas ändert.

## Railway einrichten
1. Repo auf GitHub, in Railway „Deploy from GitHub repo“ – Start-Befehl steht in `railway.json`.
2. **Volume** anlegen und auf `/data` mounten, Variable `DATA_DIR=/data` setzen (sonst ist nach jedem Deploy alles weg).
3. Für Sport: kostenlosen Schlüssel auf https://the-odds-api.com holen und als `ODDS_API_KEY` eintragen
   (500 Abrufe/Monat gratis; der Bot verteilt das Restguthaben automatisch auf die Tage bis Monatsende, ~15 pro Tag, und holt bald beginnende Spiele zuerst). Ohne Schlüssel läuft nur Krypto.
4. Domain erzeugen → Dashboard.
5. **Anmeldung** (wie beim XAUUSD-Bot): `DASHBOARD_USER` und `DASHBOARD_PASSWORD` setzen. Dann fragt der Browser
   nach Benutzername und Passwort. Fehlt eine der beiden, ist das Dashboard offen (Warnung im Log). `/health` bleibt frei.

6. **Telegram** (optional): `TELEGRAM_BOT_TOKEN` und `TELEGRAM_CHAT_ID`. Meldungen haben Prioritäten, ein Tageslimit und eine Ruhezeit (23–7 Uhr; Kritisches kommt immer durch).

Häufige Einstellungen (Takt, Abweichungs-Grenze, Bereiche an/aus, Benachrichtigungen) lassen sich im Dashboard unter
„Einstellungen“ ändern und bleiben gespeichert. Alles Weitere per Variable, siehe `polybot/config.py`.
Die Datenbank liegt in `DATA_DIR`; Sicherungen täglich in `DATA_DIR/backups` (die letzten 7) und vor jeder Schema-Änderung.

## Lokal (Windows)
```
pip install -r requirements-dev.txt
python -m pytest -q   # 121 Tests
python app.py      # dann http://localhost:5000
```
