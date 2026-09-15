"""Helpers de reponse HTTP partages par les fonctions Python de /api (chacune
definit sa propre classe `handler(BaseHTTPRequestHandler)`, cf. doc Vercel
"Python Functions in the /api Directory") -- evite de dupliquer le JSON/CORS
et le refus 401 dans kpis.py / cron_daily.py / cron_15min.py.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler
from typing import Any


def repondre(handler_http: BaseHTTPRequestHandler, status: int, payload: Any) -> None:
    handler_http.send_response(status)
    handler_http.send_header("Content-type", "application/json")
    if status == 200:
        handler_http.send_header("Cache-Control", "no-store")
    handler_http.send_header("Access-Control-Allow-Origin", "*")
    handler_http.end_headers()
    handler_http.wfile.write(json.dumps(payload, default=str).encode())


def refuser(handler_http: BaseHTTPRequestHandler) -> None:
    handler_http.send_response(401)
    handler_http.end_headers()
    handler_http.wfile.write(b"unauthorized")
