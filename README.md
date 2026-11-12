# Polymarket-Bot (Spielgeld)

Ein Bot, der Polymarket-Wetten **nur mit Spielgeld** spielt. Er übernimmt nicht die Polymarket-Kurse,
sondern berechnet **eigene Wahrscheinlichkeiten** und wettet nur, wenn er deutlich anderer Meinung ist als der Markt.
Kein Konto, kein Wallet, kein echtes Geld – er liest nur öffentliche Daten.

## Wie der Bot seine Wahrscheinlichkeit berechnet

**Krypto-Märkte** (z. B. „Bitcoin über 104.000 $ am 14. November?“, „Bitcoin erreicht 120.000 $ im November?“)
- Holt die Stundenkurse der letzten 30 Tage (Binance → Coinbase → Kraken als Ersatz).
- Misst die Schwankung (Volatilität): halb die letzten Tage, halb der 30-Tage-Schnitt.
- Rechnet aus Kurs, Ziel-Preis, Restzeit und Schwankung die Wahrscheinlichkeit aus
  (Mischung aus ruhigen und wilden Phasen, damit große Sprünge nicht unterschätzt werden).
- Stichtag-Märkte: Binance-Schlusskurs um 12:00 Uhr New Yorker Zeit. Berühren-Märkte: Ziel irgendwann vor Ablauf erreicht.
- Unterstützt: Bitcoin, Ethereum, Solana, XRP, Dogecoin. „Up or Down“-Minutenmärkte werden bewusst ignoriert.

**Sport-Märkte** – nur „Wer gewinnt“ in: Bundesliga, 2. Bundesliga, Premier League, La Liga, Serie A, Ligue 1, Champions League, Europa League, NBA, NHL, NFL, UFC (änderbar über `SPORTS_LEAGUES`)
- Holt die Quoten vieler Buchmacher (The Odds API), rechnet deren Marge heraus und bildet den Mittelwert
  (Pinnacle – der genaueste Buchmacher – zählt doppelt).
- Nur Spiele, die in den nächsten 24 Stunden beginnen, nur vor Anpfiff.

## Wann und wie viel gewettet wird
- Vorsprung = eigene Wahrscheinlichkeit − Kaufkurs − Polymarket-Gebühr. Gekauft wird erst ab 6 Punkten (Krypto) bzw. 3 Punkten (Sport).
- Einsatz 2–6 % des Kontos je nach **Sicherheit**: 60 % zählt der Vorsprung (Schwelle = 2 %, 10 Punkte darüber = 6 %),
  40 % die Trefferchance (35 % → wenig, 85 % → viel). Höchstens 10 % pro Ereignis, 60 % insgesamt im Markt.
- Kauf zum echten Briefkurs (Ask) inkl. Gebühr – so realistisch wie möglich. Gehalten bis zur Entscheidung.
- Startkapital: 1.000 $ Spielgeld.

## Neu (12.11.2026)
- **Krypto mit Optionsdaten:** Für BTC, ETH, SOL und XRP nimmt der Bot die erwartete Schwankung aus dem Deribit-Optionsmarkt
  (inkl. Smile, also höhere Wahrscheinlichkeit für große Sprünge). DOGE nutzt die Kurshistorie mit Aufschlag.
- **Arbitrage:** Findet Widersprüche (Summe aller Antworten ≠ 100 %, oder „über 72k“ teurer als „über 70k“) und kauft das Paket.
- **Orderbuch:** Käufe/Verkäufe laufen über das echte Orderbuch (Durchschnittskurs), zu dünne Bücher werden ausgelassen.
- **Früher Ausstieg:** Verkauft, sobald der Verkaufserlös die eigene Wahrscheinlichkeit erreicht (frühestens nach 30 min).
- **Testkonten:** Hauptkonto, Schwelle 4/2, Schwelle 8/4, Fest 10 $, Ohne Ausstieg, Viertel-Kelly, Nur Arbitrage – je 1.000 $.
- **Schlusskurs-Vergleich (Sport):** Buchmacher-Quote kurz vor Anpfiff vs. Kaufkurs.
- **Gruppen-Grenze:** max. 10 % für alles am selben Coin+Stichtag bzw. Spiel.
- **Telegram:** `TELEGRAM_BOT_TOKEN` und `TELEGRAM_CHAT_ID` setzen (Chat-ID zeigt das Dashboard unter Status an).

## Ist der Bot besser als der Markt?
Reiter **Treffsicherheit**: Für jeden berechneten Markt wird beim ersten Mal die Bot-Prognose und der Marktkurs gespeichert.
Nach der Entscheidung wird verglichen (Brier-Wert, kleiner = besser). Erst nach einigen hundert Märkten ist das aussagekräftig.

## Railway einrichten
1. Repo auf GitHub, in Railway „Deploy from GitHub repo“ – Start-Befehl steht in `railway.json`.
2. **Volume** anlegen und auf `/data` mounten, Variable `DATA_DIR=/data` setzen (sonst ist nach jedem Deploy alles weg).
3. Für Sport: kostenlosen Schlüssel auf https://the-odds-api.com holen und als `ODDS_API_KEY` eintragen
   (500 Abrufe/Monat gratis; der Bot verteilt das Restguthaben automatisch auf die Tage bis Monatsende, ~15 pro Tag, und holt bald beginnende Spiele zuerst). Ohne Schlüssel läuft nur Krypto.
4. Domain erzeugen → Dashboard.
5. **Anmeldung** (wie beim XAUUSD-Bot): `DASHBOARD_USER` und `DASHBOARD_PASSWORD` setzen. Dann fragt der Browser
   nach Benutzername und Passwort. Fehlt eine der beiden, ist das Dashboard offen (Warnung im Log). `/health` bleibt frei.

Alle Zahlen (Mindest-Vorsprung, Kelly-Anteil, Intervall …) lassen sich per Variable ändern, siehe `polybot/config.py`.

## Lokal (Windows)
```
pip install -r requirements-dev.txt
python -m pytest -q
python app.py      # dann http://localhost:5000
```
