"""Backfill ponctuel de l'historique (hist:da, hist:fcr, hist:afrr_up_capa,
hist:afrr_down_capa) directement depuis les API RTE/ENTSO-E, qui exposent
aussi les prix PASSES -- evite d'attendre 35 jours que cron_daily les
accumule naturellement jour par jour.

Un seul appel ENTSO-E/RTE sur une fenetre large (35j) ne renvoie EN PRATIQUE
que 2-3 jours de points (troncature silencieuse cote API, pas d'erreur
levee -- constate : une fenetre de 35j n'a renvoye que 192 points day-ahead,
soit 2 jours) : ce fichier appelle donc cron_daily.fetch_et_stocke_fenetre()
plusieurs fois de suite avec des fenetres COURTES (CHUNK_JOURS) qui se
suivent chronologiquement, plutot qu'une fois avec une fenetre large.
store.maj_serie_connue_avance archive deja toute journee complete d'un
chunk dans hist:<domaine> des l'appel du chunk SUIVANT (elle devient
"l'ancien raw:" a ce moment-la) -- l'ordre chronologique (du plus ancien au
plus recent) est donc important.

Endpoint manuel, protege par CRON_SECRET comme les autres cron (cf.
_lib/auth.py), mais PAS ajoute au declencheur planifie GitHub Actions
(.github/workflows/cron.yml) -- a appeler une fois (ou apres un reset du
KV), pas a chaque run.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd

import cron_daily

from _lib.store import PRUNE_JOURS

JOURS_BACKFILL = PRUNE_JOURS  # 35, la fenetre max conservee dans hist:<domaine>
CHUNK_JOURS = 5  # taille de fenetre que l'API renvoie fidelement en un appel


def executer() -> dict:
    maintenant = pd.Timestamp.now(tz="Europe/Paris")
    fin_globale = maintenant.normalize()  # exclut aujourd'hui, journee incomplete
    debut_globale = fin_globale - pd.Timedelta(days=JOURS_BACKFILL)

    cumul = {"da": 0, "fcr": 0, "afrr_capa": {"up": 0, "down": 0}}
    erreurs: list[str] = []
    detail_par_chunk: list[dict] = []  # visibilite par fenetre, utile si une seule renvoie peu de points

    curseur = debut_globale
    while curseur < fin_globale:
        chunk_fin = min(curseur + pd.Timedelta(days=CHUNK_JOURS), fin_globale)
        resultat_chunk = cron_daily.fetch_et_stocke_fenetre(curseur, chunk_fin)
        etiquette = f"{curseur.date()}..{chunk_fin.date()}"
        detail_par_chunk.append({"fenetre": etiquette, **resultat_chunk})

        for cle in ("da", "fcr"):
            valeur = resultat_chunk.get(cle)
            if isinstance(valeur, int):
                cumul[cle] += valeur
            else:
                erreurs.append(f"{cle} [{etiquette}]: {valeur}")

        capa = resultat_chunk.get("afrr_capa")
        if isinstance(capa, dict):
            cumul["afrr_capa"]["up"] += capa.get("up", 0)
            cumul["afrr_capa"]["down"] += capa.get("down", 0)
        else:
            erreurs.append(f"afrr_capa [{etiquette}]: {capa}")

        curseur = chunk_fin

    cumul["detail_par_chunk"] = detail_par_chunk
    if erreurs:
        cumul["erreurs"] = erreurs
    return cumul
