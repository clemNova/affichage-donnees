"""Derive data/chaleur/turpe_htb1_lu.json (TURPE variable HTB1/LU par plage
tarifaire) depuis le CSV horaire de data/export. A relancer si le tarif ou le
calendrier des plages change.

Plage majoritaire par (mois, jour de semaine, heure) sur les 12 derniers mois
du CSV -- les jours feries (plage "week-end") sont donc ignores, ecart
negligeable (< 0.5 EUR/MWh de TURPE, x0.30 dans le cout elec).

Format : "plages"[mois 1-12][jour 0=lundi..6=dimanche] = chaine de 24 codes
(un par heure, heure locale Paris), code -> nom de plage via "codes".
"""

import json
from pathlib import Path

import pandas as pd

DOSSIER = Path(__file__).resolve().parent
CSV = DOSSIER.parent / "export" / "donnees_EPEX_PEG_CO2_RTE_2021-2026_horaire.csv"
COLONNE = "RTE-HTB1_LU_ci"
CODES = {"a": "HCB", "b": "HPB", "c": "HCH", "d": "HPH", "e": "HP"}

df = pd.read_csv(CSV)
df["dt"] = pd.to_datetime(df["datetime_paris"], utc=True).dt.tz_convert("Europe/Paris")
fin = df["dt"].max()
df = df[df["dt"] > fin - pd.DateOffset(years=1)]
df["m"], df["j"], df["h"] = df["dt"].dt.month, df["dt"].dt.weekday, df["dt"].dt.hour

nom_vers_code = {v: k for k, v in CODES.items()}
plages = {}
for (m, j), g in df.groupby(["m", "j"]):
    par_heure = g.groupby("h")["plage_turpe"].agg(lambda s: s.value_counts().index[0])
    plages.setdefault(str(m), {})[str(j)] = "".join(nom_vers_code[par_heure[h]] for h in range(24))

dernier = df.sort_values("dt").groupby("plage_turpe")[COLONNE].last().round(3)
sortie = {"colonne": COLONNE, "codes": CODES, "tarif_eur_mwh": dernier.to_dict(), "plages": plages}
(DOSSIER / "turpe_htb1_lu.json").write_text(json.dumps(sortie, separators=(",", ":")), encoding="utf-8")
print(sortie["tarif_eur_mwh"], len(plages))
