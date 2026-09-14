"""Client minimal pour un store Redis compatible REST (Vercel KV / Upstash
Redis) -- une seule fonction HTTP par commande, aucune dependance client Redis
native (`requests` suffit, plus simple a deployer en fonction serverless).

Accepte indifferemment les variables d'environnement que Vercel KV
(`KV_REST_API_URL`/`KV_REST_API_TOKEN`, injectees automatiquement quand un
store KV est lie au projet) ou un compte Upstash autonome
(`UPSTASH_REDIS_REST_URL`/`UPSTASH_REDIS_REST_TOKEN`) definissent -- les deux
pointent vers la meme API REST Upstash.

Protocole : POST vers l'URL de base avec un tableau JSON `[commande, arg1, ...]`
(cf. doc Upstash REST API), pas de construction d'URL avec la valeur dans le
chemin -- evite tout probleme d'encodage sur des valeurs JSON arbitraires.
"""

from __future__ import annotations

import json
import os
from typing import Any

import requests

TIMEOUT_S = 10.0


def _base_url() -> str:
    url = os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL")
    if not url:
        raise RuntimeError(
            "Aucun store KV configure : definir KV_REST_API_URL/KV_REST_API_TOKEN "
            "(store Vercel KV lie au projet) ou UPSTASH_REDIS_REST_URL/UPSTASH_REDIS_REST_TOKEN "
            "(compte Upstash autonome) dans les variables d'environnement."
        )
    return url


def _token() -> str:
    token = os.environ.get("KV_REST_API_TOKEN") or os.environ.get("UPSTASH_REDIS_REST_TOKEN")
    if not token:
        raise RuntimeError("Token KV manquant (KV_REST_API_TOKEN ou UPSTASH_REDIS_REST_TOKEN).")
    return token


def _commande(*parts: Any) -> Any:
    reponse = requests.post(
        _base_url(),
        headers={"Authorization": f"Bearer {_token()}"},
        json=list(parts),
        timeout=TIMEOUT_S,
    )
    reponse.raise_for_status()
    corps = reponse.json()
    if corps.get("error"):
        raise RuntimeError(f"Erreur KV : {corps['error']}")
    return corps.get("result")


def get_json(cle: str, defaut: Any = None) -> Any:
    brut = _commande("GET", cle)
    if brut is None:
        return defaut
    return json.loads(brut)


def set_json(cle: str, valeur: Any) -> None:
    _commande("SET", cle, json.dumps(valeur))
