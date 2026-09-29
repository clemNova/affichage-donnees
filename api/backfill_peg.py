"""Backfill ponctuel de l'historique PEG gaz (NOOS) -- hist:noos_peg.

Endpoint SEPARE de /api/backfill_historique (RTE/ENTSO-E) : celui-ci
depasse deja le timeout Vercel Hobby (60s) a lui seul (cf. son propre
docstring) -- le PEG n'a pas besoin d'attendre derriere ce goulot.

Un appel PAR JOUR avec `published_at` (cf. fetchers.fetch_peg_historique_jour)
-- constate empiriquement qu'un appel unique sur toute la fenetre avec
seulement start_at/end_at (comme fetch_peg_forward) renvoie 0 point pour des
dates PASSEES : la courbe live NOOS ne conserve pas les livraisons passees
sans published_at. 7 jours suffisent pour la pill "vs 7j" (au lieu d'attendre
7 executions quotidiennes de cron_daily) ; jours par defaut pour rester
rapide (chaque appel est un HTTP GET separe, sequentiel).

Endpoint manuel, protege par CRON_SECRET comme les autres (cf. _lib/auth.py),
PAS ajoute au declencheur planifie GitHub Actions (.github/workflows/cron.yml)
-- a appeler une fois (ou apres un reset du KV), pas a chaque run.
"""

from __future__ import annotations

import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd

from _lib import fetchers, store


def executer(jours: int = 10) -> dict:
    aujourdhui = pd.Timestamp.now(tz="Europe/Paris").normalize().tz_localize(None)
    detail = []
    for i in range(1, jours + 1):
        jour = aujourdhui - pd.Timedelta(days=i)
        jour_iso = jour.date().isoformat()
        try:
            prix = fetchers.fetch_peg_historique_jour(jour)
            if prix is not None:
                store.maj_prix_journalier("noos_peg", jour_iso, prix)
                detail.append({"jour": jour_iso, "prix": prix})
            else:
                detail.append({"jour": jour_iso, "prix": None})
        except Exception:
            detail.append({"jour": jour_iso, "erreur": traceback.format_exc(limit=2)})
    return {"noos_peg_historique": {"n": sum(1 for d in detail if d.get("prix") is not None), "detail": detail}}
