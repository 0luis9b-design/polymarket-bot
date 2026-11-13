# Risiko-Register

W = Wahrscheinlichkeit, A = Auswirkung (1 niedrig – 3 hoch).

| Nr | Risiko | W | A | Gegenmaßnahme | Status |
|---|---|---|---|---|---|
| R1 | Eine kostenlose Datenquelle sperrt oder drosselt den Bot | 3 | 3 | Budget pro Quelle, Cache, Fehler-Pause, Ausfall-Meldung, Ersatzquellen | in Arbeit |
| R2 | Ein Modell ist falsch kalibriert und „findet“ Scheingewinne | 3 | 3 | Validierung (Abweichungs-Deckel), Vergleich mit Markt-Baseline, Freigabe nur nach Out-of-Sample-Test | in Arbeit |
| R3 | Datenbank geht verloren oder wird beschädigt | 1 | 3 | tägliches Backup auf dem Volume (7 Stück), Integritätsprüfung. **Rest-Risiko:** Backup liegt auf demselben Volume | in Arbeit |
| R4 | Überanpassung: eine Strategie sieht im Rückblick gut aus und versagt danach | 3 | 2 | zeitliche Trennung Training/Test, Mindest-Stichprobe, Warnung im Strategie-Labor | in Arbeit |
| R5 | Blick in die Zukunft im Backtest (Look-ahead) | 2 | 3 | nur Preise **vor** dem Stichzeitpunkt, Ergebnis nur zur Auswertung, Tests dafür | in Arbeit |
| R6 | Polymarket ändert die API | 2 | 3 | defensive Auswertung, Quellen-Überwachung, Meldung bei 0 Märkten | teilweise |
| R7 | Geheimnis gelangt in Logs | 2 | 3 | Schwärzen aller Fehlertexte, kein Schlüssel in URLs | in Arbeit |
| R8 | Railway-Kosten steigen durch Speicher/CPU | 1 | 2 | Aufbewahrungsregeln, Ressourcen-Anzeige, Zeitbudget pro Agent | in Arbeit |
| R9 | Zu viele Telegram-Meldungen, wichtige gehen unter | 3 | 1 | Prioritäten, Tageslimit, Ruhezeit | in Arbeit |
| R10 | Paper-Ergebnisse wirken besser als echtes Handeln wäre | 3 | 2 | Orderbuch-Füllung, Gebühren, klare Kennzeichnung „Simulation“; Limit-Orders als optimistisch markiert | teilweise |
| R11 | Nachrichten/Marktbeschreibungen enthalten manipulative Texte | 2 | 2 | werden nur gespeichert und maskiert angezeigt, nie ausgeführt oder als Anweisung gelesen | in Arbeit |
| R12 | Das System verwechselt „viele Daten“ mit „gute Prognose“ | 2 | 2 | Kennzahlen immer gegen Marktpreis und einfache Baselines; „Keine Daten verfügbar“ statt Schätzwerte | in Arbeit |
| R13 | Rechtlich: Polymarket ist nicht überall zum Handeln zugelassen | – | – | System liest nur öffentliche Daten und handelt nicht. Echtgeld ist ausdrücklich nicht Teil des Projekts | akzeptiert |
