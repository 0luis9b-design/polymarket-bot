# Entwicklungs-Roadmap

Priorität: **1** = verhindert Schaden oder ist Voraussetzung · **2** = Kern des Zielsystems · **3** = Ausbau.
Status: ✅ erledigt (mit Tests belegt) · 🔧 in Arbeit · ⏳ geplant. Stand: 18.11.2026 (abends), Version 2.5.0.

## Phase 1–2 · Audit und Architektur — ✅
Dokumente: `PROJECT_AUDIT.md`, `SYSTEM_ARCHITECTURE.md`, `TECHNICAL_DEBT.md`, `RISK_REGISTER.md`, diese Datei.

## Phase 3 · Fundament (Priorität 1) — ✅
| Schritt | Löst | Status |
|---|---|---|
| Nummerierte Datenbank-Migrationen | T6 | ✅ |
| Tägliches Backup + Integritätsprüfung | T3, R3 | ✅ |
| Quellen-Verwaltung: Budget, Mindestabstand, Fehler-Pause, Statistik | T1, T14, R1 | ✅ |
| Fehlertexte schwärzen, `/health` entschärfen, `/api/scan` entfernen | T4, T13, R7 | ✅ |
| Agenten-Gerüst + Koordinator mit Zeitbudget | T9 | ✅ |
| Schleife startet nach Absturz neu, Notfall-Neustart bei Hänger | T11 | ✅ |
| Risikobremse rastet für den Tag ein | T8 | ✅ |

## Phase 4 · Market Intelligence (Priorität 2) — ✅
Marktkatalog aller Kategorien (je Kategorie die aktivsten Märkte), stündlicher Preisverlauf, neue Märkte,
auffällige Bewegungen, Datenqualität, Priorität je Markt.

## Phase 5 · Agenten (Priorität 1–2) — ✅
| Schritt | Löst | Status |
|---|---|---|
| Validation: Abweichungs-Deckel, Frische, Stimmigkeit – sperrt fürs Handeln | T2, R2 | ✅ |
| Probability: Modell-Register, Varianten, Unsicherheit, Prognose-Historie (unveränderlich) | T5 | ✅ |
| News: BBC-RSS, Zuordnung zu Märkten | – | ✅ |
| Guardian: Ressourcen, Quellen-Ausfälle, Aufräumen | R1, R8 | ✅ |

## Phase 6 · Lernsystem (Priorität 2) — ✅
Kennzahlen (Brier, LogLoss, Kalibrierung, Treffer, Verzerrung) je Modell, Kategorie, Zeithorizont gegen
Marktpreis und 50-%-Baseline · Fehlerarten · Experimente mit zeitlicher Trennung · Vorschläge mit Freigabe
und Rückholung · Wetter: Stations-Korrektur und gemessene Streuung als lernendes Modell (T15) ·
Selbstkorrektur nur noch nach Freigabe (T7).

## Phase 7 · Backtesting (Priorität 2) — ✅
Datensatz entschiedener Märkte (Preis 1 h/6 h/24 h/72 h/7 T vor Schluss + Ergebnis) aus Polymarket-Historie ·
Strategie-Tests mit Gebühren, In-Sample/Out-of-Sample, Warnung bei Überanpassung · gespeichert und wiederholbar.

## Phase 8 · Dashboard (Priorität 1) — ✅
Neues Gerüst mit Seitenleiste, 12 Bereiche, je Bereich eigener Daten-Abruf (T10), JS/CSS als Dateien (T16).

## Phase 9 · Telegram und Supervisor (Priorität 2) — ✅
Prioritäten, Tageslimit, Ruhezeit · Claude-Review-Bericht Mi + So · geplante Claude-Sitzung.

## Phase 10–11 · Qualität und Betrieb (Priorität 1) — ✅
| Schritt | Status |
|---|---|
| Tests für jedes neue Modul (Stand 2.2: 150 Tests + Browser-Tests; Quellen, Migrationen, Prognose-Historie, Prüfung, Lernen, Backtests ohne Blick in die Zukunft, Nachrichten, Guardian, Berichte, alle API-Endpunkte) | ✅ |
| CSRF-Schutz, Anmelde-Sperre, Sicherheits-Header (T12) | ✅ |
| Abhängigkeits-Prüfung in der CI (T17) | ✅ |
| Upgrade einer v1.3-Datenbank geprüft (Backup vor Migration, Wetten und Guthaben bleiben) | ✅ |
| Dashboard in echtem Browser geprüft (alle Seiten, Handy + Desktop, keine Skriptfehler) – von Hand, nicht in der CI | ✅ |
| Kontrolle im Live-Betrieb nach dem Deploy (14.11., über die Railway-Logs) | ✅ Migration mit Backup, Läufe ohne Fehler, alle Agenten ohne Fehler |
| Im Live-Betrieb gefunden und behoben: Historie blieb bei Krypto an den 5-Minuten-Märkten hängen · Summen-Prüfung maß nach einer Mischung die Marktkurse statt das Modell · alte Risikobremsen wurden nach jedem Neustart erneut gemeldet · Wetter-Zwischenspeicher ging bei jedem Neustart verloren · Telegram ohne Wiederholung bei Verbindungsfehler | ✅ je mit Test |

## Version 2.1 · Alle Märkte im Paper-Trading (Wunsch vom 14.11.) — ✅
| Schritt | Status |
|---|---|
| Einordnung jedes Marktes in Bereich › Unterbereich › Detail aus den Polymarket-Schlagwörtern (`taxonomy.py`), für Katalog, Datensatz und alle Wetten; ältere Wetten nachträglich eingeordnet | ✅ |
| Basisraten-Modell (`base_model.py`) für alle Märkte ohne eigenes Datenmodell: Marktpreis + gemessene Abweichung gleichartiger entschiedener Märkte, zur Null gestaucht bei wenig Daten | ✅ |
| Neues Spielgeld-Konto „Haupt · Weitere“ (1.000 $): handelt nur Basisraten, ab 3 Pkt Vorsprung und nur bei statistisch gesicherter Abweichung (z ≥ 1,64); die anderen Konten bleiben bei ihren Modellen | ✅ |
| Je Wette gespeichert (Migration v9): Wahrscheinlichkeit für den eigenen Tipp, Markt-Wahrscheinlichkeit, Modell, Unsicherheit, Restlaufzeit, Spanne, Liquidität, Prüfung, Quellen, Begründung, Ausstiegsgrund | ✅ |
| Dashboard: „Konten und Lage“, „Wetten“ (Historie mit Filtern, Sortierung, Suche, Zeitraum), Detailseite je Wette, „Kalender“ (G/V je Tag, Tag antippen) – überall derselbe Stufen-Filter; auf dem Handy Karten statt Tabellen | ✅ |
| Datensatz entschiedener Märkte wächst schneller (60 statt 25 je Lauf) – er ist die Grundlage der Basisraten | ✅ |

Wichtig zum Verständnis: Das Konto „Haupt · Weitere“ wettet anfangs wenig oder gar nicht. Solange der Datensatz klein ist,
liegen die Basisraten fast genau auf dem Marktpreis – dann gibt es keinen Vorsprung. Das ist gewollt: Ohne Belege kein Einsatz.

## Version 2.2 · Jede Strategie je Thema ein Konto (Wunsch vom 14.11.) — ✅
| Schritt | Status |
|---|---|
| 9 Strategien × 11 Themen als eigene Spielgeld-Konten (Arbitrage nur Krypto/Sport/Wetter) = 91 Konten; Hauptkonten behalten ihre Kennung | ✅ |
| Einmaliger Umzug: Wetten und Gebote der bisherigen Testkonten und von „Haupt · Weitere“ wandern in das Konto ihrer Strategie und ihres Themas, Guthaben entsprechend; Sicherung vorher; Verlustgrenze neuer Konten ab dem übernommenen Wert | ✅ mit Test |
| Jedes Konto handelt nur sein Thema – mit eigenem Modell (Krypto/Sport/Wetter) und Basisraten; Limit-Orders und Außenseiter-Regel je Thema | ✅ mit Test |
| Dashboard: Tabelle Strategie × Thema (G/V je Konto, antippen öffnet das Konto), Strategie-Übersicht, pausierte Konten mit „Fortsetzen“, Filter nach Strategie auf allen Paper-Seiten | ✅ Browser-Test |
| Telegram-Tages-/Wochenbilanz und Claude-Bericht je Strategie zusammengefasst statt 91 Zeilen | ✅ mit Test |

Hinweis: Mit 91 Konten gibt es je Konto weniger Wetten – belastbare Vergleiche einzelner Felder brauchen Wochen. Für den Anfang
sind die Zeilen (Strategie über alle Themen) und Spalten (Thema über alle Strategien) aussagekräftiger als einzelne Felder.

## Version 2.3 · Nur mit gutem Grund wetten (Wunsch vom 17.11.) — ✅
Anlass: Am 15./16.11. wetteten die Basisraten auf E-Sport-Karten und genaue Fußball-Ergebnisse (Färöer – Moldau: „1:1“ zu
33 % geschätzt bei 14 % Marktpreis, dazu „Unentschieden“ und „2:2“ im selben Konto – alles verloren).

| Schritt | Status |
|---|---|
| Basisraten nie für Spiele (Sport, E-Sport) – dort nur Buchmacher-Quoten als Grund | ✅ mit Test |
| Basisraten nur für echte Ja/Nein-Fragen, mit mind. 50 Vergleichsmärkten aus demselben Thema (nicht geliehen) | ✅ mit Test |
| Muster muss in der älteren und der neueren Hälfte der Vergleichsmärkte gelten und darf allen Themen zusammen nicht widersprechen | ✅ mit Test |
| Rohabweichung über 10 Punkte = verzerrte Vergleichsdaten → kein Handel | ✅ mit Test |
| Wächter: Thema gesperrt, wenn die Basisraten dort gesichert schlechter als der Markt waren (aus der Prognose-Historie, hebt sich selbst wieder auf) | ✅ mit Test |
| Höchstens eine Wette je Spiel und Konto (alle Märkte einer Partie zusammen, auch Gebote und Außenseiter-Regel) | ✅ mit Test |
| Keine Pausen mehr: große Verluste nur gemeldet; alle bisherigen Pausen aufgehoben; Pause unter Einstellungen wieder einschaltbar | ✅ mit Test |
| Claude-Bericht: Wächter-Status und auffälligste Vergleichszellen der Basisraten | ✅ |
| Datensatz ohne Auswahl nach Endvolumen aufbauen (T26) | ⏳ vorgemerkt |

## Version 2.4 · Viel wetten, aber nur wahrscheinliche Ausgänge (Wunsch vom 17.11. abends) — ✅
| Schritt | Status |
|---|---|
| Jede Wette (außer Arbitrage): eigene Gewinnchance ≥ 50 % – so entsteht eine Historie aus gut begründeten Wetten (Einstellung `MIN_WIN_P`) | ✅ mit Test |
| Nie ein genaues Ergebnis („Exact Score“), auch kein „Nein“ darauf | ✅ mit Test |
| Spiele wieder mit Basisraten (statt nur Buchmacher-Quoten), aber nicht nach Spielbeginn und weiter nur eine Wette je Spiel | ✅ mit Test |
| Basisraten schon ab 30 Vergleichsmärkten im Thema (vorher 50), Hälften ab je 10 – mehr Wetten; Deckel 10 Pkt und Wächter bleiben | ✅ |

## Version 2.5 · Befunde aus dem Claude-Review vom 18.11. (Freigabe Luis) — ✅
| Schritt | Status |
|---|---|
| Wächter sperrt Basisraten eines Themas schon bei „wahrscheinlich schlechter als der Markt“ (z ≥ 1,0, Einstellung `BASE_GUARD_Z`); Anlass: Sport lag mit z = 1,64 genau auf der alten Grenze | ✅ mit Test |
| Lernsystem misst Herausforderer gegen die freigegebene Mischung (nicht mehr gegen das reine Modell) – dieselbe Mischung wird nach der Freigabe nicht erneut vorgeschlagen; „zurück zum reinen Modell“ wird mitgeprüft; alte Testergebnisse gegen einen früheren Stand werden gelöscht | ✅ mit Test |
| Abgelehnte Vorschläge kommen frühestens nach 7 Tagen wieder (`PROPOSAL_REJECT_DAYS`) | ✅ mit Test |
| Bericht und Dashboard: Stern nur an der gehandelten Variante, reines Modell als „Grundlage“ – damit entfällt die falsche Warnung „Wetter: Handel aussetzen“ | ✅ mit Test |
| Bericht „Wetten im Rückblick“: erwartete Gewinnchance beim Kauf gegen Preis und tatsächliche Gewinnquote, je Thema/Grund und nach Vorsprung beim Kauf – beantwortet, warum Wetter-Konten trotz guter Prognose verlieren | ✅ mit Test |
| Strategie-Agent: Polymarket-Fehler 500 beendet nur die Portion (Gespeichertes bleibt, Backtests laufen), Abfrage wird mitgeschrieben (`hist:gamma_fail`, Log `polybot.strategy`) | ✅ mit Test |
| Krypto-Kurse zuerst über Binance-Vision; api.binance.com nur noch als Ersatz (sperrt US-Server, Fehlerquote 50 %) | ✅ mit Test |

Lernen braucht keine Wette: Der Bot speichert jede Prognose (auch ohne Kauf) und vergleicht sie mit dem Ergebnis. Die Wetten
sind die Probe, ob sich die Prognosen in Geld auszahlen.

## Machbarkeit (geprüft)
| Ressource | Heute | Mit Ausbau (geschätzt) | Grenze |
|---|---|---|---|
| RAM | 222 MB | 300–400 MB | 8 GB |
| CPU | 0,4 % | 1–2 % | 8 vCPU |
| Datenbank | einige MB | +5 MB/Tag (Preisverlauf, Prognosen) → ~2 GB/Jahr vor Aufräumen | Volume |
| Polymarket-Abrufe | ~60/Lauf | +20/Stunde Katalog, +40/Lauf Historie | kein veröffentlichtes Limit erreicht |
| Open-Meteo | 5.760 Einheiten/Tag | unverändert, gestaffelt | 10.000/Tag, 600/min |
| The Odds API | ~16/Tag | unverändert | 500/Monat |
| Externe Kosten | 0 € | 0 € | 0 € |

Ergebnis: alles läuft auf dem vorhandenen Railway-Dienst ohne Zusatzkosten.

## Bewusst nicht gebaut
| Wunsch im Auftrag | Entscheidung | Grund |
|---|---|---|
| KI-gestütztes Verstehen von Nachrichten im Bot | nein | braucht ein Sprachmodell; kostenlos nicht verlässlich möglich. Ersatz: Stichwort-Zuordnung + Claude-Supervisor |
| Lokales Sprachmodell auf Railway | nein | langsam ohne Grafikkarte, würde die Serverkosten treiben |
| Eigene Prognosen für Politik, Kultur usw. | noch nicht | ohne Datenmodell wäre es geraten. Diese Märkte werden beobachtet und im Strategie-Labor statistisch ausgewertet |
| GDELT als Nachrichtenquelle | nein | lehnt Abrufe ab (429) |
| Automatische Code-Änderungen durch den Bot | nein | Lernen geschieht über Parameter und Modellwahl mit Freigabe, nie über Code |

## Nächste Schritte (Vorschlag, nach Priorität)
1. **Daten sammeln lassen.** Lernsystem, Strategie-Labor und Basisraten brauchen entschiedene Märkte. Erste belastbare
   Aussagen zu Wetter nach etwa 1–2 Wochen (≥ 100 spätere Prognosen aus ≥ 30 Ereignissen), zu Sport und Krypto später.
2. **Wetter-Modell „weather.emp“ prüfen**, sobald es als Herausforderer genug Daten hat (Stations-Korrektur, gemessene Streuung).
3. ~~Wetter am Messtag mit Live-Messwerten (METAR)~~ ✅ 14.11. (`metar.py`, Modell `weather.live@1`)
4. ~~Aufgezeichnete echte API-Antworten als Testdaten (T18)~~ ✅ 14.11. (Polymarket Gamma/CLOB, METAR; Open-Meteo weiter synthetisch)
5. ~~Automatische Browser-Tests in der CI (T22)~~ ✅ 14.11.
6. **Basisraten verfeinern**, sobald je Bereich einige Hundert entschiedene Märkte da sind: nach Unterbereich (z. B. Fußball
   getrennt von NBA) und nach Markttyp (Sieger, Handicap, Über/Unter).
7. **`engine.py` weiter zerlegen** (T9) – erst wenn das neue System zwei Wochen stabil läuft.
8. **Ursache der Polymarket-Fehler 500 eingrenzen**, sobald `hist:gamma_fail` ein paar Tage gezählt hat (welches Thema, welche Richtung, welche Seite).
9. **Wetter: Mindest-Vorsprung oder Modell prüfen**, falls „Wetten im Rückblick“ zeigt, dass die Wetten seltener gewinnen als erwartet.

## Vorgemerkt (aus früheren Absprachen)
Wetter am Messtag mit Live-Messwerten (METAR) · Export als Tabelle · Auswertung nach Quelle
(Liga, Stadt, Markt-Typ) · weitere RSS-Quellen nach Prüfung.
