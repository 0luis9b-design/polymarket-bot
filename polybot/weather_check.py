"""Wetter: Ist die Vorhersage zu breit? (2.9, offene Frage aus Bericht #4)

Anlass: Die Wetter-Prognosen sind im Schnitt genauer als der Markt, die Wetten verlieren trotzdem. Gewettet wurde vor allem
„Nein“ auf den Temperatur-Bereich, den der Markt für den wahrscheinlichsten hält – und dort lag meist der Markt richtig.
Verdacht: Die Verteilung des Modells ist zu breit und gibt dem wahrscheinlichsten Bereich zu wenig.

Zwei Messungen, beide nur aus entschiedenen Märkten und nur mit dem, was zum Prognose-Zeitpunkt bekannt war:

1. Streuung: Für jeden entschiedenen Tag (Station · Datum · Höchst/Tiefst) und Zeithorizont die gemessene Temperatur
   (Mitte des eingetroffenen Bereichs) gegen das Ensemble-Mittel, geteilt durch die Breite, mit der das Modell rechnet
   (Ensemble-Streuung plus Verschmierung, dazu die Breite des Bereichs). Passt die Breite, liegt die Hälfte der Tage
   innerhalb von ±0,67 und die Streuung dieser Werte bei 1,0. Darunter = Modell zu breit, darüber = zu eng.
2. Favorit: Je Tag und Zeithorizont der Bereich mit dem höchsten Marktpreis. Wie oft traf er ein – gegen das, was Modell
   und Markt ihm gegeben hatten?

Nur Messung: Es ändert sich nichts am Handel. Eine schmalere Variante müsste als Herausforderer laufen und über das
Lernsystem freigegeben werden.
"""
import json
import math
import statistics
from collections import defaultdict

from . import config

LEADS = ((-99, 12, "unter 12 h"), (12, 36, "etwa 1 Tag"), (36, 60, "etwa 2 Tage"), (60, 9999, "3 Tage und mehr"))
Z50 = 0.6745          # ±0,67 Standardabweichungen umfassen 50 % einer Normalverteilung


def lead_name(lead_h):
    for lo, hi, name in LEADS:
        if lo <= (lead_h if lead_h is not None else 24) < hi:
            return name
    return LEADS[-1][2]


def model_sd(f):
    """Breite, mit der weather.ens rechnet: Ensemble-Streuung und Verschmierung (wie in weather_model.analyze)."""
    base = config.WEATHER_SPREAD_C if f.get("unit") == "C" else config.WEATHER_SPREAD_C * 1.8
    lead_days = max(0.0, (f.get("lead_h") if f.get("lead_h") is not None else 24) / 24 + 0.5)
    smear = base * (1 + 0.15 * lead_days)
    return math.sqrt((f.get("ens_sd") or 0.0) ** 2 + smear ** 2)


def spread(store, min_n=20):
    """Messung 1. Gibt je Vorlauf n, Anteil innerhalb ±0,67, Streuung der z-Werte und mittlere Verschiebung zurück."""
    seen, by_lead = set(), defaultdict(list)
    for r in store.q("SELECT f.cluster, f.horizon, f.features FROM forecasts f JOIN outcomes o ON o.market_id=f.market_id "
                     "WHERE f.category='weather' AND f.model='weather.ens' AND f.features IS NOT NULL AND o.outcome=1"):
        try:
            x = json.loads(r["features"])
        except (TypeError, ValueError):
            continue
        if x.get("lo") is None or x.get("hi") is None or x.get("ens_mean") is None:
            continue                       # Rand-Bereiche („≤ 5 °C“) sagen nicht, wie warm es genau war
        key = (r["cluster"], r["horizon"])
        if key in seen:
            continue
        seen.add(key)
        width = x["hi"] - x["lo"]
        sd = math.sqrt(model_sd(x) ** 2 + width ** 2 / 12)   # gleichverteilt im Bereich
        resid = (x["lo"] + x["hi"]) / 2 - x["ens_mean"]
        by_lead[lead_name(x.get("lead_h"))].append(resid / sd)
    out = []
    for _, _, name in LEADS:
        zs = by_lead.get(name) or []
        if len(zs) < min_n:
            if zs:
                out.append({"vorlauf": name, "n": len(zs), "innen50": None, "streuung": None, "verschiebung": None})
            continue
        mean = sum(zs) / len(zs)
        out.append({"vorlauf": name, "n": len(zs), "innen50": sum(1 for z in zs if abs(z) < Z50) / len(zs),
                    "streuung": statistics.pstdev(zs), "verschiebung": mean})
    return out


def favourite(store, min_n=20):
    """Messung 2. Je Tag und Zeithorizont der Bereich mit dem höchsten Marktpreis: eingetroffen gegen Modell und Markt.

    „gehandelt“ = die Variante, mit der gehandelt wird (Stern im Bericht), „Ensemble“ = reines weather.ens."""
    groups = defaultdict(list)
    for r in store.q("SELECT f.cluster, f.horizon, f.p, f.market_p, f.champion, f.model, f.hours_left, o.outcome "
                     "FROM forecasts f JOIN outcomes o ON o.market_id=f.market_id "
                     "WHERE f.category='weather' AND f.market_p IS NOT NULL AND f.cluster IS NOT NULL "
                     "AND (f.champion=1 OR f.model='weather.ens')"):
        groups[(r["cluster"], r["horizon"], "gehandelt" if r["champion"] else "Ensemble")].append(r)
    agg = defaultdict(lambda: {"n": 0, "modell": 0.0, "markt": 0.0, "ein": 0.0})
    for (_cl, _hz, which), rs in groups.items():
        if len(rs) < 3 or sum(r["outcome"] for r in rs) != 1:
            continue                       # nur vollständige Tage: mehrere Bereiche, genau einer traf ein
        fav = max(rs, key=lambda r: r["market_p"])
        lead = (fav["hours_left"] or 24) - 24   # hours_left zählt bis Tagesende, Vorlauf bis Tagesbeginn
        a = agg[(which, lead_name(lead))]
        a["n"] += 1
        a["modell"] += fav["p"]
        a["markt"] += fav["market_p"]
        a["ein"] += fav["outcome"]
    out = []
    for which in ("gehandelt", "Ensemble"):
        for _, _, name in LEADS:
            a = agg.get((which, name))
            if not a or a["n"] < min_n:
                continue
            out.append({"variante": which, "vorlauf": name, "n": a["n"], "modell": a["modell"] / a["n"],
                        "markt": a["markt"] / a["n"], "eingetroffen": a["ein"] / a["n"]})
    return out


def verdict(rows, min_n=100):
    """Ein Satz für den Bericht – nur bei genug Tagen."""
    rs = [r for r in rows if r["streuung"] is not None]
    n = sum(r["n"] for r in rs)
    if n < min_n:
        return None
    s = sum(r["streuung"] * r["n"] for r in rs) / n
    if s < 0.85:
        return f"Die Vorhersage ist zu breit: Die Temperaturen streuen nur etwa {s * 100:.0f} % so weit, wie das Modell annimmt ({n} Tage)."
    if s > 1.15:
        return f"Die Vorhersage ist zu eng: Die Temperaturen streuen etwa {s * 100:.0f} % so weit, wie das Modell annimmt ({n} Tage)."
    return f"Die Breite der Vorhersage passt etwa ({s * 100:.0f} % der angenommenen Streuung, {n} Tage)."
