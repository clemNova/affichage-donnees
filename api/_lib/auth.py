"""Protection des endpoints cron (cron_daily.py, cron_15min.py) : ces deux
endpoints declenchent de vrais appels RTE/ENTSO-E (quota limite, ex. RTE
~50 000 appels/mois) -- ils ne doivent JAMAIS etre appelables par n'importe
qui sur internet (cf. echange utilisateur : "je veux m'assurer que n'importe
qui sur internet [ne ]puisse[ pas] acceder a mes apis").

FERME PAR DEFAUT (fail closed) : si CRON_SECRET n'est pas configure dans les
variables d'environnement Vercel, l'acces est REFUSE (pas autorise par
defaut) -- un oubli de configuration ne doit jamais se traduire par un
endpoint grand ouvert. /api/kpis (lecture seule, aucune consommation de
quota, aucun secret expose) reste volontairement public et n'utilise PAS
cette fonction -- c'est necessaire pour que la page fetch() cote client
puisse le lire sans exposer de secret dans le JS livre au navigateur (tout
secret inclus dans du JS cote client serait visible via "Afficher la
source").
"""

from __future__ import annotations

import hmac
import os


def autorise(headers) -> bool:
    secret = os.environ.get("CRON_SECRET")
    if not secret:
        return False  # pas de secret configure -> AUCUN acces, pas l'inverse
    fourni = headers.get("Authorization", "")
    attendu = f"Bearer {secret}"
    # comparaison a temps constant : evite une fuite du secret par timing attack
    return hmac.compare_digest(fourni, attendu)
