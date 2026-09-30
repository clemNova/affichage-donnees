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

DECOUPE EN TRANCHES : un seul appel sur tout l'historique risquerait de depasser
le timeout Vercel Hobby (60 s). Chaque appel traite `jours` jours (12 par
defaut) a partir de `debut` (par defaut : le lendemain de la fin du CSV
d'export, borne a HORAIRE_JOURS en arriere) et renvoie `prochain_appel` :
GET /api/backfill_chaleur?debut=YYYY-MM-DD[&jours=N], a rappeler jusqu'a ce
qu'il vaille null. Idempotent : refaire une tranche ecrase les memes heures.

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


def _backfill_peg(debut: pd.Timestamp, fin: pd.Timestamp) -> dict:
    """PEG NOOS des jours de [debut, fin[ posterieurs au CSV et absents de
    hist:noos_peg, hors aujourd'hui (cron_daily via fetch_peg_spot). `debut`
    et `fin` naifs."""
    deja = kv.get_json("hist:noos_peg", {})
    premier = max(debut, pd.Timestamp(chaleur_store.dernier_jour_peg_csv()) + pd.Timedelta(days=1))
    aujourdhui = pd.Timestamp.now(tz="Europe/Paris").normalize().tz_localize(None)
    jours = [j for j in pd.date_range(premier, min(fin, aujourdhui) - pd.Timedelta(days=1)) if j.date().isoformat() not in deja]
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


JOURS_PAR_APPEL = 12

# app.py passe la query string (dict) aux modules qui declarent ce drapeau.
ACCEPTE_PARAMS = True


def executer(params: dict | None = None) -> dict:
    params = params or {}
    demain = pd.Timestamp.now(tz="Europe/Paris").normalize() + pd.Timedelta(days=1)
    plancher = demain - pd.Timedelta(days=chaleur_store.HORAIRE_JOURS)
    apres_csv = pd.Timestamp(chaleur_store.dernier_jour_peg_csv(), tz="Europe/Paris") + pd.Timedelta(days=1)
    if params.get("debut"):
        debut_globale = pd.Timestamp(params["debut"], tz="Europe/Paris")
    else:
        debut_globale = max(apres_csv, plancher)
    jours = int(params.get("jours") or JOURS_PAR_APPEL)
    fin_globale = min(debut_globale + pd.Timedelta(days=jours), demain)

    detail_peg = _backfill_peg(debut_globale.tz_localize(None), fin_globale.tz_localize(None))
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

    prochain = None
    if fin_globale < demain:
        prochain = f"/api/backfill_chaleur?debut={fin_globale.date().isoformat()}" + (f"&jours={jours}" if params.get("jours") else "")
    return {
        "tranche": f"{debut_globale.date()}..{fin_globale.date()}",
        "prochain_appel": prochain,
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
