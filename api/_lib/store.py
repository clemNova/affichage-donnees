"""Orchestration KV : gere l'etat persistant (cache brut court + historique
journalier borne a 35 jours) et calcule le snapshot de KPI lu par /api/kpis.

Modele de stockage (cf. dashboard_web/README.md pour le detail) :
- `raw:<domaine>` : liste de points recents (quelques jours) pour les series
  "connues a l'avance" (da, fcr, afrr_up_capa, afrr_down_capa), ou fenetre
  glissante ~2 jours pour `raw:afrr_activation`.
- `hist:<domaine>` : dict {date_iso: {moyenne, [tb2, bas, peak si DA]}},
  borne aux 35 derniers jours -- sert de base a la comparaison 30j glissante
  (meme calcul que dashboard_marche/dashboard_calc.moyenne_30j_glissante,
  juste applique a un historique KV au lieu d'un CSV local).
- `kpis:latest` : snapshot final, ecrit par le cron 15 min, lu tel quel par
  /api/kpis (aucun calcul au moment de la requete -- reponse instantanee).
"""

from __future__ import annotations

import pandas as pd

from . import calc, kv

PRUNE_JOURS = 35
FENETRE_ACTIVATION_JOURS = 2

DIRECTIONS_CAPACITE = {"up": "afrr_up_capa", "down": "afrr_down_capa"}
DIRECTIONS_ACTIVATION = {"up": "up", "down": "down"}


def _maintenant() -> pd.Timestamp:
    return pd.Timestamp.now(tz="Europe/Paris").tz_localize(None)


def _df_points(points: list[dict], cle_valeur: str) -> pd.DataFrame:
    if not points:
        return pd.DataFrame(columns=["timestamp", cle_valeur])
    df = pd.DataFrame(points)
    df["timestamp"] = pd.to_datetime(df["ts"])
    return df[["timestamp", cle_valeur]].dropna(subset=["timestamp"])


def _prune_hist(hist: dict) -> dict:
    limite = (_maintenant().normalize() - pd.Timedelta(days=PRUNE_JOURS)).date().isoformat()
    return {k: v for k, v in hist.items() if k >= limite}


def maj_serie_connue_avance(nom_domaine: str, points_bruts: list[dict], avec_tb2_bas_peak: bool = False) -> int:
    """Fait basculer dans `hist:<domaine>` toute journee COMPLETE presente
    dans l'ancien `raw:<domaine>` (timestamp < aujourd'hui) avant de le
    remplacer par les nouveaux points fraichement recuperes -- rattrape
    naturellement un cron quotidien manque un jour (plusieurs journees
    completes peuvent etre en attente a la fois)."""
    ancien = kv.get_json(f"raw:{nom_domaine}", [])
    df_ancien = _df_points(ancien, "prix")
    hist = kv.get_json(f"hist:{nom_domaine}", {})

    if not df_ancien.empty:
        aujourdhui = _maintenant().normalize()
        jours_complets = df_ancien[df_ancien["timestamp"] < aujourdhui]
        if not jours_complets.empty:
            if avec_tb2_bas_peak:
                indicateurs = calc.indicateurs_journaliers_da(jours_complets.rename(columns={"prix": "prix_eur_mwh"}))
                extremes = calc.min_max_journaliers(jours_complets, "prix")
                for jour in indicateurs.index:
                    cle = jour.date().isoformat()
                    hist[cle] = {
                        "moyenne": indicateurs.loc[jour, "moyenne_jour"],
                        "tb2": indicateurs.loc[jour, "tb2"],
                        "bas": extremes.loc[jour, "bas"],
                        "peak": extremes.loc[jour, "peak"],
                    }
            else:
                moyennes = calc.moyenne_journaliere(jours_complets, "prix")
                for jour, valeur in moyennes.items():
                    hist[jour.date().isoformat()] = {"moyenne": float(valeur)}

    kv.set_json(f"hist:{nom_domaine}", _prune_hist(hist))
    kv.set_json(f"raw:{nom_domaine}", points_bruts)
    return len(points_bruts)


def maj_activation(nouveaux_points: list[dict]) -> int:
    """Fusionne les nouveaux points d'activation aFRR avec le cache existant
    (dedoublonne par timestamp, garde les ~2 derniers jours) -- contrairement
    aux series connues a l'avance, l'activation n'a pas de notion de "journee
    complete a archiver" : elle reste secondaire, pas de comparaison 30j."""
    ancien = kv.get_json("raw:afrr_activation", [])
    fusion: dict[str, dict] = {p["ts"]: p for p in ancien}
    for p in nouveaux_points:
        fusion[p["ts"]] = p
    limite = (_maintenant() - pd.Timedelta(days=FENETRE_ACTIVATION_JOURS)).isoformat()
    points = sorted((p for ts, p in fusion.items() if ts >= limite), key=lambda p: p["ts"])
    kv.set_json("raw:afrr_activation", points)
    return len(points)


def _serie_du_jour(df: pd.DataFrame, cle_valeur: str, jour: pd.Timestamp) -> list[dict]:
    """Points du jour (creneau [jour, jour+1j[), tries, pour le trace du
    profil 15 min cote frontend -- {ts, prix} legers, pas le DataFrame entier."""
    if df.empty:
        return []
    masque = (df["timestamp"] >= jour) & (df["timestamp"] < jour + pd.Timedelta(days=1))
    sous_ensemble = df.loc[masque].sort_values("timestamp")
    return [{"ts": row.timestamp.isoformat(), "prix": float(getattr(row, cle_valeur))} for row in sous_ensemble.itertuples()]


def _kpis_courbe_connue(ligne: dict, prefixe: str, nom_domaine: str, jour: pd.Timestamp, instant: pd.Timestamp) -> None:
    points = kv.get_json(f"raw:{nom_domaine}", [])
    df = _df_points(points, "prix")
    if df.empty:
        return

    courant = calc.trouve_valeur_courante(df, "prix", instant)
    if courant is not None:
        ts_courant, prix_courant = courant
        ligne[f"{prefixe}_prix_courant"] = prix_courant
        ligne[f"{prefixe}_prix_courant_horodatage"] = ts_courant.isoformat()

    hist = kv.get_json(f"hist:{nom_domaine}", {})
    jour_iso = jour.date().isoformat()
    quotidien_aujourdhui = calc.moyenne_journaliere(df, "prix")
    if jour in quotidien_aujourdhui.index:
        moyenne_jour = float(quotidien_aujourdhui.loc[jour])
    elif jour_iso in hist:
        moyenne_jour = hist[jour_iso]["moyenne"]
    else:
        return

    historique_moyennes = pd.Series({pd.Timestamp(d): v["moyenne"] for d, v in hist.items()})
    moyenne_30j = calc.moyenne_30j_glissante(historique_moyennes, jour) if len(historique_moyennes) else float("nan")
    ligne[f"{prefixe}_moyenne_jour"] = moyenne_jour
    ligne[f"{prefixe}_moyenne_30j"] = moyenne_30j
    ligne[f"{prefixe}_ecart_pct"] = calc.ecart_pct(moyenne_jour, moyenne_30j)


def calcule_et_sauvegarde_snapshot() -> dict:
    instant = _maintenant()
    jour = instant.normalize()
    ligne: dict = {"fetched_at": instant.isoformat()}

    _kpis_courbe_connue(ligne, "da", "da", jour, instant)

    df_da = _df_points(kv.get_json("raw:da", []), "prix")
    hist_da = kv.get_json("hist:da", {})
    if not df_da.empty:
        indicateurs = calc.indicateurs_journaliers_da(df_da.rename(columns={"prix": "prix_eur_mwh"}))
        extremes = calc.min_max_journaliers(df_da, "prix")
        if jour in indicateurs.index:
            tb2 = float(indicateurs.loc[jour, "tb2"])
            bas = float(extremes.loc[jour, "bas"])
            peak = float(extremes.loc[jour, "peak"])
        elif jour.date().isoformat() in hist_da:
            entree = hist_da[jour.date().isoformat()]
            tb2, bas, peak = entree.get("tb2"), entree.get("bas"), entree.get("peak")
        else:
            tb2 = bas = peak = None
        if tb2 is not None:
            hist_tb2 = pd.Series({pd.Timestamp(d): v["tb2"] for d, v in hist_da.items() if "tb2" in v})
            hist_bas = pd.Series({pd.Timestamp(d): v["bas"] for d, v in hist_da.items() if "bas" in v})
            hist_peak = pd.Series({pd.Timestamp(d): v["peak"] for d, v in hist_da.items() if "peak" in v})
            tb2_30j = calc.moyenne_30j_glissante(hist_tb2, jour) if len(hist_tb2) else float("nan")
            bas_30j = calc.moyenne_30j_glissante(hist_bas, jour) if len(hist_bas) else float("nan")
            peak_30j = calc.moyenne_30j_glissante(hist_peak, jour) if len(hist_peak) else float("nan")
            ligne["da_tb2"] = tb2
            ligne["da_tb2_moyenne_30j"] = tb2_30j
            ligne["da_tb2_ecart_pct"] = calc.ecart_pct(tb2, tb2_30j)
            ligne["da_bas_jour"] = bas
            ligne["da_bas_moyenne_30j"] = bas_30j
            ligne["da_bas_ecart_pct"] = calc.ecart_pct(bas, bas_30j)
            ligne["da_peak_jour"] = peak
            ligne["da_peak_moyenne_30j"] = peak_30j
            ligne["da_peak_ecart_pct"] = calc.ecart_pct(peak, peak_30j)

    _kpis_courbe_connue(ligne, "fcr", "fcr", jour, instant)
    for prefixe, nom_domaine in DIRECTIONS_CAPACITE.items():
        _kpis_courbe_connue(ligne, f"afrr_{prefixe}_capa", nom_domaine, jour, instant)

    points_activation = kv.get_json("raw:afrr_activation", [])
    if points_activation:
        df_activation = pd.DataFrame(points_activation)
        df_activation["timestamp"] = pd.to_datetime(df_activation["ts"])
        for prefixe, colonne in DIRECTIONS_ACTIVATION.items():
            ligne[f"afrr_{prefixe}_activation_moyenne_24h"] = calc.moyenne_glissante_afrr(df_activation, colonne, instant)
            dernier = calc.trouve_dernier_prix_publie_afrr(df_activation, colonne, instant)
            if dernier is not None:
                ts_dernier, prix_dernier, delai_min = dernier
                ligne[f"afrr_{prefixe}_activation_dernier_prix"] = prix_dernier
                ligne[f"afrr_{prefixe}_activation_dernier_horodatage"] = ts_dernier.isoformat()
                ligne[f"afrr_{prefixe}_activation_delai_min"] = delai_min

    # Profils du jour (96 points 15 min natifs, pas de lissage horaire) pour
    # les graphes de la page TV -- un seul endpoint (/api/kpis) sert a la fois
    # les KPI scalaires et les courbes, plus simple qu'un second endpoint.
    ligne["series"] = {
        "da": _serie_du_jour(df_da, "prix", jour),
        "fcr": _serie_du_jour(_df_points(kv.get_json("raw:fcr", []), "prix"), "prix", jour),
        "afrr_up_capa": _serie_du_jour(_df_points(kv.get_json("raw:afrr_up_capa", []), "prix"), "prix", jour),
        "afrr_down_capa": _serie_du_jour(_df_points(kv.get_json("raw:afrr_down_capa", []), "prix"), "prix", jour),
    }

    ligne_json = {k: (None if isinstance(v, float) and pd.isna(v) else v) for k, v in ligne.items()}
    kv.set_json("kpis:latest", ligne_json)
    return ligne_json
