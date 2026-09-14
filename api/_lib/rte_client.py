"""Client generique pour les API RTE Open Data (auth OAuth2 + appel REST).

Copie de rte_api_client.py (pipeline local) : la logique OAuth2/retry est
identique. Seule difference : les identifiants sont lus UNIQUEMENT depuis les
variables d'environnement de la fonction serverless (definies dans le projet
Vercel), pas depuis un fichier .env local -- il n'y en a pas dans ce contexte.
Meme convention de nommage RTE_<NOM_API>_CLIENT_ID / RTE_<NOM_API>_CLIENT_SECRET,
donc les memes valeurs que le .env local peuvent etre copiees telles quelles
dans les variables d'environnement Vercel.
"""

from __future__ import annotations

import base64
import os
import time
from dataclasses import dataclass
from typing import Any

import requests

RTE_TOKEN_URL = "https://digital.iservices.rte-france.com/token/oauth/"
MARGE_EXPIRATION_JETON_S = 60.0
TIMEOUT_REQUETE_S = 30.0


@dataclass(frozen=True)
class IdentifiantsRTE:
    client_id: str
    client_secret: str


class ErreurAPIRTE(RuntimeError):
    pass


class ErreurAuthentificationRTE(ErreurAPIRTE):
    pass


class ErreurLimiteAppelsRTE(ErreurAPIRTE):
    def __init__(self, message: str, retry_after_s: float | None):
        super().__init__(message)
        self.retry_after_s = retry_after_s


def charge_identifiants_rte(nom_api: str) -> IdentifiantsRTE:
    prefixe = f"RTE_{nom_api.upper()}"
    client_id = os.environ.get(f"{prefixe}_CLIENT_ID")
    client_secret = os.environ.get(f"{prefixe}_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError(
            f"Identifiants RTE manquants pour l'API '{nom_api}'. "
            f"Definir {prefixe}_CLIENT_ID et {prefixe}_CLIENT_SECRET dans les "
            f"variables d'environnement du projet Vercel."
        )
    return IdentifiantsRTE(client_id=client_id, client_secret=client_secret)


_cache_jetons: dict[str, tuple[str, float]] = {}


def obtiens_jeton_acces(identifiants: IdentifiantsRTE) -> str:
    jeton_existant = _cache_jetons.get(identifiants.client_id)
    if jeton_existant is not None:
        jeton, expiration = jeton_existant
        if time.time() < expiration - MARGE_EXPIRATION_JETON_S:
            return jeton

    couple = f"{identifiants.client_id}:{identifiants.client_secret}".encode("utf-8")
    entete_auth = base64.b64encode(couple).decode("ascii")
    reponse = requests.post(
        RTE_TOKEN_URL,
        headers={"Authorization": f"Basic {entete_auth}"},
        data={"grant_type": "client_credentials"},
        timeout=TIMEOUT_REQUETE_S,
    )
    if reponse.status_code == 401:
        raise ErreurAuthentificationRTE(
            "Authentification RTE refusee (401) : verifier client_id/client_secret pour cette API."
        )
    reponse.raise_for_status()
    contenu = reponse.json()
    jeton = contenu["access_token"]
    duree_s = float(contenu.get("expires_in", 7200))
    _cache_jetons[identifiants.client_id] = (jeton, time.time() + duree_s)
    return jeton


def _leve_erreur_rte(reponse: requests.Response) -> None:
    try:
        contenu = reponse.json()
    except ValueError:
        reponse.raise_for_status()
        return

    message = contenu.get("error_description") or contenu.get("error") or str(contenu)
    transaction_id = (contenu.get("error_details") or {}).get("transaction_id")
    if transaction_id:
        message = f"{message} (transaction_id={transaction_id})"

    if reponse.status_code == 429:
        retry_after = reponse.headers.get("Retry-After")
        raise ErreurLimiteAppelsRTE(
            f"Limite d'appels RTE atteinte (429) : {message}",
            retry_after_s=float(retry_after) if retry_after else None,
        )
    if reponse.status_code == 401:
        raise ErreurAuthentificationRTE(f"Authentification RTE refusee (401) : {message}")
    raise ErreurAPIRTE(f"Erreur API RTE (HTTP {reponse.status_code}) : {message}")


def appelle_api_rte(url_ressource: str, identifiants: IdentifiantsRTE, params: dict[str, Any] | None = None) -> dict:
    """GET authentifie sur une ressource RTE, avec un retry unique si le jeton a expire entre-temps."""
    jeton = obtiens_jeton_acces(identifiants)
    reponse = requests.get(url_ressource, headers={"Authorization": f"Bearer {jeton}"}, params=params, timeout=TIMEOUT_REQUETE_S)

    if reponse.status_code == 401:
        _cache_jetons.pop(identifiants.client_id, None)
        jeton = obtiens_jeton_acces(identifiants)
        reponse = requests.get(url_ressource, headers={"Authorization": f"Bearer {jeton}"}, params=params, timeout=TIMEOUT_REQUETE_S)

    if not reponse.ok:
        _leve_erreur_rte(reponse)

    return reponse.json()
