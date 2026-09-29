"""Client NOOS Energy (donnees de marche : courbe PEG gaz, courbe PWRTE elec
France).

PAS d'authentification cote NOOS (confirme aupres de l'utilisateur) : seul le
curve_uid identifie la courbe interrogee dans l'URL, pas de cle API/jeton a
envoyer -- contrairement a rte_client.py (OAuth2).

- PEG (gaz) : variable d'environnement NOOS_PEG_CURVE_UID obligatoire
  (Vercel -> Project Settings -> Environment Variables), ex. "NGPEG".
- PWRTE (elec France) : une seule courbe brute 15 min, PAS de produit
  Base/Peak natif cote NOOS -- calcules cote client a partir des points
  bruts (cf. fetchers.fetch_elec_forward_base_peak). "PWRTE" est l'
  identifiant donne par l'utilisateur ; NOOS_ELEC_CURVE_UID permet de le
  surcharger si besoin, sinon "PWRTE" par defaut.
"""

from __future__ import annotations

import os
from typing import Any

import requests

NOOS_BASE_URL = "https://api.noos.energy/v1/curves/PRICE/contracts"
TIMEOUT_REQUETE_S = 30.0
CURVE_UID_ELEC_DEFAUT = "PWRTE"


def _curve_uid_peg() -> str:
    curve_uid = os.environ.get("NOOS_PEG_CURVE_UID")
    if not curve_uid:
        raise RuntimeError(
            "Variable d'environnement NOOS_PEG_CURVE_UID manquante -- a definir "
            "dans Vercel -> Project Settings -> Environment Variables (identifiant "
            'de la courbe PEG cote NOOS, ex. "NGPEG").'
        )
    return curve_uid


def _appelle_noos(curve_uid: str, params: dict[str, Any] | None = None) -> dict:
    url = f"{NOOS_BASE_URL}/{curve_uid}"
    reponse = requests.get(url, params=params or {}, timeout=TIMEOUT_REQUETE_S)
    reponse.raise_for_status()
    return reponse.json()


def appelle_api_noos_peg(params: dict[str, Any] | None = None) -> dict:
    """GET sur la courbe PEG configuree. `params` sert a restreindre la
    fenetre (start_at/end_at, ISO 8601) pour une courbe forward."""
    return _appelle_noos(_curve_uid_peg(), params)


def appelle_api_noos_elec(params: dict[str, Any] | None = None) -> dict:
    """GET sur la courbe elec France (PWRTE, points 15 min bruts). `params`
    DOIT restreindre la fenetre (start_at/end_at) -- sans ca la courbe
    complete (~6 ans en 15 min) pese plusieurs Mo par appel."""
    curve_uid = os.environ.get("NOOS_ELEC_CURVE_UID") or CURVE_UID_ELEC_DEFAUT
    return _appelle_noos(curve_uid, params)
