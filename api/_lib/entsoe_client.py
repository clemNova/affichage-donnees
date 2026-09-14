"""Client partage pour les donnees ENTSO-E Transparency Platform (bibliotheque
`entsoe-py`). Copie de entsoe_client.py (pipeline local) : cle API PUBLIQUE
ENTSO-E par defaut (pas un secret), surchargeable via ENTSOE_API_KEY."""

from __future__ import annotations

import os

from entsoe import EntsoePandasClient

CLE_API_PUBLIQUE_DEFAUT = "37b37e80-260c-4164-9bb5-3d6d3e53d95b"
PAYS_FRANCE = "FR"


def cree_client_entsoe() -> EntsoePandasClient:
    cle = os.environ.get("ENTSOE_API_KEY", CLE_API_PUBLIQUE_DEFAUT)
    return EntsoePandasClient(api_key=cle)
