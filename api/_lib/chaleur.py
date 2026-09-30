"""Calculs purs du dashboard Chaudieres electriques : cout de revient d'un MWh
thermique gaz vs electrique, heure par heure, et agregats (30j glissants,
mois). Meme methode que data/export/calcul_indice_now.py (cf.
data/export/FICHE_METHODE_COUT_CHALEUR.md), raccordement HTB1/LU (RTE).

Aucun acces KV/reseau ici (cf. chaleur_store.py pour l'orchestration).
"""

from __future__ import annotations

import json
import os

import pandas as pd

# --- Parametres (identiques a calcul_indice_now.py) -------------------------
TURPE_REDUCTION_ELECTRO_INTENSIF = 0.30
ACCISE_ELEC_EUR_MWH = 0.5
RENDEMENT_ELEC = 0.985
RENDEMENT_GAZ = 0.92
FACTEUR_CO2_GAZ_T_PAR_MWH = 0.182  # tCO2 / MWh gaz (PCI)
RATIO_PCS_PCI_GAZ = 1.11

# Pas de pipeline CO2 : derniere valeur du CSV horaire (2026-08-27), a mettre
# a jour a la main tant qu'il n'y a pas de source live.
CO2_EUR_T = 79.0

# Remuneration de reserve aFRR : desactivee en live (cf. echange utilisateur),
# a passer a True quand on voudra la reintegrer. Le stockage horaire
# (chaleur_store.py) devra alors fournir prix_rs_hausse/prix_rs_baisse en
# EUR/MW/h -- unite a verifier vs les prix aFRR capacite du KV avant de
# l'activer (natif RTE en EUR/MW/15min).
AVEC_RESERVE = False

CHEMIN_TURPE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "chaleur", "turpe_htb1_lu.json",
)
_turpe: dict | None = None


def _charge_turpe() -> dict:
    global _turpe
    if _turpe is None:
        with open(CHEMIN_TURPE, encoding="utf-8") as f:
            _turpe = json.load(f)
    return _turpe


def turpe_htb1(ts: pd.Timestamp) -> float:
    """TURPE variable HTB1/LU (EUR/MWh, avant reduction electro-intensif) pour
    une heure locale Paris (timestamp naif)."""
    t = _charge_turpe()
    code = t["plages"][str(ts.month)][str(ts.weekday())][ts.hour]
    return t["tarif_eur_mwh"][t["codes"][code]]


def cout_gaz(peg_pcs: float, co2: float = CO2_EUR_T) -> float:
    return (peg_pcs + co2 * FACTEUR_CO2_GAZ_T_PAR_MWH) * RATIO_PCS_PCI_GAZ / RENDEMENT_GAZ


def cout_elec(epex: float, turpe_variable: float) -> float:
    return (epex + turpe_variable * TURPE_REDUCTION_ELECTRO_INTENSIF + ACCISE_ELEC_EUR_MWH) / RENDEMENT_ELEC


def couts_horaires(ts: pd.Timestamp, epex: float, peg_pcs: float, rs_hausse: float = 0.0, rs_baisse: float = 0.0) -> dict:
    """{"elec": .., "gaz": ..} EUR/MWh thermique pour l'heure `ts`. aFRR
    deduite (hausse cote elec, baisse cote gaz) seulement si AVEC_RESERVE."""
    elec = cout_elec(epex, turpe_htb1(ts))
    gaz = cout_gaz(peg_pcs)
    if AVEC_RESERVE:
        elec -= rs_hausse
        gaz -= rs_baisse
    return {"elec": elec, "gaz": gaz}


def horaire_depuis_15min(points: list[dict], peg_par_jour) -> dict[str, dict]:
    """Points DA 15 min ({"ts","prix"}, heure locale naive) -> {"YYYY-MM-DDTHH":
    {"elec","gaz"}} sur la moyenne horaire. `peg_par_jour(jour_iso)` donne le
    PEG (EUR/MWh PCS) applicable a un jour, ou None (heure ignoree)."""
    if not points:
        return {}
    df = pd.DataFrame(points)
    df["ts"] = pd.to_datetime(df["ts"])
    moyennes = df.groupby(df["ts"].dt.floor("h"))["prix"].mean()
    resultat = {}
    for heure, epex in moyennes.items():
        peg = peg_par_jour(heure.date().isoformat())
        if peg is None:
            continue
        resultat[heure.strftime("%Y-%m-%dT%H")] = couts_horaires(heure, float(epex), peg)
    return resultat


def agreger(heures: list[dict]) -> dict:
    """Agregat d'une liste d'heures {"elec","gaz"} : arbitrage = min des deux
    (comme calcul_indice_now), donc nb_heures_elec + nb_heures_gaz = nb_heures.
    Indice NOW = cout gaz seul - cout arbitrage (>= 0)."""
    if not heures:
        return {"nb_heures": 0}
    n = len(heures)
    elec = sum(h["elec"] for h in heures) / n
    gaz = sum(h["gaz"] for h in heures) / n
    arbitrage = sum(min(h["elec"], h["gaz"]) for h in heures) / n
    nb_elec = sum(1 for h in heures if h["elec"] < h["gaz"])
    return {
        "nb_heures": n,
        "nb_heures_elec": nb_elec,
        "nb_heures_gaz": n - nb_elec,
        "cout_elec_eur_mwh": elec,
        "cout_gaz_eur_mwh": gaz,
        "cout_arbitrage_eur_mwh": arbitrage,
        "indice_now_eur_mwh": gaz - arbitrage,
    }


def ligne_mensuelle(annee_mois: str, heures: list[dict]) -> dict:
    """Meme colonnes que indice_now_mensuel_HTB1_LU.json (data/export)."""
    a = agreger(heures)
    return {
        "annee_mois": annee_mois,
        "cout_gaz_seul_eur_mwh": a["cout_gaz_eur_mwh"],
        "cout_elec_seul_eur_mwh": a["cout_elec_eur_mwh"],
        "cout_arbitrage_eur_mwh": a["cout_arbitrage_eur_mwh"],
        "part_heures_elec": a["nb_heures_elec"] / a["nb_heures"],
        "nb_heures": a["nb_heures"],
        "indice_now_eur_mwh": a["indice_now_eur_mwh"],
        "gain_total_eur_mois": a["indice_now_eur_mwh"] * a["nb_heures"],
    }
