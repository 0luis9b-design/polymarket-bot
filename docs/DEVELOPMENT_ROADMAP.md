# Entwicklungs-Roadmap

Priorität: **1** = verhindert Schaden oder ist Voraussetzung · **2** = Kern des Zielsystems · **3** = Ausbau.
Status: ✅ erledigt · 🔧 in Arbeit · ⏳ geplant.

## Phase 1–2 · Audit und Architektur — ⏳
Dokumente: `PROJECT_AUDIT.md`, `SYSTEM_ARCHITECTURE.md`, `TECHNICAL_DEBT.md`, `RISK_REGISTER.md`, diese Datei.

## Phase 3 · Fundament (Priorität 1) — ⏳
| Schritt | Löst | Status |
|---|---|---|
| Nummerierte Datenbank-Migrationen | T6 | ⏳ |
| Tägliches Backup + Integritätsprüfung | T3, R3 | ⏳ |
| Quellen-Verwaltung: Budget, Mindestabstand, Fehler-Pause, Statistik | T1, T14, R1 | ⏳ |
| Fehlertexte schwärzen, `/health` entschärfen, `/api/scan` entfernen | T4, T13, R7 | ⏳ |
| Agenten-Gerüst + Koordinator mit Zeitbudget | T9 | ⏳ |
| Schleife startet nach Absturz neu, Notfall-Neustart bei Hänger | T11 | ⏳ |
| Risikobremse rastet für den Tag ein | T8 | ⏳ |

## Phase 4 · Market Intelligence (Priorität 2) — ⏳
Marktkatalog aller Kategorien (je Kategorie die aktivsten Märkte), stündlicher Preisverlauf, neue Märkte,
auffällige Bewegungen, Datenqualität, Priorität je Markt.

## Phase 5 · Agenten (Priorität 1–2) — ⏳
| Schritt | Löst | Status |
|---|---|---|
| Validation: Abweichungs-Deckel, Frische, Stimmigkeit – sperrt fürs Handeln | T2, R2 | ⏳ |
| Probability: Modell-Register, Varianten, Unsicherheit, Prognose-Historie (unveränderlich) | T5 | ⏳ |
| News: BBC-RSS, Zuordnung zu Märkten | – | ⏳ |
| Guardian: Ressourcen, Quellen-Ausfälle, Aufräumen | R1, R8 | ⏳ |

## Phase 6 · Lernsystem (Priorität 2) — ⏳
Kennzahlen (Brier, LogLoss, Kalibrierung, Treffer, Verzerrung) je Modell, Kategorie, Zeithorizont gegen
Marktpreis und 50-%-Baseline · Fehlerarten · Experimente mit zeitlicher Trennung · Vorschläge mit Freigabe
und Rückholung · Wetter: Stations-Korrektur und gemessene Streuung als lernendes Modell (T15) ·
Selbstkorrektur nur noch nach Freigabe (T7).

## Phase 7 · Backtesting (Priorität 2) — ⏳
Datensatz entschiedener Märkte (Preis 1 h/6 h/24 h/72 h/7 T vor Schluss + Ergebnis) aus Polymarket-Historie ·
Strategie-Tests mit Gebühren, In-Sample/Out-of-Sample, Warnung bei Überanpassung · gespeichert und wiederholbar.

## Phase 8 · Dashboard (Priorität 1) — ⏳
Neues Gerüst mit Seitenleiste, 12 Bereiche, je Bereich eigener Daten-Abruf (T10), JS/CSS als Dateien (T16).

## Phase 9 · Telegram und Supervisor (Priorität 2) — ⏳
Prioritäten, Tageslimit, Ruhezeit · Claude-Review-Bericht Mi + So · geplante Claude-Sitzung.

## Phase 10–11 · Qualität und Betrieb (Priorität 1) — ⏳
Tests für jedes neue Modul · CSRF-Schutz, Anmelde-Sperre, Sicherheits-Header (T12) · Abhängigkeits-Prüfung
in der CI (T17) · ein Push · Kontrolle im Live-Betrieb.

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

## Vorgemerkt (aus früheren Absprachen)
Wetter am Messtag mit Live-Messwerten (METAR) · Export als Tabelle · Auswertung nach Quelle
(Liga, Stadt, Markt-Typ) · weitere RSS-Quellen nach Prüfung.
