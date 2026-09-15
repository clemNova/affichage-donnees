"""Backfill ponctuel de l'historique (hist:da, hist:fcr, hist:afrr_up_capa,
hist:afrr_down_capa) directement depuis les API RTE/ENTSO-E, qui exposent
aussi les prix PASSES -- evite d'attendre 35 jours que cron_daily les
accumule naturellement jour par jour. Reutilise cron_daily.executer() avec
une fenetre bien plus large (store.maj_serie_connue_avance range deja
n'importe quelle journee complete du payload dans hist:<domaine>, pas besoin
d'une logique separee).

Endpoint manuel, protege par CRON_SECRET comme les autres cron (cf.
_lib/auth.py), mais PAS ajoute au declencheur planifie GitHub Actions
(.github/workflows/cron.yml) -- a appeler une fois (ou apres un reset du
KV), pas a chaque run. Si l'API RTE/ENTSO-E refuse une fenetre aussi large
en un seul appel, reduire JOURS_BACKFILL et appeler plusieurs fois avec des
fenetres qui se chevauchent (idempotent, sans risque).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cron_daily

from _lib.store import PRUNE_JOURS

JOURS_BACKFILL = PRUNE_JOURS  # 35, la fenetre max conservee dans hist:<domaine>


def executer() -> dict:
    return cron_daily.executer(jours_avant=JOURS_BACKFILL)
