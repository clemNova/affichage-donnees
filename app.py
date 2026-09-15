"""Point d'entree Python UNIQUE impose par le runtime Python de Vercel (le
mode "un fichier api/*.py = une fonction independante" n'est plus disponible
en pratique, meme avec vercel.json configure -- erreur systematique "No
python entrypoint found") : ce handler route lui-meme, selon le chemin
appele, vers la logique de kpis / cron_daily / cron_15min (inchangee, dans
api/). Declare a Vercel via pyproject.toml (tool.vercel.entrypoint).
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "api"))

import cron_15min
import cron_daily
from _lib import kv
from _lib.auth import autorise


def _repondre(handler_http: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    handler_http.send_response(status)
    handler_http.send_header("Content-type", "application/json")
    if status == 200:
        handler_http.send_header("Cache-Control", "no-store")
    handler_http.send_header("Access-Control-Allow-Origin", "*")
    handler_http.end_headers()
    handler_http.wfile.write(json.dumps(payload, default=str).encode())


def _refuser(handler_http: BaseHTTPRequestHandler) -> None:
    handler_http.send_response(401)
    handler_http.end_headers()
    handler_http.wfile.write(b"unauthorized")


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        chemin = self.path.split("?", 1)[0].rstrip("/")

        if chemin == "/api/kpis":
            try:
                snapshot = kv.get_json("kpis:latest", {})
                _repondre(self, 200, snapshot)
            except Exception as erreur:
                _repondre(self, 500, {"erreur": str(erreur)})
            return

        if chemin in ("/api/cron_daily", "/api/cron_15min"):
            if not autorise(self.headers):
                _refuser(self)
                return
            module = cron_daily if chemin == "/api/cron_daily" else cron_15min
            try:
                _repondre(self, 200, module.executer())
            except Exception:
                _repondre(self, 500, {"erreur": traceback.format_exc(limit=2)})
            return

        self.send_response(404)
        self.end_headers()
