import os
import time
import requests
from sqlalchemy import create_engine, Column, Integer, String, Float, JSON
from sqlalchemy.orm import declarative_base, sessionmaker

# 1. DATENBANK-VERBINDUNG (Liest automatisch Railway aus)
# Falls lokal getestet wird, nutzt es zur Sicherheit eine lokale SQLite-Datei
DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
else:
    DATABASE_URL = "sqlite:///local_bot.db"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# 2. DATENBANK-TABELLE FÜR DIE IMAGINÄREN WETTEN
class SimulatedTrade(Base):
    __tablename__ = "simulated_trades"
    
    id = Column(Integer, primary_key=True, index=True)
    market_title = Column(String, nullable=False)
    category = Column(String, default="Uncategorized")
    prediction_selection = Column(String, nullable=False)  # Worauf gewettet wurde (z.B. "Yes")
    confidence_calculated = Column(Float, nullable=False)   # Berechnete Sicherheit in %
    stake_euro = Column(Float, default=10.0)                # Immer 10 Euro
    entry_price = Column(Float, nullable=False)             # Quote beim Kauf
    status = Column(String, default="OPEN")                 # OPEN, WON, LOST

# Tabellen in Railway/Postgres erstellen, falls sie noch nicht existieren
Base.metadata.create_all(bind=engine)

# 3. MATHEMATISCHE RISIKO-BERECHNUNG
def check_risk_and_decision(outcomes_count, ai_confidence):
    """
    Berechnet dynamisch das Risiko basierend auf der Formel: P = 1.2 / n
    Gibt True zurück, wenn die KI-Sicherheit hoch genug ist.
    """
    if outcomes_count < 2:
        return False
        
    # Dynamischer Schwellenwert (z.B. 1.2 / 2 = 60% | 1.2 / 3 = 40%)
    required_threshold = 1.2 / outcomes_count
    
    # Umwandlung in Prozentwerte für den Vergleich (z.B. 0.60 -> 60%)
    required_percentage = required_threshold * 100
    
    if ai_confidence >= required_percentage:
        return True, required_percentage
    return False, required_percentage

# 4. POLYMARKET API ABFRAGE (GAMMA API)
def fetch_polymarket_events():
    """Holt die aktuellsten Live-Märkte von Polymarket"""
    url = "https://gamma-api.polymarket.com/events?active=true&limit=10"
    try:
        response = requests.get(url)
        if response.status_with == 200:
            return response.json()
    except Exception as e:
        print(f"Fehler beim Abrufen der Polymarket-Daten: {e}")
    return []

# Hauptschleife des Bots
if __name__ == "__main__":
    print("Polymarket Paper-Trading Bot gestartet...")
    # Hier wird der Bot später in einer Endlosschleife laufen
    # Für den ersten Testlauf holen wir einmal Daten:
    markets = fetch_polymarket_events()
    print(f"{len(markets)} Märkte erfolgreich von Polymarket eingelesen!")
