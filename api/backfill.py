"""Backfill ponctuel de l'historique (hist:da, hist:fcr, hist:afrr_up_capa,
hist:afrr_down_capa) directement depuis les API RTE/ENTSO-E, qui exposent
aussi les prix PASSES -- evite d'attendre 35 jours que cron_daily les
accumule naturellement jour par jour.

Deux contraintes combinees, decouvertes en usage reel :
1. Un seul appel ENTSO-E/RTE sur une fenetre large (35j) ne renvoie EN
   PRATIQUE que 2-3 jours de points (troncature silencieuse cote API, pas
   d'erreur levee) -- il faut decouper en fenetres COURTES (CHUNK_JOURS)
   qui se suivent chronologiquement (store.maj_serie_connue_avance archive
   une fenetre dans hist:<domaine> des l'appel de la fenetre SUIVANTE).
2. 7 fenetres x 3 appels API strictement sequentiels depasse le timeout
   Vercel (60s max, palier Hobby) -- FUNCTION_INVOCATION_TIMEOUT constate.
3. Chaque fenetre ECRASE raw:<domaine> (store.maj_serie_connue_avance) --
   la derniere fenetre de backfill s'arretant a hier (jamais aujourd'hui,
   journee incomplete), raw:<domaine> se retrouvait SANS les donnees
   d'aujourd'hui/demain que cron_daily y maintient normalement (constate :
   da_prix_courant disparu du snapshot, courbe du jour reduite a 1 point
   apres un backfill). Un dernier appel a fetch_et_stocke_fenetre() avec la
   fenetre normale du cron quotidien restaure cet etat juste apres le
   backfill (et archive au passage la derniere fenetre de backfill dans
   hist:<domaine>, sans perte).

da / fcr / afrr_capa sont des domaines KV independants (raw:da, raw:fcr,
raw:afrr_up_capa+down_capa) : aucune dependance d'ordre ENTRE eux, seulement
A L'INTERIEUR de chaque domaine. Paralleliser les 3 sequences avec un thread
chacune (I/O-bound, le GIL n'est pas un problleme ici) ramene le temps total
au plus lent des 3 au lieu de la somme des 21 appels.

Endpoint manuel, protege par CRON_SECRET comme les autres cron (cf.
_lib/auth.py), mais PAS ajoute au declencheur planifie GitHub Actions
(.github/workflows/cron.yml) -- a appeler une fois (ou apres un reset du
KV), pas a chaque run.
"""

from __future__ import annotations

import os
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd

import cron_daily

from _lib import fetchers, store
from _lib.rte_client import charge_identifiants_rte
from _lib.store import PRUNE_JOURS

JOURS_BACKFILL = PRUNE_JOURS  # 35, la fenetre max conservee dans hist:<domaine>
CHUNK_JOURS = 5  # taille de fenetre que l'API renvoie fidelement en un appel


def _fenetres(debut_globale: pd.Timestamp, fin_globale: pd.Timestamp):
    curseur = debut_globale
    while curseur < fin_globale:
        chunk_fin = min(curseur + pd.Timedelta(days=CHUNK_JOURS), fin_globale)
        yield curseur, chunk_fin
        curseur = chunk_fin


def _backfill_da(debut_globale: pd.Timestamp, fin_globale: pd.Timestamp) -> list[dict]:
    detail = []
    for debut, fin in _fenetres(debut_globale, fin_globale):
        etiquette = f"{debut.date()}..{fin.date()}"
        try:
            points = fetchers.fetch_day_ahead(debut, fin)
            n = store.maj_serie_connue_avance("da", points, avec_tb2_bas_peak=True)
            detail.append({"fenetre": etiquette, "n": n})
        except Exception:
            detail.append({"fenetre": etiquette, "erreur": traceback.format_exc(limit=2)})
    return detail


def _backfill_fcr(debut_globale: pd.Timestamp, fin_globale: pd.Timestamp) -> list[dict]:
    detail = []
    for debut, fin in _fenetres(debut_globale, fin_globale):
        etiquette = f"{debut.date()}..{fin.date()}"
        try:
            points = fetchers.fetch_fcr_capacite(debut, fin)
            n = store.maj_serie_connue_avance("fcr", points)
            detail.append({"fenetre": etiquette, "n": n})
        except Exception:
            detail.append({"fenetre": etiquette, "erreur": traceback.format_exc(limit=2)})
    return detail


def _backfill_afrr_capa(debut_globale: pd.Timestamp, fin_globale: pd.Timestamp) -> list[dict]:
    try:
        identifiants = charge_identifiants_rte("BALANCING_CAPACITY")
    except Exception:
        return [{"fenetre": "toutes", "erreur": traceback.format_exc(limit=2)}]
    detail = []
    for debut, fin in _fenetres(debut_globale, fin_globale):
        etiquette = f"{debut.date()}..{fin.date()}"
        try:
            capa = fetchers.fetch_afrr_capacite(debut, fin, identifiants)
            n_up = store.maj_serie_connue_avance("afrr_up_capa", capa["UP"])
            n_down = store.maj_serie_connue_avance("afrr_down_capa", capa["DOWN"])
            detail.append({"fenetre": etiquette, "up": n_up, "down": n_down})
        except Exception:
            detail.append({"fenetre": etiquette, "erreur": traceback.format_exc(limit=2)})
    return detail


def _total(detail: list[dict], cle: str = "n") -> int:
    return sum(d[cle] for d in detail if cle in d)


def executer() -> dict:
    maintenant = pd.Timestamp.now(tz="Europe/Paris")
    fin_globale = maintenant.normalize()  # exclut aujourd'hui, journee incomplete
    debut_globale = fin_globale - pd.Timedelta(days=JOURS_BACKFILL)

    with ThreadPoolExecutor(max_workers=3) as executeur:
        futur_da = executeur.submit(_backfill_da, debut_globale, fin_globale)
        futur_fcr = executeur.submit(_backfill_fcr, debut_globale, fin_globale)
        futur_capa = executeur.submit(_backfill_afrr_capa, debut_globale, fin_globale)
        detail_da = futur_da.result()
        detail_fcr = futur_fcr.result()
        detail_capa = futur_capa.result()

    # Chaque fenetre de backfill ECRASE raw:<domaine> avec SES points
    # (store.maj_serie_connue_avance) -- la derniere fenetre s'arretant a
    # hier (fin_globale exclut aujourd'hui, journee incomplete), raw:<domaine>
    # se retrouve donc SANS les donnees d'aujourd'hui/demain que cron_daily
    # y maintient normalement (constate : da_prix_courant disparu, series.da
    # reduite a 1 point apres un backfill). Restaure cette fenetre courante
    # juste apres -- archive au passage la derniere fenetre de backfill dans
    # hist:<domaine> (comportement normal de maj_serie_connue_avance), donc
    # aucune perte de l'historique qui vient d'etre construit.
    restauration = cron_daily.fetch_et_stocke_fenetre(
        fin_globale - pd.Timedelta(days=1), fin_globale + pd.Timedelta(days=2)
    )

    return {
        "da": {"total": _total(detail_da), "detail_par_chunk": detail_da},
        "fcr": {"total": _total(detail_fcr), "detail_par_chunk": detail_fcr},
        "afrr_capa": {
            "up_total": _total(detail_capa, "up"),
            "down_total": _total(detail_capa, "down"),
            "detail_par_chunk": detail_capa,
        },
        "restauration_fenetre_courante": restauration,
    }
