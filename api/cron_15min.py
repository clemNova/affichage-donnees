"""Logique du top-up 15 min, appelee par le handler unique (../app.py) sur
GET /api/cron_15min : top-up UNIQUEMENT de l'activation aFRR (seule serie
reellement publiee au fil de l'eau) + calcul et sauvegarde du snapshot
complet de KPI (kpis:latest), lu par /api/kpis. Le day-ahead/FCR/aFRR-capacite
ne sont PAS re-fetches ici, cf. cron_daily.py.
"""

from __future__ import annotations

import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd

from _lib import fetchers, store
from _lib.rte_client import charge_identifiants_rte


def executer() -> dict:
    maintenant = pd.Timestamp.now(tz="Europe/Paris")
    debut = maintenant.normalize() - pd.Timedelta(hours=6)  # marge, l'appel reste < 24h (limite RTE)

    resultat: dict = {}
    try:
        identifiants = charge_identifiants_rte("BALANCING_ENERGY")
        points = fetchers.fetch_afrr_activation(debut, maintenant, identifiants)
        resultat["activation_top_up"] = store.maj_activation(points)
    except Exception:
        resultat["activation_top_up"] = f"echec, on continue avec le cache existant: {traceback.format_exc(limit=2)}"

    snapshot = store.calcule_et_sauvegarde_snapshot()
    resultat["snapshot"] = snapshot
    return resultat
