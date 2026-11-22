# Risiko-Register

W = Wahrscheinlichkeit, A = Auswirkung (1 niedrig – 3 hoch).

| Nr | Risiko | W | A | Gegenmaßnahme | Status |
|---|---|---|---|---|---|
| R1 | Eine kostenlose Datenquelle sperrt oder drosselt den Bot | 3 | 3 | Budget pro Quelle, Cache, Fehler-Pause, Ausfall-Meldung, Ersatzquellen | umgesetzt |
| R2 | Ein Modell ist falsch kalibriert und „findet“ Scheingewinne | 3 | 3 | Validierung (Abweichungs-Deckel), Vergleich mit Markt-Baseline, Freigabe nur nach Out-of-Sample-Test | umgesetzt |
| R3 | Datenbank geht verloren oder wird beschädigt | 1 | 3 | tägliches Backup auf dem Volume (7 Stück), Integritätsprüfung. **Rest-Risiko:** Backup liegt auf demselben Volume | umgesetzt |
| R4 | Überanpassung: eine Strategie sieht im Rückblick gut aus und versagt danach | 3 | 2 | zeitliche Trennung Training/Test, Mindest-Stichprobe, Warnung im Strategie-Labor | umgesetzt |
| R5 | Blick in die Zukunft im Backtest (Look-ahead) | 2 | 3 | nur Preise **vor** dem Stichzeitpunkt, Ergebnis nur zur Auswertung, Tests dafür | umgesetzt |
| R6 | Polymarket ändert die API | 2 | 3 | defensive Auswertung, Quellen-Überwachung mit Ausfall-Meldung, Agenten-Fehler im Dashboard | teilweise – keine aufgezeichneten Testdaten (T18) |
| R7 | Geheimnis gelangt in Logs | 2 | 3 | Schwärzen aller Fehlertexte, kein Schlüssel in URLs | umgesetzt |
| R8 | Railway-Kosten steigen durch Speicher/CPU | 1 | 2 | Aufbewahrungsregeln, Ressourcen-Anzeige, Zeitbudget pro Agent | umgesetzt |
| R9 | Zu viele Telegram-Meldungen, wichtige gehen unter | 3 | 1 | Prioritäten, Tageslimit, Ruhezeit | umgesetzt |
| R10 | Paper-Ergebnisse wirken besser als echtes Handeln wäre | 3 | 2 | Orderbuch-Füllung, Gebühren, klare Kennzeichnung „Simulation“; Limit-Orders als optimistisch markiert | teilweise |
| R11 | Nachrichten/Marktbeschreibungen enthalten manipulative Texte | 2 | 2 | werden nur gespeichert und maskiert angezeigt, nie ausgeführt oder als Anweisung gelesen | umgesetzt |
| R12 | Das System verwechselt „viele Daten“ mit „gute Prognose“ | 2 | 2 | Kennzahlen immer gegen Marktpreis und einfache Baselines; „Keine Daten verfügbar“ statt Schätzwerte | umgesetzt |
| R13 | Rechtlich: Polymarket ist nicht überall zum Handeln zugelassen | – | – | System liest nur öffentliche Daten und handelt nicht. Echtgeld ist ausdrücklich nicht Teil des Projekts | akzeptiert |
| R14 | Die Validierung sperrt echte Chancen (Modell hat recht, Markt irrt stark) | 2 | 1 | abgelehnte Prognosen werden gespeichert und im Lernzentrum gegen den Markt ausgewertet („War die Prüfung berechtigt?“); Grenze in den Einstellungen änderbar | umgesetzt |
| R15 | Ein Vorschlag wird freigegeben und verschlechtert die Prognosen | 2 | 2 | Vorgänger läuft als Herausforderer weiter, Rückfall-Erkennung schlägt das Zurückholen vor, Rückholung mit einem Klick | umgesetzt |
| R16 | Basisraten handeln auf dünner oder verzerrter Datengrundlage (Live 15./16.11.: E-Sport, Fußball-Ergebnisse) | 2 | 2 | nur Ja/Nein, 30 Vergleichsmärkte im eigenen Thema, Stabilität, Deckel 10 Pkt, z ≥ 1,64, eine Wette je Spiel; Wächter je Thema sperrt seit 18.11. schon bei „wahrscheinlich schlechter“ (z ≥ 1,0) | umgesetzt 17./18.11. |
| R17 | aviationweather.gov drosselt oder fällt aus | 2 | 1 | 30 min Zwischenspeicher, höchstens 5 Stationen je Abruf, Budget, 15 min Pause nach Fehler; ohne Messwerte gilt das alte Vorgehen | umgesetzt |
| R18 | Gute Durchschnitts-Prognose, aber Verluste: Der Bot wettet genau dort, wo er am stärksten vom Markt abweicht – dort kann er öfter danebenliegen als im Schnitt (Wetter 18.11.) | 2 | 2 | Bericht „Wetten im Rückblick“ vergleicht erwartete und tatsächliche Gewinnquote der gekauften Wetten; seit 2.8 sperrt der Rückblick Bereiche (Thema · Wettart · Seite · Abweichung), in denen Wetten gesichert verloren hätten – Bestätigt 22.11.: Wetter 19 % gewonnen bei 40 % Preis | umgesetzt 2.8 |
