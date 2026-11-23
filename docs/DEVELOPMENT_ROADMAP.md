# Entwicklungs-Roadmap

Priorität: **1** = verhindert Schaden oder ist Voraussetzung · **2** = Kern des Zielsystems · **3** = Ausbau.
Status: ✅ erledigt (mit Tests belegt) · 🔧 in Arbeit · ⏳ geplant. Stand: 22.11.2026, Version 2.9.1.

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
| Datensatz ohne Auswahl nach Endvolumen aufbauen (T26) | ✅ 2.7 |

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
| 2.5.1: METAR-Budget-Meldungen als eine Sammelzeile alle 10 Minuten statt über 1.000 Zeilen nach jedem Neustart | ✅ mit Test |

Lernen braucht keine Wette: Der Bot speichert jede Prognose (auch ohne Kauf) und vergleicht sie mit dem Ergebnis. Die Wetten
sind die Probe, ob sich die Prognosen in Geld auszahlen.

## Version 2.6 · Aufgeräumte Empfehlungen, Summen-Prüfung repariert (Wunsch vom 21.11.) — ✅
| Schritt | Status |
|---|---|
| Empfehlungen gleichen sich nach jedem Bericht ab: gleiches Thema mit neuen Zahlen ersetzt das alte (`reports.rec_key`), was der neue Bericht nicht mehr nennt, wird automatisch erledigt, Verworfenes kommt 7 Tage nicht wieder; im Dashboard steht der Grund dabei | ✅ mit Test |
| Einmaliger Abgleich der bisherigen 19 offenen Empfehlungen mit dem neuesten Bericht (kv `recs_tidy_v1`) | ✅ mit Test |
| Summen-Prüfung nur, wenn auch die Marktkurse zusammen etwa 100 % ergeben; „genau eine trifft zu“ kommt aus der Angabe am Markt (`negRisk`), nicht mehr aus `enableNegRisk` am Ereignis. Anlass: Bericht 18.11. – 653 gesperrte Krypto-Prognosen (Kurs-Leitern), Modell dort besser als der Markt (Brier 0,080 gegen 0,112) | ✅ mit Test |

## Version 2.7 · Saubere Vergleichsdaten, Auswertung nach Quelle (Wunsch vom 21.11.) — ✅
| Schritt | Status |
|---|---|
| Vergleichsdaten ohne Auswahl nach Endvolumen (`HISTORY_MIN_VOLUME` = 0): Das Endvolumen entsteht erst nach dem Messzeitpunkt, eingetroffene Außenseiter wurden deshalb bevorzugt gesammelt (T26) | ✅ mit Test |
| Ein Kurs zählt nur, wenn sich der Preis in den 48 h davor bewegt hat (mind. 3 verschiedene Kurse) – nutzt nur, was damals schon bekannt war | ✅ mit Test |
| Neue Zeilen tragen `sel = 2`; Basisraten, Backtests und Kalibrierung nutzen nur diese; alte Zeilen werden beim Sammeln nach und nach neu berechnet; 120 statt 60 Märkte je Lauf, damit der Neuaufbau schneller geht | ✅ mit Test |
| Seite „Auswertung“: abgeschlossene Wetten nach Liga/Stadt/Coin, Art der Wette (Sieger, Unentschieden, Über/Unter, Handicap, Karte; Wetter genauer Wert/Spanne/Randbereich), gekaufter Seite, Grund und Gewinnchance beim Kauf – mit Treffer gegen Preis; filterbar wie alle Paper-Seiten | ✅ Test + Browser-Test |
| Claude-Bericht: beste und schwächste Quellen, Arten der Wette, Stand des Neuaufbaus der Vergleichsdaten | ✅ mit Test |

Übergang: Bis genug neue Vergleichsdaten da sind (je Thema und Kursstufe mindestens 30 Märkte), wetten die Basisraten kaum –
bei Wetter, Krypto und Sport voraussichtlich nach wenigen Tagen, bei kleinen Themen später. Die eigenen Modelle sind nicht betroffen.

## Version 2.8 · Rückblick: nur dort wetten, wo es sich gelohnt hätte (Bericht #4 vom 22.11.) — ✅
Befund: Wetter- und Krypto-Prognosen sind im Schnitt genauer als der Markt, die Wetten darauf verlieren trotzdem
(Wetter 19 % gewonnen bei 40 % Preis, n = 298; Krypto 41 % bei 60 %, n = 184). In den Logs: fast nur „Nein“ auf den
Temperatur-Bereich, den der Markt für den wahrscheinlichsten hält (15–22 Pkt Abweichung) und „Nein“ auf „Fällt Bitcoin auf …“
(2 von 21 gewonnen bei 48 % Preis). Gewettet wird, wo Modell und Markt weit auseinanderliegen – dort lag meist der Markt richtig.

| Schritt | Status |
|---|---|
| `hindsight.py`: stündlich (Lern-Agent) für alle entschiedenen Prognosen der letzten 30 Tage nachrechnen, ob eine Wette auf die Seite des Modells zum damaligen Marktpreis gewonnen hätte – je Thema · Wettart · Seite · Abweichung (3–8 / 8–15 / 15–25 / über 25 Pkt), Sicherheit über unabhängige Ereignisse | ✅ mit Test |
| Engine: keine Modell-Wette und kein Limit-Gebot in Bereichen mit gesichertem Verlust (z ≤ −1,64, ≥ 20 Ereignisse); genauester Bereich mit genug Daten entscheidet; Basisraten, feste Regeln und Arbitrage unberührt; gibt sich von selbst wieder frei | ✅ mit Test |
| Einstellungen `HINDSIGHT` (an/aus) und `HINDSIGHT_Z`; Änderungen als Ereignis „rueckblick“ (ohne Telegram) | ✅ |
| Seite „Auswertung“: Tabelle „Rückblick“; Claude-Bericht Abschnitt 7 | ✅ Test + Browser-Test |
| Bericht: Empfehlung „Wetten gewinnen seltener als erwartet“ nur noch aus Wetten seit dem Rückblick (höchstens 14 Tage alt); Prüfregel-Auswertung zählt nur Ablehnungen nach der letzten Regeländerung (Summen-Prüfung seit 21.11.) | ✅ mit Test |
| Export als Tabelle: Knopf „Als Tabelle herunterladen“ auf der Seite „Wetten“ (gleiche Auswahl, öffnet in Excel, Marktfragen gegen Formel-Einschleusung geschützt) | ✅ mit Test |

## Version 2.9 · Befunde aus dem Claude-Review vom 22.11. (Freigabe Luis: „Alles bauen“) — ✅
Befund 1: E-Sport schien 0 von 83 Counter-Strike-Wetten zu gewinnen (Preis Ø 47 %). In der Abrechnung (`engine.settle`,
`polymarket.resolution`) fand sich kein Fehler; belegt ist ein Fehler in der Zählung: „Treffer“ zählte nur abgerechnete Wetten. Gewinner wurden aber meist kurz vor Schluss mit „Gewinn
mitgenommen“ verkauft (z. B. Dota 2 „Froglings … Game 1“ zu 99,9 ¢) und fehlten – übrig blieben fast nur die Verlierer.
Darum auch „Dota 2: 7 % Treffer, aber +287 $“.

| Schritt | Status |
|---|---|
| „Treffer“ in Auswertung und Bericht zählt verkaufte Wetten mit, sobald der Markt entschieden ist (Ausgang aus `outcomes`, `paperview.hit`); Spalte heißt jetzt „mit Ausgang“ | ✅ mit Test |
| Abrechnung holt jeden 3. Lauf auch den Ausgang verkaufter Märkte der letzten 30 Tage (höchstens 200 je Lauf) | ✅ mit Test |
| Bericht „Wirkung des Rückblicks“: Modell-Wetten und Limit-Gebote gleich lange vor und nach dem Start des Rückblicks – Wetten je Tag, Ausgang für uns gegen Preis, G/V; dazu gesperrte Märkte je Tag (kv `rueckblick:gesperrt`, Log-Zeile „Lauf“) | ✅ mit Test |
| Bericht „Wetter: Ist die Vorhersage zu breit?“ (`weather_check.py`): gemessene Temperatur gegen Ensemble-Mittel, geteilt durch die Modellbreite (soll: 50 % innen, Streuung 1,0) je Vorlauf; Favoriten-Bereich des Marktes: eingetroffen gegen Modell und Markt. Empfehlung nur bei ≥ 100 Tagen und klarer Abweichung | ✅ mit Test |

Wird die Wetter-Breite als falsch gemessen, braucht es eine schmalere Variante als
Herausforderer (Lernsystem, Freigabe im Dashboard) – nichts ändert sich von selbst.

## Version 2.9.1 · Trefferquote überall gleich, Polymarket-Fehler 500 umgangen (Freigabe Luis 22.11.) — ✅
Befund aus den Logs 18.–22.11.: Der Fehler 500 beim Sammeln kam nie zufällig, sondern immer bei denselben Abfragen – vor dem
21.11. bei den älteren Märkten auf Seite 1 (z. B. Politik bis 31.07., Kultur bis 01.09., Krypto bis 01.07.: der
Rückwärts-Durchlauf blieb dort tagelang hängen), seitdem z. B. Wirtschaft · neueste · Seite 3, Welt · neueste · Seite 7,
Technik · neueste · Seite 5. Polymarket selbst konnten wir von hier nicht abfragen; am wahrscheinlichsten bringt ein einzelner
Markt die ganze Seite zum Absturz.

| Schritt | Status |
|---|---|
| `StrategyAgent.fetch_page`: Seite mit Fehler 500 wird geteilt (100 → 25 → 5 → 1), nur der defekte Eintrag übersprungen (höchstens 40 Zusatz-Abrufe je Lauf); kommt nichts durch, gilt es als Störung wie bisher | ✅ mit Test |
| Gezählt in kv `hist:gamma_skip` (übersprungene Einträge) neben `hist:gamma_fail` (ganze Seite ausgefallen); beides im Bericht, Abschnitt 11 | ✅ mit Test |
| „Trefferquote“ überall nach Markt-Ausgang: Startseite, Konten, Strategie-Tabelle, Telegram-Bilanzen, Bericht (`store.HIT_SQL`, `store.DECIDED_SQL`) | ✅ mit Test |

Prüfen im Bericht Mi 25.11.: Sinkt „ganze Seite ausgefallen“, und wie viele Einträge wurden übersprungen?

## Version 2.10 · Alles tippen (Wunsch vom 23.11.) — ✅
Frage von Luis: „Warum tippt der Bot nicht die Tennis Matches oder andere Sportarten wie Tischtennis? Er soll wirklich alles
tippen, damit wir nach einer Zeit die Strategien verbessern können.“ Ursache: Sport holte der Bot nur für die Ligen mit
Buchmacher-Quoten (Fußball, NFL, UFC); Tennis/Tischtennis kamen höchstens über „weitere Märkte“, dort aber keine Basisraten
(Name gegen Name statt Ja/Nein; Sport-Basisraten zudem vom Wächter gesperrt). Live 23.11.: viele Tennis- und alle
Tischtennis-Märkte ohne echtes Orderbuch (z. B. Kauf 30 / Verkauf 70 ¢ oder gar kein Gebot).

| Schritt | Status |
|---|---|
| Neue Strategie „Alles tippen“ (11 Konten `alles_<thema>`): je Spiel/Ereignis eine Wette auf den Favoriten laut Markt – Sieger-Markt vor Zusatzmärkten, Sieger-Tipp vor „Nein“, Kaufkurs 50–90 ¢, je 5 $, hält bis zur Entscheidung | ✅ mit Test |
| Sammeln: Sieger-Märkte aller Sportarten und E-Sport (`ALL_TAGS` = sports:3, esports:3, table-tennis:10 Tage), alle 30 min; Spiele, die in den nächsten 48 h beginnen; andere Themen bis 7 Tage | ✅ mit Test |
| Feste Regeln bleiben: nie ein genaues Ergebnis, Gewinnchance ≥ 50 %, eine Wette je Spiel, nicht nach Spielbeginn, nur mit echtem Orderbuch (Spanne ≤ 10 ¢, Liquidität ≥ 100 $), Themen-Schalter gelten | ✅ mit Test |
| Bald beginnende Spiele zuerst, höchstens 40 neue Wetten je Konto und Lauf | ✅ mit Test |
| Abrechnung von Spielen schon 2 h nach Spielbeginn statt erst zum offiziellen Marktende (Tischtennis: Ende = Beginn + 7 Tage) – gilt für alle Konten | ✅ mit Test |
| Einstellungen `ALL_TIPS` (an/aus) und `ALL_STAKE` (Einsatz) | ✅ |
| 2.10.2: Erster Lauf brachte fast nur Tischtennis (Ukraine/Tschechien, alle 30 min ein Spiel) – je Konto und Sportart höchstens 25 offene Wetten (`ALL_MAX_PER_SUB`) | ✅ mit Test |
| 2.10.1: Live-Start 23.11.: `/markets` lieferte die Spiele ohne Tags → erste 40 Tipps unter „Sonstiges“. Jetzt `include_tag` und notfalls der Tag, über den geholt wurde; einmalige Umbuchung samt Einsatz (`Engine.refile_all`, kv `alles_refile_v1`) | ✅ mit Test |

## Version 2.11 · Form-Modell: Stärke nach Ergebnissen und Gegnern (Freigabe 23.11.: „Du kannst alles kostenlose umsetzen“) — ✅
Frage von Luis: Analysiert der Bot jede Wette genau (direkter Vergleich, Form, letzte Spiele angepasst an die Gegner,
Verletzungen)? Antwort: nein – Sport lief nur über Buchmacher-Quoten. Umgesetzt wurde, was kostenlos geht.

| Schritt | Status |
|---|---|
| `form_model.py`: Elo-Wertung je Sportart aus entschiedenen Polymarket-Sieger-Märkten („Name gegen Name“: Tennis, Tischtennis, Basketball, Eishockey, MMA, E-Sport …); K-Faktor nach Spielzahl; Prognose nur, wenn beide Seiten ≥ 8 gewertete Spiele haben | ✅ mit Test |
| Sammeln (stündlich, 20 Abrufe): zuerst die letzten 36 h, dann rückwärts Tag für Tag bis 90 Tage (Zeiger kv `form:back`, Tabelle `match_results`, Migration 11) | ✅ mit Test |
| Fußball: ClubElo-Spielplan (frei, ohne Schlüssel) mit Wahrscheinlichkeit je Tordifferenz → Sieg/Remis/Niederlage inkl. Heimvorteil; Zuordnung über Teamnamen und Spieltag | ✅ mit Test |
| Verletzte/Gesperrte: API-Football (Gratis-Plan, Schlüssel `API_FOOTBALL_KEY` trägt Luis selbst in Railway ein), 2 Abrufe alle 3 h, Schlüssel nur im Kopf der Anfrage, aus Fehlertexten geschwärzt; angezeigt und als Merkmal gespeichert, ändert die Wahrscheinlichkeit noch nicht | ✅ mit Test |
| Messen: jede Prognose als `form.elo` in der Prognose-Historie (nicht aktiv gehandelt in den Hauptstrategien); Bericht Abschnitt 7: Daten je Sportart und Brier Form-Modell gegen Markt | ✅ mit Test |
| Neue Strategie „Form (Elo)“ (Konten `form_sport`, `form_esport`): Wette, wenn das Modell mind. 5 Pkt mehr sieht als der Kaufpreis und die Seite ≥ 50 % hat; je 5 $; eine Wette je Spiel, vor Spielbeginn, echtes Orderbuch; Einstellungen `FORM_TIPS`, `FORM_MIN_EDGE`, `FORM_STAKE` | ✅ mit Test |

2.11.1 nach dem Live-Start: Das Sammeln begann 90 Tage zurück (Wertungen aus August, Wetten mit 14–18 Pkt Vorsprung auf
wenige alte Spiele) – jetzt zuerst die letzten 36 h, dann rückwärts; Wertung erst ab 8 Spielen je Seite. ClubElo antwortete
nicht (Zeitüberschreitung) – neuer Versuch nach 1 h mit 40 s Wartezeit, Fehler steht im Bericht.

Nicht gebaut (kostet Geld oder braucht ein Sprachmodell): mehr Buchmacher-Quoten (The Odds API ab 30 $/Monat),
Nachrichten verstehen. Direkter Vergleich: steckt in der Elo-Wertung, sofern die Seiten schon gegeneinander gespielt haben;
für sich allein sagt er wenig über das nächste Spiel.

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
8. ~~Ursache der Polymarket-Fehler 500 eingrenzen~~ ✅ 2.9.1: immer dieselben Abfragen; defekte Einträge werden übersprungen.
9. ~~Wetter: Mindest-Vorsprung oder Modell prüfen~~ → 2.8 Rückblick sperrt verlierende Bereiche automatisch. Ob die Streuung zu
   breit ist, misst seit 2.9 der Bericht (Abschnitt 7). Nächster Schritt erst nach dem Messergebnis.
10. **Wirkung des Rückblicks prüfen** (Bericht Mi 25.11., Abschnitt 9 „Wirkung des Rückblicks“): verlieren Wetter- und
    Krypto-Wetten seitdem weniger?

## Vorgemerkt (aus früheren Absprachen)
~~Wetter am Messtag mit Live-Messwerten (METAR)~~ ✅ · ~~Export als Tabelle~~ ✅ 2.8 · ~~Auswertung nach Quelle (Liga, Stadt, Markt-Typ)~~ ✅ 2.7 ·
weitere RSS-Quellen nach Prüfung.
