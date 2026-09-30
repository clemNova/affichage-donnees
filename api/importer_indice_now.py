"""Import ponctuel de l'historique mensuel de l'Indice NOW (HTB1/LU, RTE) depuis
data/export/indice_now_mensuel_HTB1_LU.json vers hist_chaleur:indice_now
(stockage permanent, cf. _lib/chaleur_store.py). Les mois suivants sont
ajoutes par cron_daily (chaleur_store.maj_mois_clos) a la cloture de chaque
mois.

Endpoint manuel protege par CRON_SECRET, pas dans le declencheur planifie --
a appeler une fois (ou apres mise a jour du fichier).
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _lib import kv
from _lib import chaleur_store
from _lib.chaleur_store import CLE_INDICE

CHEMIN_JSON = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "export", "indice_now_mensuel_HTB1_LU.json"
)


def executer() -> dict:
    if not os.path.exists(CHEMIN_JSON):
        return {"erreur": f"fichier introuvable : {CHEMIN_JSON}"}
    with open(CHEMIN_JSON, encoding="utf-8") as f:
        lignes = json.load(f)
    indice = kv.get_json(CLE_INDICE, {})
    for ligne in lignes:
        indice[ligne["annee_mois"]] = ligne
    kv.set_json(CLE_INDICE, indice)
    # Le snapshot lu par /api/chaleur embarque la liste des mois : le
    # recalculer maintenant, sinon la page garde l'ancien historique jusqu'au
    # prochain cron (GitHub Actions peut tarder plusieurs heures).
    chaleur_store.calcule_et_sauvegarde_snapshot()
    return {"mois_importes": len(lignes), "premier_mois": min(indice), "dernier_mois": max(indice), "snapshot_rafraichi": True}
