"""Import ponctuel de l'historique long FCR/aFRR/mFRR (gains journaliers en
EUR/MW/JOUR, calcules pour un actif de reference 1 MW -- cf. echange
utilisateur) depuis `data/gains_capacitaires_journalier.csv`, fourni
separement par l'utilisateur : RTE Open Data n'expose pas un historique aussi
long via l'API (cf. `hist_mensuel:<domaine>` dans `store.py`).

Calcule des moyennes MENSUELLES par domaine et les ecrit dans un stockage
PERMANENT (pas de purge, contrairement a `hist:<domaine>` limite a 35 jours) :
`hist_mensuel:<domaine>` = {"YYYY-MM": moyenne}. Utilise par
`store._valeur_mois_an_dernier` pour la comparaison "vs meme mois l'annee
precedente" de FCR et aFRR capacite (`_kpis_courbe_connue(..., mode="mois_an_dernier")`),
comparee a `{prefixe}_moyenne_jour` qui est dans l'unite NATIVE RTE
(EUR/MW/15min, pas EUR/MW/jour) -- on DIVISE donc par PAR_JOUR (96) a
l'import pour rester dans la meme unite, sinon l'ecart calcule est absurde
(ex. -98%, un facteur ~96 d'ecart d'unite plutot qu'une vraie variation).

Colonnes CSV -> domaine :
- gain_fcr_eur -> fcr
- gain_afrr_hausse_eur (repli sur gain_afrr_symetrique_eur si absent,
  regime de prix symetrique avant le 15/10/2023) -> afrr_up_capa
- gain_afrr_baisse_eur (meme repli) -> afrr_down_capa
- gain_mfrr_hausse_eur / gain_mfrr_baisse_eur -> mfrr_up_capa / mfrr_down_capa
  (non utilise par une comparaison aujourd'hui -- mFRR reste sur vs 30j,
  historique trop recent cote RTE -- importe quand meme pour disponibilite
  future).

Endpoint manuel, protege par CRON_SECRET comme `backfill_historique` (cf.
`_lib/auth.py`), mais PAS ajoute au declencheur planifie GitHub Actions
(`.github/workflows/cron.yml`) -- a appeler une fois (ou apres mise a jour du
CSV), pas a chaque run.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd

from _lib import kv

CHEMIN_CSV = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "gains_capacitaires_journalier.csv"
)

# Prix RTE natif en EUR/MW/15min -- le CSV fournit des gains en EUR/MW/JOUR
# (96 pas de 15 min) : diviser par cette constante ramene les deux sources a
# la meme unite avant de les comparer (cf. docstring du module).
PAR_JOUR = 96


def _series_par_domaine(df: pd.DataFrame) -> dict[str, pd.Series]:
    afrr_up = df["gain_afrr_hausse_eur"].fillna(df["gain_afrr_symetrique_eur"])
    afrr_down = df["gain_afrr_baisse_eur"].fillna(df["gain_afrr_symetrique_eur"])
    return {
        "fcr": df["gain_fcr_eur"],
        "afrr_up_capa": afrr_up,
        "afrr_down_capa": afrr_down,
        "mfrr_up_capa": df["gain_mfrr_hausse_eur"],
        "mfrr_down_capa": df["gain_mfrr_baisse_eur"],
    }


def executer() -> dict:
    if not os.path.exists(CHEMIN_CSV):
        return {"erreur": f"fichier introuvable : {CHEMIN_CSV}"}

    df = pd.read_csv(CHEMIN_CSV, parse_dates=["jour"]).set_index("jour")
    resultats: dict = {}

    for domaine, serie in _series_par_domaine(df).items():
        serie = serie.dropna()
        if serie.empty:
            resultats[domaine] = "aucune donnee dans le CSV"
            continue
        moyennes_mensuelles = serie.groupby(serie.index.to_period("M")).mean() / PAR_JOUR

        hist_mensuel = kv.get_json(f"hist_mensuel:{domaine}", {})
        for periode, valeur in moyennes_mensuelles.items():
            hist_mensuel[str(periode)] = float(valeur)
        kv.set_json(f"hist_mensuel:{domaine}", hist_mensuel)

        resultats[domaine] = {
            "mois_importes": int(len(moyennes_mensuelles)),
            "premier_mois": str(moyennes_mensuelles.index.min()),
            "dernier_mois": str(moyennes_mensuelles.index.max()),
        }

    return resultats
