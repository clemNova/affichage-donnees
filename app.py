"""Point d'entree Python de ce projet Vercel, declare explicitement via
pyproject.toml (tool.vercel.entrypoint = "app:handler").

Pourquoi un entrypoint unique et pas une fonction par fichier api/*.py :
sur ce projet, le deploiement avec 3 fichiers api/*.py definissant chacun
leur propre `class handler(BaseHTTPRequestHandler)` echoue au build avec
l'erreur Vercel "No python entrypoint found in default locations, but found
potential entrypoints: api/cron_15min.py (variable: handler), api/cron_daily.py
(variable: handler), api/kpis.py (variable: handler)" -- Vercel a trouve 3
candidats et refuse de choisir. La doc Vercel decrit un mode "fonctions par
fichier dans /api" sans entrypoint requis, mais ce mode ne s'est pas active
ici (probablement reserve aux projets deja existants dans cet etat cote
plateforme) -- l'erreur reelle du build fait foi. Ce fichier route donc
lui-meme, selon le chemin appele, vers la logique de kpis / cron_daily /
cron_15min (imports directs, pas de HTTP interne).
"""

from __future__ import annotations

import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "api"))

import backfill
import cron_15min
import cron_daily
import importer_historique_mensuel
from _lib import kv
from _lib.auth import autorise
from _lib.http_utils import refuser, repondre

# Endpoints proteges par CRON_SECRET -- declenchent de vrais appels RTE/
# ENTSO-E (quota limite) ou des ecritures KV ponctuelles. Ni
# /api/backfill_historique ni /api/importer_historique_mensuel ne sont
# ajoutes au declencheur planifie GitHub Actions (cf.
# .github/workflows/cron.yml) : ce sont des operations ponctuelles (remplir
# hist:<domaine>/hist_mensuel:<domaine> d'un coup), a appeler manuellement.
MODULES_PROTEGES = {
    "/api/cron_daily": cron_daily,
    "/api/cron_15min": cron_15min,
    "/api/backfill_historique": backfill,
    "/api/importer_historique_mensuel": importer_historique_mensuel,
}


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        chemin = self.path.split("?", 1)[0].rstrip("/")

        if chemin == "/api/kpis":
            try:
                snapshot = kv.get_json("kpis:latest", {})
                repondre(self, 200, snapshot)
            except Exception as erreur:
                repondre(self, 500, {"erreur": str(erreur)})
            return

        if chemin in MODULES_PROTEGES:
            if not autorise(self.headers):
                refuser(self)
                return
            try:
                repondre(self, 200, MODULES_PROTEGES[chemin].executer())
            except Exception:
                repondre(self, 500, {"erreur": traceback.format_exc(limit=2)})
            return

        self.send_response(404)
        self.end_headers()
