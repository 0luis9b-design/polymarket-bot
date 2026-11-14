"""Versionsstand des Systems. CHANGES wird im Claude-Bericht unter „Neue Funktionen“ gezeigt."""
VERSION = "2.0.0"

# (Datum, Version, Text) – neueste zuerst
CHANGES = [
    ("2026-11-14", "2.0.0", "Agenten-Architektur mit Koordinator (Scanner, Probability, Validation, Learning, Strategy, News, Guardian)"),
    ("2026-11-14", "2.0.0", "Prognose-Historie unveränderlich, mit Modell, Version, Quellen und Prüfergebnis"),
    ("2026-11-14", "2.0.0", "Validierung sperrt unplausible Prognosen fürs Handeln (Abweichungs-Deckel, veraltete Daten)"),
    ("2026-11-14", "2.0.0", "Marktkatalog aller Kategorien mit stündlichem Preisverlauf, neuen Märkten und Bewegungen"),
    ("2026-11-14", "2.0.0", "Lernsystem: Kennzahlen je Modell, Experimente mit Out-of-Sample-Test, Vorschläge mit Freigabe"),
    ("2026-11-14", "2.0.0", "Strategie-Labor: Datensatz entschiedener Märkte und Backtests mit zeitlicher Trennung"),
    ("2026-11-14", "2.0.0", "Quellen-Budgets, tägliches Backup, Datenbank-Migrationen, Schwärzen von Geheimnissen in Fehlertexten"),
    ("2026-11-14", "2.0.0", "Telegram mit Prioritäten, Tageslimit und Ruhezeit; Claude-Review-Bericht Mi und So"),
    ("2026-11-14", "2.0.0", "Neues Dashboard mit 12 Bereichen"),
    ("2026-11-13", "1.3.0", "Hauptkonto in Krypto, Sport und Wetter aufgeteilt"),
    ("2026-11-12", "1.2.0", "Wetter-Märkte, Limit-Orders, Außenseiter-Konto, Risikobremse, Wachhund"),
    ("2026-11-12", "1.1.0", "Deribit-Optionen, Arbitrage, Orderbuch-Ausführung, Testkonten, Telegram"),
]
