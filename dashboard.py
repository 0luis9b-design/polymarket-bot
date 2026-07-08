import streamlit as st
import os
from sqlalchemy import create_engine, Column, Integer, String, Float
from sqlalchemy.orm import declarative_base, sessionmaker

# 1. DATENBANK-VERBINDUNG (Gleiche Logik wie in der main.py)
DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
else:
    DATABASE_URL = "sqlite:///local_bot.db"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Definition der Tabelle, um darauf zuzugreifen
class SimulatedTrade(Base):
    __tablename__ = "simulated_trades"
    id = Column(Integer, primary_key=True, index=True)
    market_title = Column(String, nullable=False)
    category = Column(String, default="Uncategorized")
    prediction_selection = Column(String, nullable=False)
    confidence_calculated = Column(Float, nullable=False)
    stake_euro = Column(Float, default=10.0)
    entry_price = Column(Float, nullable=False)
    status = Column(String, default="OPEN")

# 2. DASHBOARD OBERFLÄCHE
st.set_page_config(page_title="Polymarket AI Bot", layout="wide")

st.title("🤖 Polymarket AI Paper-Trading Bot")
st.subheader("Übersicht deiner automatisierten, imaginären Wetten")

# Verbindung zur Datenbank herstellen und Daten abrufen
db = SessionLocal()
trades = db.query(SimulatedTrade).all()
db.close()

# Statistiken berechnen
total_bets = len(trades)
open_bets = len([t for t in trades if t.status == "OPEN"])
won_bets = len([t for t in trades if t.status == "WON"])
lost_bets = len([t for t in trades if t.status == "LOST"])

# Spalten für die KPI-Karten
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric(label="Startguthaben (Imaginär)", value="100.00 €")
with col2:
    st.metric(label="Aktive Wetten", value=open_bets)
with col3:
    st.metric(label="Gewonnene Wetten", value=won_bets)
with col4:
    st.metric(label="Verlorene Wetten", value=lost_bets)

st.markdown("---")

# Tabelle mit den Wetten anzeigen
st.write("### 📊 Aktuelle Wett-Historie")
if total_bets == 0:
    st.info("Noch keine Wetten abgeschlossen. Der Bot analysiert gerade im Hintergrund...")
else:
    # Daten für die Anzeige aufbereiten
    table_data = []
    for t in trades:
        table_data.append({
            "ID": t.id,
            "Markt-Thema": t.market_title,
            "Kategorie": t.category,
            "Tipp": t.prediction_selection,
            "KI-Sicherheit": f"{t.confidence_calculated:.1f}%",
            "Einsatz": f"{t.stake_euro:.2f} €",
            "Status": t.status
        })
    st.table(table_data)

st.markdown("---")
st.write("💡 *Hinweis: Dieser Bot nutzt die Formel $P = \\frac{1.2}{n}$ zur Risikobewertung und lernt kontinuierlich aus Fehlern.*")
