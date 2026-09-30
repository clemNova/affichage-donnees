"""Orchestration KV du dashboard Chaudieres electriques (cf. chaleur.py pour
les calculs).

Cles :
- `hist_chaleur:horaire` : {"YYYY-MM-DDTHH": {"elec","gaz","elec_net","gaz_net"}}
  EUR/MWh thermique (bruts pour les courbes, nets de reserve aFRR pour l'indice),
  borne a HORAIRE_JOURS -- alimente par cron_daily depuis raw:da (moyenne
  horaire) et le PEG de hist:noos_peg, ou par backfill_chaleur.
- `hist_chaleur:indice_now` : {"YYYY-MM": ligne mensuelle}, PERMANENT --
  seede depuis data/export (importer_indice_now), complete chaque mois clos.
- `chaleur:latest` : snapshot lu tel quel par /api/chaleur.
"""

from __future__ import annotations

import os
from collections.abc import Callable

import pandas as pd

from . import chaleur, kv, store

HORAIRE_JOURS = 45  # 30j glissants + un mois clos a archiver + marge
FENETRE_HEURES = 720  # 30 jours glissants
SEUIL_MOIS_COMPLET = 0.95  # part d'heures minimale pour archiver un mois clos

CHEMIN_CSV_PEG = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "export", "donnees_EPEX_PEG_CO2_RTE_2021-2026_horaire.csv",
)
_peg_csv: dict[str, float] | None = None

CLE_HORAIRE = "hist_chaleur:horaire"
CLE_INDICE = "hist_chaleur:indice_now"


def _maintenant() -> pd.Timestamp:
    return pd.Timestamp.now(tz="Europe/Paris").tz_localize(None)


def _peg_du_csv() -> dict[str, float]:
    """PEG moyen par jour (heure de Paris) du CSV d'export -- historique plus
    long que hist:noos_peg (35 j max, purge). Charge une fois par process."""
    global _peg_csv
    if _peg_csv is None:
        df = pd.read_csv(CHEMIN_CSV_PEG, usecols=["datetime_paris", "peg_eur_mwh"]).dropna()
        jour = pd.to_datetime(df["datetime_paris"], utc=True).dt.tz_convert("Europe/Paris").dt.strftime("%Y-%m-%d")
        _peg_csv = df.groupby(jour)["peg_eur_mwh"].mean().to_dict()
    return _peg_csv


def peg_par_jour_depuis_hist() -> Callable[[str], float | None]:
    """Renvoie f(jour_iso) -> PEG du jour. Sources : hist:noos_peg (NOOS, prime
    quand il existe) completee par le CSV d'export pour les jours plus anciens.
    Un jour sans PEG connu prend le dernier jour connu avant (jours futurs,
    week-ends, trous) ; avant le tout premier jour connu, la premiere valeur.
    None seulement si aucune source n'est disponible."""
    connus = dict(_peg_du_csv()) if os.path.exists(CHEMIN_CSV_PEG) else {}
    connus.update({j: v["moyenne"] for j, v in kv.get_json("hist:noos_peg", {}).items()})
    jours = sorted(connus)

    def peg(jour_iso: str) -> float | None:
        precedents = [j for j in jours if j <= jour_iso]
        if precedents:
            return connus[precedents[-1]]
        return connus[jours[0]] if jours else None

    return peg


def dernier_jour_peg_csv() -> str:
    return max(_peg_du_csv())


def maj_peg_jours(prix_par_jour: dict[str, float]) -> None:
    """Ecrit plusieurs jours de PEG dans hist:noos_peg en UNE lecture/ecriture
    (store.maj_prix_journalier le ferait jour par jour, non sur pour des
    fetch en parallele)."""
    hist = kv.get_json("hist:noos_peg", {})
    for jour_iso, prix in prix_par_jour.items():
        hist[jour_iso] = {"moyenne": float(prix)}
    kv.set_json("hist:noos_peg", store._prune_hist(hist))


def _prune_horaire(horaire: dict) -> dict:
    limite = (_maintenant().normalize() - pd.Timedelta(days=HORAIRE_JOURS)).strftime("%Y-%m-%dT%H")
    return {k: v for k, v in horaire.items() if k >= limite}


def maj_horaire(points_da_15min: list[dict], rs_hausse: list[dict] | None = None, rs_baisse: list[dict] | None = None) -> int:
    """Calcule les couts horaires depuis des points DA 15 min (+ prix aFRR
    capacite hausse/baisse pour les champs nets) et les fusionne dans
    hist_chaleur:horaire (ecrase les heures deja connues). Renvoie le nombre
    d'heures ecrites."""
    nouvelles = chaleur.horaire_depuis_15min(points_da_15min, peg_par_jour_depuis_hist(), rs_hausse, rs_baisse)
    horaire = kv.get_json(CLE_HORAIRE, {})
    horaire.update(nouvelles)
    kv.set_json(CLE_HORAIRE, _prune_horaire(horaire))
    return len(nouvelles)


def maj_mois_clos() -> str | None:
    """Archive dans hist_chaleur:indice_now le mois calendaire precedent s'il
    est clos, absent, et couvert par assez d'heures. Idempotent, appele a
    chaque cron_daily -- ne fait quelque chose qu'une fois par mois.
    Renvoie le mois archive, ou None."""
    mois = (_maintenant().normalize().replace(day=1) - pd.Timedelta(days=1)).strftime("%Y-%m")
    indice = kv.get_json(CLE_INDICE, {})
    if mois in indice:
        return None
    heures = [v for k, v in kv.get_json(CLE_HORAIRE, {}).items() if k.startswith(mois)]
    if len(heures) < SEUIL_MOIS_COMPLET * pd.Period(mois).days_in_month * 24:
        return None
    indice[mois] = chaleur.ligne_mensuelle(mois, heures)
    kv.set_json(CLE_INDICE, indice)
    return mois


def calcule_et_sauvegarde_snapshot() -> dict:
    maintenant = _maintenant()
    cle_maintenant = maintenant.strftime("%Y-%m-%dT%H")
    jour_iso = maintenant.date().isoformat()
    horaire = kv.get_json(CLE_HORAIRE, {})

    jour = [
        {"h": int(k[11:13]), **v} for k, v in sorted(horaire.items()) if k.startswith(jour_iso)
    ]
    fenetre = [v for k, v in sorted(horaire.items()) if k <= cle_maintenant][-FENETRE_HEURES:]
    indice = kv.get_json(CLE_INDICE, {})

    snapshot = {
        "fetched_at": maintenant.isoformat(),
        "jour": jour,
        "maintenant": horaire.get(cle_maintenant),
        "glissant_30j": chaleur.agreger(fenetre),
        "indice_now_mensuel": [indice[m] for m in sorted(indice)],
        "co2_eur_t": chaleur.CO2_EUR_T,
    }
    kv.set_json("chaleur:latest", snapshot)
    return snapshot
