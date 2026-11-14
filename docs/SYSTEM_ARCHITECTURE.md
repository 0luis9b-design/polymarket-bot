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
┌─────────────── Koordinator (ein Takt alle 10 min, Zeitbudget 240 s, Prioritäten) ─────────────┐
│ 1 Handels-Lauf (Engine, Agent „Paper Trading“)                                                 │
│      Scanner ─► Probability ─► Validation ─► Paper-Konten (Ausstieg, Einstieg, Arbitrage)      │
│ 2 Hintergrund-Agenten nach Fälligkeit und Priorität, solange das Zeitbudget reicht             │
│      Scanner (alle Kategorien, stündlich) · News (30 min) · Learning (stündlich) ·             │
│      Strategy (30 min) · Guardian (jeder Takt, läuft immer)                                    │
│      Wer dreimal hintereinander scheitert, wird zurückgestuft (5 → 60 min Pause)               │
│ 3 Mittwoch und Sonntag ab 17 Uhr: Claude-Review-Bericht                                        │
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
| Strategy Research | `agents/strategy.py` | baut den Datensatz entschiedener Märkte (Preis vor Schluss + Ergebnis), testet Strategien mit zeitlicher Trennung | alle 30 min eine Portion Datensatz, Tests wenn der Datensatz gewachsen ist |
| News Research | `agents/news.py` | RSS-Schlagzeilen sammeln, entdoppeln, Märkten zuordnen | 30 min |
| System Guardian | `agents/guardian.py` | Ressourcen, Quellen-Ausfälle, tägliches Backup, Integrität (Datenbank + Prüfsummen-Kette der Prognosen), Aufräumen. Der Neustart einer hängenden Schleife liegt im Wachhund (`app.py`) | jeder Lauf |
| Paper Trading | `engine.py` | virtuelle Wetten auf 11 Spielgeld-Konten, Risikobremse, Abrechnung | jeder Lauf |
| Berichte | `reports.py` | Claude-Review-Bericht Mi + So (13 Abschnitte, nur gemessene Werte) | Zeitplan |

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
| `resolved`, `backtests` | Datensatz entschiedener Märkte, gespeicherte Strategie-Tests | Tests sind reproduzierbar (Parameter + Datenstand) |
| `agent_runs`, `events`, `source_stats` | Agenten-Läufe, Systemereignisse, Abrufe/Fehler je Quelle und Tag | Aufbewahrung 14/90/90 Tage |
| `news`, `news_links` | Schlagzeilen mit Quelle und Zeit, Zuordnung zu Märkten | URL eindeutig |
| `reports`, `recommendations` | Claude-Berichte, Empfehlungen mit Status und Notiz | dauerhaft |

Kennzahlen (Brier, LogLoss, Kalibrierung, Tagesverlauf) werden nicht als eigene Tabelle geführt, sondern stündlich aus
`forecasts` + `outcomes` neu berechnet und als Zwischenstand in `kv` (`learn:summary`) abgelegt – so kann nichts auseinanderlaufen.

## Der Weg einer Prognose
1. Scanner liefert den Markt. 2. Probability rechnet alle Varianten (z. B. Wetter: Ensemble eng, Ensemble
breit, gelernt). 3. Validation prüft; gesperrte Prognosen werden trotzdem gespeichert (mit Grund), aber
nicht gehandelt. 4. Die Variante des aktiven Modells geht an die Paper-Konten. 5. Bei Entscheidung des
Marktes kommt das Ergebnis in `outcomes`. 6. Learning misst alle Varianten gegen den Marktpreis vom
Prognose-Zeitpunkt. 7. Ist ein Herausforderer an **späteren** Daten klar besser, entsteht ein Vorschlag.
8. Freigabe im Dashboard aktiviert ihn; der Vorgänger läuft als Herausforderer weiter (Rückfall-Erkennung).

## Kontrolliertes Lernen – Kriterien
- Trennung nach Zeit: erste 60 % der entschiedenen Prognosen = Vergleich, letzte 40 % = Out-of-Sample.
- Vorschlag nur, wenn Out-of-Sample: mindestens 100 Prognosen aus mindestens 30 unabhängigen Ereignissen,
  Brier ≥ 2 % besser, Unterschied statistisch klar (z ≥ 1,64, über Ereignis-Gruppen gerechnet) und im früheren
  Teil nicht schlechter. Pro Bereich wird nur der beste bestandene Herausforderer vorgeschlagen.
- Rückfall: aktives Modell wird laufend gegen seinen Vorgänger gemessen; ist es klar schlechter, entsteht
  ein Vorschlag „zurückrollen“.

## Kosten- und Ressourcen-Steuerung
- Jede Quelle hat Tagesbudget, Mindestabstand und Fehler-Pause (`sources.py`). Ist das Budget leer,
  wird nicht mehr abgerufen – der letzte Stand bleibt nutzbar.
- Jeder Agent hat ein Zeitbudget; der Koordinator stellt Agenten zurück, wenn der Takt zu lang wird,
  und zählt das mit.
- Priorität je Markt (0–100) = 40 % Handelsaktivität + 20 % Liquidität + 20 % Nähe zur Entscheidung + 10 % eigenes
  Modell vorhanden + 10 % aktuelle Schlagzeilen, abgewertet um 25 % je Datenmangel. Sie bestimmt die Reihenfolge im
  Dashboard und welche Märkte (die 400 wichtigsten) Schlagzeilen zugeordnet bekommen.
- Kostenpflichtige Dienste werden nie automatisch aktiviert; es gibt im Code keinen Schalter dafür.

## Sicherheit
Geheimnisse nur in Umgebungsvariablen; alle Fehlertexte werden vor dem Speichern geschwärzt. Anmeldung für
alles außer `/health`, Sperre nach 10 Fehlanmeldungen. POST nur von der eigenen Seite (eigener Kopf). Strenge
Content-Security-Policy, kein eingebettetes JavaScript. Eingaben der Einstellungen gegen feste Grenzen
geprüft. Externe Texte (Marktbeschreibungen, Schlagzeilen) werden nur gespeichert und maskiert angezeigt.

## Claude-Supervisor
Der Bot schreibt Mi und So um 17 Uhr einen Review-Bericht (13 Abschnitte, nur gemessene Werte), speichert
ihn, zeigt ihn im Dashboard und schreibt ihn zusätzlich ins Railway-Log. Eine geplante Claude-Sitzung
(Mi und So, 18:09) liest ihn dort, prüft die Logs und schreibt Luis eine Einschätzung mit Vorschlägen.
Claude ändert nichts ohne Freigabe.

Weil das Dashboard passwortgeschützt ist, schreibt der Koordinator zusätzlich nach jedem Takt eine Zeile ins Log
(`polybot.takt`: gelaufene und zurückgestellte Agenten, Agenten mit Fehler, pausierte Quellen, Ergebnis der Prüfung,
pausierte Konten, Bestand). Damit lässt sich der Zustand auch ohne Anmeldung nachvollziehen – Geheimnisse stehen dort nie.
