"""Endpoint public lu par la page TV (public/index.html) : renvoie tel quel
le dernier snapshot de KPI (kpis:latest), ecrit par cron_15min.py -- aucun
calcul au moment de la requete, reponse quasi instantanee. Pas d'auth : ne
contient que des prix derives, aucun identifiant/secret.
"""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _lib import kv


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            snapshot = kv.get_json("kpis:latest", {})
            self.send_response(200)
        except Exception as erreur:
            snapshot = {"erreur": str(erreur)}
            self.send_response(500)
        self.send_header("Content-type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(snapshot, default=str).encode())
