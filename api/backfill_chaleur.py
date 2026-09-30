"""Backfill ponctuel de hist_chaleur:horaire (couts horaires elec/gaz) depuis
le day-ahead ENTSO-E des HORAIRE_JOURS derniers jours -- rattrape le trou
entre le CSV d'export (data/export, s'arrete au 27/08/2026) et le premier
cron_daily, pour que les 30j glissants soient complets.

N'utilise PAS store.maj_serie_connue_avance (ecraserait raw:da, cf.
backfill.py) : les points DA sont convertis directement en couts horaires.
Le PEG : jusqu'a la fin du CSV d'export (27/08/2026) il vient du CSV ; APRES,
cet endpoint le recupere lui-meme sur NOOS, un appel par jour avec
published_at (fetchers.fetch_peg_historique_jour, en parallele) et l'ecrit
dans hist:noos_peg -- inutile d'appeler /api/backfill_peg avant. Un jour sans
PEG connu (NOOS ne renvoie rien, week-end...) prend la valeur du dernier jour
connu (chaleur_store.peg_par_jour_depuis_hist).

L'aFRR capacite (services systeme, RTE) est recuperee dans les memes fenetres.
Fenetres de 5 jours (troncature silencieuse d'ENTSO-E sur les fenetres larges,
cf. backfill.py), recuperees en parallele (timeout Vercel Hobby 60s).

Endpoint manuel protege par CRON_SECRET, pas dans le declencheur planifie.
"""

from __future__ import annotations

import os
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd

from _lib import chaleur_store, fetchers, kv
from _lib.rte_client import charge_identifiants_rte

CHUNK_JOURS = 5


def _fetch(debut: pd.Timestamp, fin: pd.Timestamp, identifiants) -> dict:
    """DA ENTSO-E + aFRR capacite RTE (services systeme) d'une fenetre."""
    etiquette = f"{debut.date()}..{fin.date()}"
    try:
        resultat = {"fenetre": etiquette, "points": fetchers.fetch_day_ahead(debut, fin), "up": [], "down": []}
    except Exception:
        return {"fenetre": etiquette, "erreur": traceback.format_exc(limit=2)}
    try:
        capa = fetchers.fetch_afrr_capacite(debut, fin, identifiants)
        resultat["up"], resultat["down"] = capa["UP"], capa["DOWN"]
    except Exception:
        resultat["erreur_afrr"] = traceback.format_exc(limit=2)
    return resultat


def _fetch_peg(jour: pd.Timestamp) -> tuple[str, float | None, str | None]:
    jour_iso = jour.date().isoformat()
    try:
        return jour_iso, fetchers.fetch_peg_historique_jour(jour), None
    except Exception:
        return jour_iso, None, traceback.format_exc(limit=2)


def _backfill_peg(fin: pd.Timestamp) -> dict:
    """PEG NOOS des jours posterieurs au CSV et absents de hist:noos_peg,
    jusqu'a hier (aujourd'hui : cron_daily via fetch_peg_spot)."""
    deja = kv.get_json("hist:noos_peg", {})
    premier = pd.Timestamp(chaleur_store.dernier_jour_peg_csv()) + pd.Timedelta(days=1)
    jours = [j for j in pd.date_range(premier, fin - pd.Timedelta(days=1)) if j.date().isoformat() not in deja]
    with ThreadPoolExecutor(max_workers=8) as executeur:
        resultats = list(executeur.map(_fetch_peg, jours))
    trouves = {j: p for j, p, _ in resultats if p is not None}
    if trouves:
        chaleur_store.maj_peg_jours(trouves)
    return {
        "jours_demandes": len(jours),
        "jours_trouves": len(trouves),
        "sans_prix": [j for j, p, e in resultats if p is None and e is None],
        "erreurs": [{"jour": j, "erreur": e} for j, _, e in resultats if e],
    }


def executer() -> dict:
    fin_globale = pd.Timestamp.now(tz="Europe/Paris").normalize() + pd.Timedelta(days=1)
    debut_globale = fin_globale - pd.Timedelta(days=chaleur_store.HORAIRE_JOURS)
    detail_peg = _backfill_peg(pd.Timestamp.now(tz="Europe/Paris").normalize().tz_localize(None))
    fenetres = []
    curseur = debut_globale
    while curseur < fin_globale:
        fenetres.append((curseur, min(curseur + pd.Timedelta(days=CHUNK_JOURS), fin_globale)))
        curseur = fenetres[-1][1]

    try:
        identifiants = charge_identifiants_rte("BALANCING_CAPACITY")
    except Exception:
        identifiants = None  # aFRR : erreur reportee par fenetre, heures nettes = brutes

    with ThreadPoolExecutor(max_workers=6) as executeur:
        resultats = list(executeur.map(lambda f: _fetch(*f, identifiants), fenetres))

    def cumul(cle):
        return [p for r in resultats for p in r.get(cle, [])]

    return {
        "peg": detail_peg,
        "heures_ecrites": chaleur_store.maj_horaire(cumul("points"), cumul("up"), cumul("down")),
        "detail_par_chunk": [
            {
                "fenetre": r["fenetre"],
                **({"n_da": len(r["points"]), "n_afrr_up": len(r["up"]), "n_afrr_down": len(r["down"])} if "points" in r else {}),
                **{k: r[k] for k in ("erreur", "erreur_afrr") if k in r},
            }
            for r in resultats
        ],
    }
