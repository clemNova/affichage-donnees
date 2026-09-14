"""Endpoint appele par le cron Vercel quotidien (cf. vercel.json, ~12h-16h) :
day-ahead, capacite FCR et capacite aFRR sont allouees/publiees A L'AVANCE
(cf. echange utilisateur sur le pipeline local) -- un seul fetch par jour
suffit, inutile de le faire toutes les 15 min.

Lancement manuel (test) : GET /api/cron_daily
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd

from _lib import fetchers, store
from _lib.auth import autorise
from _lib.rte_client import charge_identifiants_rte


def executer() -> dict:
    maintenant = pd.Timestamp.now(tz="Europe/Paris")
    debut = maintenant.normalize() - pd.Timedelta(days=1)
    fin = maintenant.normalize() + pd.Timedelta(days=2)

    resultats: dict = {}

    for nom_domaine, fetch, avec_extras in [
        ("da", lambda: fetchers.fetch_day_ahead(debut, fin), True),
        ("fcr", lambda: fetchers.fetch_fcr_capacite(debut, fin), False),
    ]:
        try:
            points = fetch()
            n = store.maj_serie_connue_avance(nom_domaine, points, avec_tb2_bas_peak=avec_extras)
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

    return resultats


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not autorise(self.headers):
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b"unauthorized")
            return
        resultats = executer()
        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(resultats, default=str).encode())
