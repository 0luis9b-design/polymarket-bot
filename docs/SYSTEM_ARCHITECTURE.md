# System-Architektur

## Grundsatz-Entscheidungen (mit Begründung)
1. **Ein Prozess bleibt.** Gemessen: 222 MB RAM, 0,4 % CPU. Mehrere Dienste oder eine Warteschlange
   würden Railway-Kosten und Fehlerquellen erhöhen, ohne ein Problem zu lösen. Die Agenten laufen
   nacheinander in einem Takt, jeder mit eigenem Zeitbudget.
2. **SQLite bleibt.** Eine Datei, ein Schreiber, wenige MB pro Tag. Migrationen und Backup kommen dazu.
   Wechsel auf Postgres erst, wenn zwei Dienste gleichzeitig schreiben müssen.
3. **„Agenten“ sind Programm-Module, keine Sprachmodelle.** Budget für externe Dienste ist 0 €, ein
   lokales Modell passt nicht sinnvoll auf den Server. Jeder Agent hat eine klare Aufgabe, eigene
   Statuszeile, eigenes Fehlerprotokoll. Das Urteilsvermögen eines Sprachmodells kommt über den
   **Claude-Supervisor** (2× pro Woche, im vorhandenen Claude-Abo, keine API-Kosten).
4. **Nichts lernt unkontrolliert.** Neue Modellvarianten laufen als „Herausforderer“ nur mit, werden an
   späteren Daten (Out-of-Sample) gemessen, und erst eine Freigabe im Dashboard macht sie aktiv.
   Jede Aktivierung ist versioniert und rückholbar.
5. **Nachrichten über RSS statt Such-API.** GDELT hat bei der Prüfung jeden Abruf abgelehnt (429).
   BBC-Feeds sind als Standard-RSS geprüft. Der Bot versteht Texte nicht – er ordnet Schlagzeilen
   Märkten über Stichwörter zu und zeigt sie mit Quelle und Zeit. Bewertet werden sie vom Supervisor.
6. **Nur Analyse und Spielgeld.** Kein Wallet, keine Signatur, kein schreibender Polymarket-Aufruf.

## Aufbau
```
                         ┌────────────────────── Dashboard (Flask, /api/*) ─────────────────────┐
                         │ Übersicht · Märkte · Prognosen · KI-Zentrale · Lernen · Strategie ·  │
                         │ Performance · Daten · System · Claude-Berichte · Einstellungen ·     │
                         │ Paper-Trading                                                        │
                         └───────────────────────────────▲──────────────────────────────────────┘
                                                         │ liest
┌─────────────── Koordinator (ein Takt alle 10 min, Zeitbudget, Prioritäten) ───────────────────┐
│ 1 Guardian (vorher)   Ressourcen prüfen, bei Engpass schwere Agenten aussetzen                 │
│ 2 Handels-Lauf (Engine)                                                                        │
│      Scanner ─► Probability ─► Validation ─► Paper-Konten (Ausstieg, Einstieg, Arbitrage)      │
│ 3 Langsame Agenten nach Fälligkeit und Priorität                                               │
│      Scanner (alle Kategorien, stündlich) · Learning · Strategy · News · Berichte              │
│ 4 Guardian (nachher)  Quellen-Statistik, Backup, Aufräumen, Ausfall-Meldungen                  │
└───────────────────────────────────────────────┬────────────────────────────────────────────────┘
                                                │ schreibt
                                   ┌────────────▼────────────┐         ┌───────────────────────┐
                                   │ SQLite auf /data        │◄────────│ Quellen-Verwaltung    │
                                   │ + tägliches Backup      │         │ Budget, Limit, Status │
                                   └─────────────────────────┘         └───────────▲───────────┘
                                                                                   │ alle HTTP-Abrufe
        Polymarket · Deribit · Binance/Coinbase/Kraken · The Odds API · Open-Meteo · BBC RSS · Telegram
```

## Agenten
| Agent | Modul | Aufgabe | Takt |
|---|---|---|---|
| Koordinator | `agents/coordinator.py` | Reihenfolge, Zeitbudget, Zurückstellen bei Engpass, Status aller Agenten | jeder Lauf |
| Market Scanner | `agents/scanner.py` | Marktkatalog aller Kategorien, stündlicher Preisverlauf, neue Märkte, auffällige Bewegungen, Datenqualität, Priorität je Markt | Modell-Märkte jeder Lauf, alle Kategorien stündlich |
| Probability | `agents/probability.py` | Modell-Register mit Versionen, Varianten je Markt, Unsicherheit, unveränderliche Prognose-Historie | jeder Lauf |
| Validation | `agents/validation.py` | prüft jede Prognose (Daten frisch? Markt handelbar? Abweichung plausibel? Summen stimmig?) und darf sie fürs Handeln sperren | jeder Lauf |
| Learning | `agents/learning.py` | Kennzahlen je Modell/Kategorie/Zeithorizont gegen Markt und Baselines, Fehlerarten, Experimente, Freigabe-Vorschläge, Rückfall-Erkennung | stündlich |
| Strategy Research | `agents/strategy.py` | baut den Datensatz entschiedener Märkte (Preis vor Schluss + Ergebnis), testet Strategien mit zeitlicher Trennung | Datensatz jeder Lauf (kleine Portion), Tests täglich |
| News Research | `agents/news.py` | RSS-Schlagzeilen sammeln, entdoppeln, Märkten zuordnen | 30 min |
| System Guardian | `agents/guardian.py` | Ressourcen, Quellen-Ausfälle, Backup, Integrität, Aufräumen, sicherer Neustart | jeder Lauf |
| Berichte | `reports.py` | Claude-Review-Bericht Mi + So, Tages-/Wochenbilanz | Zeitplan |

## Datenmodell
Bestehend (bleibt): `kv`, `positions`, `orders`, `predictions`, `log`, `equity_acc`.
Neu, über nummerierte Migrationen (`schema_version`):

| Tabelle | Inhalt | Regeln |
|---|---|---|
| `markets` | Katalog: Frage, Kategorie, Ende, Preis, Liquidität, Bewegung, Datenqualität, Priorität | ein Eintrag pro Markt |
| `snapshots` | stündlicher Preis je Markt | Schlüssel (Markt, Stunde); 14 Tage stündlich, danach täglich |
| `forecasts` | **Prognose-Historie**: Markt, Modell, Version, Zeithorizont, Wahrscheinlichkeit, Marktpreis, Unsicherheit, Quellen, Merkmale, Prüfergebnis | nur Anfügen; Ändern/Löschen sperrt die Datenbank selbst (Trigger); Prüfsummen-Kette |
| `outcomes` | tatsächliches Ergebnis je Markt | getrennt von der Prognose |
| `model_registry`, `model_versions` | aktives Modell je Kategorie, alle Parameter-Stände | Rückkehr zu jeder früheren Version möglich |
| `experiments`, `proposals` | Tests Herausforderer gegen aktives Modell, Freigabe-Vorschläge mit Status | |
| `metrics_daily` | Tageswerte Brier/LogLoss je Modell | für Verlaufs-Diagramme |
| `resolved`, `backtests` | Datensatz entschiedener Märkte, gespeicherte Strategie-Tests | Tests sind reproduzierbar (Parameter + Datenstand) |
| `agent_runs`, `events`, `source_stats` | Agenten-Läufe, Systemereignisse, Abrufe/Fehler je Quelle und Tag | Aufbewahrung 14/90/90 Tage |
| `news`, `news_links` | Schlagzeilen mit Quelle und Zeit, Zuordnung zu Märkten | URL eindeutig |
| `reports`, `recommendations` | Claude-Berichte, Empfehlungen mit Status und Notiz | dauerhaft |

## Der Weg einer Prognose
1. Scanner liefert den Markt. 2. Probability rechnet alle Varianten (z. B. Wetter: Ensemble eng, Ensemble
breit, gelernt). 3. Validation prüft; gesperrte Prognosen werden trotzdem gespeichert (mit Grund), aber
nicht gehandelt. 4. Die Variante des aktiven Modells geht an die Paper-Konten. 5. Bei Entscheidung des
Marktes kommt das Ergebnis in `outcomes`. 6. Learning misst alle Varianten gegen den Marktpreis vom
Prognose-Zeitpunkt. 7. Ist ein Herausforderer an **späteren** Daten klar besser, entsteht ein Vorschlag.
8. Freigabe im Dashboard aktiviert ihn; der Vorgänger läuft als Herausforderer weiter (Rückfall-Erkennung).

## Kontrolliertes Lernen – Kriterien
- Trennung nach Zeit: erste 60 % der entschiedenen Prognosen = Vergleich, letzte 40 % = Out-of-Sample.
- Vorschlag nur, wenn Out-of-Sample: mindestens 100 Prognosen, Brier ≥ 2 % besser, Unterschied statistisch
  klar (z ≥ 1,64), keine Verschlechterung der Kalibrierung.
- Rückfall: aktives Modell wird laufend gegen seinen Vorgänger gemessen; ist es klar schlechter, entsteht
  ein Vorschlag „zurückrollen“.

## Kosten- und Ressourcen-Steuerung
- Jede Quelle hat Tagesbudget, Mindestabstand und Fehler-Pause (`sources.py`). Ist das Budget leer,
  wird nicht mehr abgerufen – der letzte Stand bleibt nutzbar.
- Jeder Agent hat ein Zeitbudget; der Koordinator stellt Agenten zurück, wenn der Takt zu lang wird,
  und zählt das mit.
- Priorität je Markt = Handelsaktivität × Nähe zur Entscheidung × Datenqualität × Abweichung Modell/Markt.
  Sie bestimmt, welche Märkte Preisverlauf, Nachrichten-Zuordnung und einen Platz oben im Dashboard bekommen.
- Kostenpflichtige Dienste werden nie automatisch aktiviert; es gibt im Code keinen Schalter dafür.

## Sicherheit
Geheimnisse nur in Umgebungsvariablen; alle Fehlertexte werden vor dem Speichern geschwärzt. Anmeldung für
alles außer `/health`. POST nur von der eigenen Seite. Eingaben der Einstellungen gegen feste Grenzen
geprüft. Externe Texte (Marktbeschreibungen, Schlagzeilen) werden nur gespeichert und maskiert angezeigt.

## Claude-Supervisor
Der Bot schreibt Mi und So um 17 Uhr einen Review-Bericht (13 Abschnitte, nur gemessene Werte), speichert
ihn, zeigt ihn im Dashboard und schreibt ihn zusätzlich ins Railway-Log. Eine geplante Claude-Sitzung
(Mi und So, 18:09) liest ihn dort, prüft die Logs und schreibt Luis eine Einschätzung mit Vorschlägen.
Claude ändert nichts ohne Freigabe.
