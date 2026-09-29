"""Client NOOS Energy (donnees de marche gaz, courbe PEG).

PAS d'authentification cote NOOS (confirme aupres de l'utilisateur) : seul le
curve_uid identifie la courbe interrogee dans l'URL, pas de cle API/jeton a
envoyer -- contrairement a rte_client.py (OAuth2). Seule variable
d'environnement necessaire : NOOS_PEG_CURVE_UID (Vercel -> Project Settings ->
Environment Variables), ex. "NGPEG".
"""

from __future__ import annotations

import os
from typing import Any

import requests

NOOS_BASE_URL = "https://api.noos.energy/v1/curves/PRICE/contracts"
TIMEOUT_REQUETE_S = 30.0


def _curve_uid_peg() -> str:
    curve_uid = os.environ.get("NOOS_PEG_CURVE_UID")
    if not curve_uid:
        raise RuntimeError(
            "Variable d'environnement NOOS_PEG_CURVE_UID manquante -- a definir "
            "dans Vercel -> Project Settings -> Environment Variables (identifiant "
            'de la courbe PEG cote NOOS, ex. "NGPEG").'
        )
    return curve_uid


def appelle_api_noos_peg(params: dict[str, Any] | None = None) -> dict:
    """GET (sans authentification) sur la courbe PEG configuree. `params` sert
    a restreindre la fenetre (start_at/end_at, ISO 8601) pour une courbe forward."""
    url = f"{NOOS_BASE_URL}/{_curve_uid_peg()}"
    reponse = requests.get(url, params=params or {}, timeout=TIMEOUT_REQUETE_S)
    reponse.raise_for_status()
    return reponse.json()
