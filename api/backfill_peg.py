"""Backfill ponctuel de l'historique PEG gaz (NOOS) -- hist:noos_peg.

Endpoint SEPARE de /api/backfill_historique (RTE/ENTSO-E) : celui-ci
depasse deja le timeout Vercel Hobby (60s) a lui seul (cf. son propre
docstring) -- le PEG n'a pas besoin d'attendre derriere ce goulot. NOOS
n'expose qu'UNE valeur par jour (pas de courbe 15 min), et fetch_peg_forward
(meme endpoint que le spot/forward, juste une fenetre PASSEE ici) ne
tronque pas sur une fenetre de quelques semaines -- un seul appel suffit,
pas besoin de chunker en fenetres courtes comme pour RTE/ENTSO-E.

Permet d'avoir tout de suite une vraie pill "vs 7j" sur le spot Peg au lieu
d'attendre 7 executions quotidiennes de cron_daily pour accumuler
l'historique -- cf. echange utilisateur.

Endpoint manuel, protege par CRON_SECRET comme les autres (cf. _lib/auth.py),
PAS ajoute au declencheur planifie GitHub Actions (.github/workflows/cron.yml)
-- a appeler une fois (ou apres un reset du KV), pas a chaque run.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd

from _lib import fetchers, store
from _lib.store import PRUNE_JOURS


def executer(jours: int = PRUNE_JOURS) -> dict:
    fin = pd.Timestamp.now(tz="Europe/Paris").normalize().tz_localize(None)
    debut = fin - pd.Timedelta(days=jours)
    points = fetchers.fetch_peg_forward(debut, fin)
    for point in points:
        store.maj_prix_journalier("noos_peg", point["ts"][:10], point["prix"])
    return {"noos_peg": {"n": len(points), "fenetre": f"{debut.date()}..{fin.date()}"}}
