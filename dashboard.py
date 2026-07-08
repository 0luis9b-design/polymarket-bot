import streamlit as pd
import streamlit as st
import os
import time
import threading
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
# Wir importieren die Tabellen-Struktur und die Scan-Funktion aus der main.py
from main import Base, SimulatedTrade, fetch_and_process_markets

# 1. ERWEITERTES DASHBOARD-STYLING
st.set_page_config(page_title="Polymarket Quant Bot", page_icon="🤖", layout="wide")

# 2. DATENBANK-VERBINDUNG
DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
else:
    DATABASE_URL = "sqlite:///local_bot.db"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 3. DER BULLETPROOF HINTERGRUND-WORKER (Der Bot im Dashboard)
@st.cache_resource
def start_bot_background_worker():
    def bot_loop():
        print("🤖 [Hintergrund-Bot] Engine erfolgreich im Dashboard-Thread gestartet!")
        while True:
            try:
                fetch_and_process_markets()
            except Exception as e:
                print(f"❌ [Hintergrund-Bot] Fehler im Scan-Durchlauf: {e}")
            # Alle 5 Minuten (300 Sekunden) neu scannen
            time.sleep(300)

    # Starte den Bot als Daemon-Thread (stirbt automatisch, wenn das Dashboard stoppt)
    bot_thread = threading.Thread(target=bot_loop, daemon=True)
    bot_thread.start()
    return True

# Aktiviert den Bot-Worker genau EINMAL beim Laden des Dashboards
start_bot_background_worker()

# 4. DASHBOARD OBERFLÄCHE (UI)
st.title("🤖 Polymarket AI Quantitative Paper-Trading Dashboard")
st.subheader("Live-Analyse & Mathematische Risiko-Engine")

# Daten aus der Postgres-Datenbank laden
db = SessionLocal()
try:
    trades = db.query(SimulatedTrade).order_counts = db.query(SimulatedTrade).count()
    all_trades = db.query(SimulatedTrade).all()
finally:
    db.close()

# METRIKEN BERECHNEN
total_trades = len(all_trades)
simulated_balance = 1000.0 - (total_trades * 10.0) # Startkapital 1000€ minus Einsätze

# UI Layout in Spalten
col1, col2, col3 = st.columns(3)
with col1:
    st.metric(label="Virtuelles Restkapital", value=f"{simulated_balance:.2f} €")
with col2:
    st.metric(label="Platzierte Wetten (Gesamt)", value=str(total_trades))
with col3:
    st.metric(label="Bot-Status", value="AKTIV (Scannt alle 5 Min)", delta="Online")

st.markdown("---")
st.write("### 📊 Aktuelle Positionen & Platzierte KI-Wetten")

if total_trades > 0:
    # Tabelle hübsch aufbereiten
    trade_list = []
    for t in all_trades:
        trade_list.append({
            "ID": t.id,
            "Markt / Event": t.market_title,
            "Kategorie": t.category,
            "KI-Tipp": t.prediction_selection,
            "KI-Sicherheit": f"{t.confidence_calculated:.1f}%",
            "Einsatz": f"{t.stake_euro} €",
            "Status": t.status
        })
    st.dataframe(trade_list, use_container_width=True)
else:
    st.info("Der Bot arbeitet im Hintergrund. Sobald ein Markt die mathematische Hürde (1.2 / n) überspringt, taucht die Wette hier live auf! Aktualisiere die Seite in ein paar Minuten.")
