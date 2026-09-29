"""Fetchers RTE/ENTSO-E/NOOS adaptes au contexte serverless : fenetres COURTES
(quelques jours, pas de chunking multi-semaines -- inutile ici), pas de cache
disque (le KV s'en charge cote appelant, cf. cron_daily.py/cron_15min.py).
Logique de parsing reprise du pipeline local deja teste (module_aFRR/,
module_FCR/, spot_entsoe.py) -- meme format de sortie, juste renvoyee comme
liste de dict au lieu d'un DataFrame ecrit sur un CSV.
"""

from __future__ import annotations

import pandas as pd
from entsoe.exceptions import NoMatchingDataError

from . import noos_client
from .entsoe_client import PAYS_FRANCE, cree_client_entsoe
from .rte_client import IdentifiantsRTE, appelle_api_rte


def fetch_day_ahead(date_debut: pd.Timestamp, date_fin: pd.Timestamp) -> list[dict]:
    """Day-ahead spot, converti en EUR/MWh, reechantillonne a 15 min uniforme
    (report en avant) -- gere les deux regimes ENTSO-E (horaire avant ~2025,
    15 min ensuite) comme spot_entsoe.py."""
    client = cree_client_entsoe()
    try:
        serie = client.query_day_ahead_prices(country_code=PAYS_FRANCE, start=date_debut, end=date_fin)
    except (NoMatchingDataError, AttributeError):
        return []
    if serie is None or len(serie) == 0:
        return []
    grille = pd.date_range(serie.index.min(), serie.index.max(), freq="15min")
    serie_15min = serie.reindex(grille, method="ffill")
    ts = serie_15min.index.tz_convert("Europe/Paris").tz_localize(None)
    return [{"ts": t.isoformat(), "prix": float(v)} for t, v in zip(ts, serie_15min.to_numpy())]


def fetch_fcr_capacite(date_debut: pd.Timestamp, date_fin: pd.Timestamp) -> list[dict]:
    """Prix de capacite FCR (EUR/MW), document ENTSO-E A52."""
    client = cree_client_entsoe()
    try:
        df = client.query_contracted_reserve_prices(
            country_code=PAYS_FRANCE, process_type="A52", type_marketagreement_type="A01",
            start=date_debut, end=date_fin,
        )
    except (NoMatchingDataError, AttributeError):
        return []
    if df is None or len(df) == 0:
        return []
    df = df.reset_index()
    df.columns = ["timestamp"] + list(df.columns[1:])
    ts = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert("Europe/Paris").dt.tz_localize(None)
    prix = df[df.columns[1]].to_numpy(dtype=float)
    return [{"ts": t.isoformat(), "prix": float(v)} for t, v in zip(ts, prix)]


def _fetch_capacite_reserve(
    date_debut: pd.Timestamp, date_fin: pd.Timestamp, identifiants: IdentifiantsRTE, correspond
) -> dict[str, list[dict]]:
    """Prix de capacite (EUR/MW/15min, cf. guide RTE Balancing Capacity
    §5.10.1.3), par sens, pour les blocs dont le champ `reserve` verifie le
    predicat `correspond`. Renvoie {"UP": [...], "DOWN": [...]}."""
    url = "https://digital.iservices.rte-france.com/open_api/balancing_capacity/v5/result_procured_reserves"
    reponse = appelle_api_rte(url, identifiants, params={
        "start_date": date_debut.isoformat(), "end_date": date_fin.isoformat(),
    })
    resultat: dict[str, list[dict]] = {"UP": [], "DOWN": []}
    for bloc in reponse.get("result_procured_reserves", []):
        if not correspond(str(bloc.get("reserve") or "")):
            continue
        for valeur in bloc.get("values", []):
            direction = valeur.get("direction")
            if direction not in resultat:
                continue
            resultat[direction].append({"ts": valeur["start_date"][:19], "prix": float(valeur["price"])})
    return resultat


def fetch_afrr_capacite(date_debut: pd.Timestamp, date_fin: pd.Timestamp, identifiants: IdentifiantsRTE) -> dict[str, list[dict]]:
    """Prix de capacite aFRR, par sens. Renvoie {"UP": [...], "DOWN": [...]}."""
    return _fetch_capacite_reserve(date_debut, date_fin, identifiants, lambda r: r.upper() == "AFRR")


def fetch_mfrr_capacite(date_debut: pd.Timestamp, date_fin: pd.Timestamp, identifiants: IdentifiantsRTE) -> dict[str, list[dict]]:
    """Prix de capacite mFRR/RR, par sens. Meme ressource RTE que aFRR (v5),
    disponible seulement depuis le 20/10/2025 (doc RTE "FCR, aFRR et mFRR/RR
    capacity"). Filtre volontairement PERMISSIF (toute valeur de `reserve`
    contenant "MFRR", insensible a la casse -- couvre "MFRR", "MFRR-RR", etc.)
    plutot qu'une egalite stricte : la valeur exacte du libelle RTE n'a pas pu
    etre verifiee empiriquement (pas d'identifiants RTE disponibles pour
    tester), ce filtre reduit le risque de reponse vide par simple erreur
    d'orthographe. Ne peut pas matcher "AFRR" par erreur (ne contient pas "M")."""
    return _fetch_capacite_reserve(date_debut, date_fin, identifiants, lambda r: "MFRR" in r.upper())


def _valeur_ou_nan(v) -> float:
    if v is None:
        return float("nan")
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


def fetch_afrr_activation(date_debut: pd.Timestamp, date_fin: pd.Timestamp, identifiants: IdentifiantsRTE) -> list[dict]:
    """Prix marginal d'activation aFRR (EUR/MWh), agrege du pas 4s natif au
    pas 15 min (meme logique que module_aFRR/afrr_prix_marginal_rte.py).
    Contrainte RTE : max 24h par appel -- l'appelant doit fournir une fenetre
    <= 24h (le cron 15 min ne demande jamais plus que ca)."""
    url = "https://digital.iservices.rte-france.com/open_api/balancing_energy/v5/afrr_marginal_price"
    reponse = appelle_api_rte(url, identifiants, params={
        "start_date": date_debut.isoformat(), "end_date": date_fin.isoformat(),
    })
    lignes = []
    for jour in reponse.get("days", []):
        date_jour = jour["start_date"]
        for point in jour.get("datas", []):
            lignes.append({
                "ts": f"{date_jour}T{point['step']}",
                "up": _valeur_ou_nan(point.get("upward_afrr_marginal_price")),
                "down": _valeur_ou_nan(point.get("downward_afrr_marginal_price")),
            })
    if not lignes:
        return []
    df = pd.DataFrame(lignes)
    df["ts"] = pd.to_datetime(df["ts"])
    df = df.set_index("ts").resample("15min").mean().reset_index()
    resultat = []
    for row in df.itertuples():
        resultat.append({
            "ts": row.ts.isoformat(),
            "up": None if pd.isna(row.up) else float(row.up),
            "down": None if pd.isna(row.down) else float(row.down),
        })
    return resultat


def fetch_peg_spot() -> list[dict]:
    """Prix spot PEG du jour (gaz naturel, EUR/MWh PCS) -- NOOS Energy publie
    UN point par jour (pas de courbe 15 min comme RTE/ENTSO-E), on prend le
    premier point de la reponse. Renvoie [] si NOOS ne renvoie aucun point."""
    donnees = noos_client.appelle_api_noos_peg()
    points = donnees.get("time_series") or []
    if not points:
        return []
    premier = points[0]
    return [{"ts": premier["timestamp"], "prix": float(premier["value"])}]


def fetch_peg_forward(date_debut: pd.Timestamp, date_fin: pd.Timestamp) -> list[dict]:
    """Courbe forward PEG (gaz naturel, EUR/MWh PCS) restreinte a
    [date_debut, date_fin[ -- meme endpoint NOOS que le spot, filtre via
    start_at/end_at (cf. echange utilisateur). Peut renvoyer plusieurs points
    selon la granularite NOOS -- a l'appelant de moyenner sur la fenetre."""
    params = {"start_at": date_debut.isoformat(), "end_at": date_fin.isoformat()}
    donnees = noos_client.appelle_api_noos_peg(params)
    points = donnees.get("time_series") or []
    return [{"ts": p["timestamp"], "prix": float(p["value"])} for p in points]


def fetch_elec_forward_base_peak(date_debut: pd.Timestamp, date_fin: pd.Timestamp) -> dict:
    """Base/Peak forward elec France (NOOS, courbe PWRTE) sur [date_debut,
    date_fin[. NOOS n'expose qu'une courbe brute 15 min (pas de produit
    Base/Peak natif) -- calcule cote client, meme convention que celle
    communiquee par l'utilisateur : Base = moyenne simple de TOUS les points
    15 min de la fenetre ; Peak = moyenne des points sur les jours ouvres
    (lundi-vendredi) entre 8h et 20h (convention europeenne), heure locale
    Europe/Paris. Renvoie {"base": None, "peak": None} si NOOS ne renvoie
    aucun point sur la fenetre."""
    params = {"start_at": date_debut.isoformat(), "end_at": date_fin.isoformat()}
    donnees = noos_client.appelle_api_noos_elec(params)
    points = donnees.get("time_series") or []
    if not points:
        return {"base": None, "peak": None}
    df = pd.DataFrame(points)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert("Europe/Paris")
    base = float(df["value"].mean())
    jour_ouvre = df["timestamp"].dt.weekday < 5
    heure_peak = df["timestamp"].dt.hour.between(8, 19)
    valeurs_peak = df.loc[jour_ouvre & heure_peak, "value"]
    peak = float(valeurs_peak.mean()) if len(valeurs_peak) else None
    return {"base": base, "peak": peak}


def fetch_signal_pp(date_debut: pd.Timestamp, date_fin: pd.Timestamp, identifiants: IdentifiantsRTE) -> dict[str, bool]:
    """Signal jours PP (Periode de Pointe, mecanisme de capacite), API RTE
    'Signal' v2 -- cf. guide RTE_GU_API_Signal_v02.00.01 fourni par
    l'utilisateur. Renvoie {date_iso: bool}, un booleen par jour calendaire
    dans [date_debut, date_fin]. ATTENTION : les donnees ne sont opposables
    (officielles) qu'a partir du 01/11/2026 (cf. guide, §4.1.1.1) -- avant
    cette date l'API peut repondre normalement mais a titre indicatif
    seulement, ou ne rien signaler du tout si la saison n'a pas commence."""
    url = "https://digital.iservices.rte-france.com/open_api/signal/v2/signals"
    params = {"start_date": date_debut.isoformat(), "end_date": date_fin.isoformat()}
    reponse = appelle_api_rte(url, identifiants, params=params)
    resultat: dict[str, bool] = {}
    for bloc in reponse.get("signals", []):
        if bloc.get("type") != "PP":
            continue
        for valeur in bloc.get("values", []):
            resultat[valeur["start_date"][:10]] = bool(valeur["value"])
    return resultat
