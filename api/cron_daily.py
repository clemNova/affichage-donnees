"""Logique du fetch quotidien (day-ahead + FCR + capacite aFRR), appelee par
le handler unique (../app.py) sur GET /api/cron_daily -- day-ahead, capacite
FCR et capacite aFRR sont allouees/publiees A L'AVANCE (cf. echange
utilisateur sur le pipeline local) -- un seul fetch par jour suffit, inutile
de le faire toutes les 15 min.

`fetch_et_stocke_fenetre(debut, fin)` est le coeur reutilise par backfill.py :
constate qu'un seul appel ENTSO-E/RTE sur une fenetre large (35j) ne renvoie
en pratique que 2-3 jours de points (silencieusement tronque cote API, pas
d'erreur levee) -- backfill.py doit donc appeler cette fonction plusieurs
fois avec des fenetres COURTES qui se suivent, pas une seule fois avec une
fenetre large.
"""

from __future__ import annotations

import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd

from _lib import fetchers, store
from _lib.rte_client import charge_identifiants_rte


def _fenetre_mois_plus(n: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    """[1er jour du mois cible (aujourd'hui + n mois), 1er jour du mois
    suivant[ -- meme regle que labelMoisPlus() cote frontend (coge.html)."""
    debut = pd.Timestamp.now(tz="Europe/Paris").normalize().replace(day=1).tz_localize(None) + pd.DateOffset(months=n)
    return debut, debut + pd.DateOffset(months=1)


def _fenetre_trimestre_apres_m2() -> tuple[pd.Timestamp, pd.Timestamp]:
    """1er trimestre calendaire COMPLET apres le mois de M+2 (pas le
    trimestre courant + 1) -- meme regle que labelTrimestreApresM2() cote
    frontend, pour rester coherent avec le libelle affiche."""
    debut_m2, _ = _fenetre_mois_plus(2)
    trimestre_m2 = (debut_m2.month - 1) // 3
    trimestre, annee = trimestre_m2 + 1, debut_m2.year
    if trimestre > 3:
        trimestre, annee = 0, annee + 1
    debut = pd.Timestamp(year=annee, month=trimestre * 3 + 1, day=1)
    return debut, debut + pd.DateOffset(months=3)


def fetch_et_stocke_fenetre(debut: pd.Timestamp, fin: pd.Timestamp) -> dict:
    resultats: dict = {}

    for nom_domaine, fetch, avec_extras in [
        ("da", lambda: fetchers.fetch_day_ahead(debut, fin), True),
        ("fcr", lambda: fetchers.fetch_fcr_capacite(debut, fin), False),
    ]:
        try:
            points = fetch()
            n = store.maj_serie_connue_avance(nom_domaine, points, avec_indicateurs_da=avec_extras)
            resultats[nom_domaine] = n
        except Exception:
            resultats[nom_domaine] = f"echec: {traceback.format_exc(limit=2)}"

    try:
        identifiants = charge_identifiants_rte("BALANCING_CAPACITY")
        capa = fetchers.fetch_afrr_capacite(debut, fin, identifiants)
        n_up = store.maj_serie_connue_avance("afrr_up_capa", capa["UP"])
        n_down = store.maj_serie_connue_avance("afrr_down_capa", capa["DOWN"])
        resultats["afrr_capa"] = {"up": n_up, "down": n_down}
    except Exception:
        resultats["afrr_capa"] = f"echec: {traceback.format_exc(limit=2)}"

    try:
        identifiants = charge_identifiants_rte("BALANCING_CAPACITY")
        capa_mfrr = fetchers.fetch_mfrr_capacite(debut, fin, identifiants)
        n_up = store.maj_serie_connue_avance("mfrr_up_capa", capa_mfrr["UP"])
        n_down = store.maj_serie_connue_avance("mfrr_down_capa", capa_mfrr["DOWN"])
        resultats["mfrr_capa"] = {"up": n_up, "down": n_down}
    except Exception:
        resultats["mfrr_capa"] = f"echec: {traceback.format_exc(limit=2)}"

    try:
        # PEG gaz (NOOS) : un seul point par jour, pas de fenetre -- ecrit
        # directement dans hist:noos_peg (cf. store.maj_prix_journalier),
        # pas de raw:noos_peg puisqu'il n'y a pas de courbe 15 min a faire
        # vieillir comme pour da/fcr/afrr/mfrr.
        spot_peg = fetchers.fetch_peg_spot()
        if spot_peg:
            jour_iso = spot_peg[0]["ts"][:10]
            store.maj_prix_journalier("noos_peg", jour_iso, spot_peg[0]["prix"])
            resultats["noos_peg"] = 1
        else:
            resultats["noos_peg"] = 0
    except Exception:
        resultats["noos_peg"] = f"echec: {traceback.format_exc(limit=2)}"

    fenetres_forward = {
        "m1": _fenetre_mois_plus(1),
        "m2": _fenetre_mois_plus(2),
        "q1": _fenetre_trimestre_apres_m2(),
    }

    try:
        # Forward PEG (NOOS) : moyenne des points renvoyes sur chaque fenetre
        # M+1/M+2/1er trimestre apres M+2 -- pas d'historique, chaque
        # execution ecrase forward:noos_peg avec les valeurs courantes (les
        # fenetres glissent avec le calendrier, cf. store.maj_forward).
        valeurs_forward: dict = {}
        for periode, (debut_p, fin_p) in fenetres_forward.items():
            points = fetchers.fetch_peg_forward(debut_p, fin_p)
            prix = [p["prix"] for p in points if p.get("prix") is not None]
            if prix:
                valeurs_forward[periode] = sum(prix) / len(prix)
        if valeurs_forward:
            store.maj_forward("noos_peg", valeurs_forward)
        resultats["noos_peg_forward"] = valeurs_forward or "aucune donnee"
    except Exception:
        resultats["noos_peg_forward"] = f"echec: {traceback.format_exc(limit=2)}"

    try:
        # Forward elec France (NOOS PWRTE) : Base/Peak calcules cote client
        # sur chaque fenetre (cf. fetchers.fetch_elec_forward_base_peak),
        # memes fenetres M+1/M+2/1er trimestre apres M+2 que le gaz.
        valeurs_elec: dict = {}
        for periode, (debut_p, fin_p) in fenetres_forward.items():
            base_peak = fetchers.fetch_elec_forward_base_peak(debut_p, fin_p)
            if base_peak.get("base") is not None:
                valeurs_elec[periode] = base_peak
        if valeurs_elec:
            store.maj_forward("noos_elec", valeurs_elec)
        resultats["noos_elec_forward"] = valeurs_elec or "aucune donnee"
    except Exception:
        resultats["noos_elec_forward"] = f"echec: {traceback.format_exc(limit=2)}"

    return resultats


def executer(jours_avant: int = 1) -> dict:
    maintenant = pd.Timestamp.now(tz="Europe/Paris")
    debut = maintenant.normalize() - pd.Timedelta(days=jours_avant)
    fin = maintenant.normalize() + pd.Timedelta(days=2)
    return fetch_et_stocke_fenetre(debut, fin)
