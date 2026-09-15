"""Fonction Vercel /api/kpis -- lecture seule et publique (pas de secret, pas
de quota RTE/ENTSO-E consomme) : renvoie le dernier snapshot calcule par
cron_15min.py et stocke sous la cle KV "kpis:latest". Fichier autonome, un
fichier api/*.py = une fonction Vercel (cf. README, section Architecture).
"""

from __future__ import annotations

import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _lib import kv
from _lib.http_utils import repondre


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        try:
            snapshot = kv.get_json("kpis:latest", {})
            repondre(self, 200, snapshot)
        except Exception as erreur:
            repondre(self, 500, {"erreur": str(erreur)})
