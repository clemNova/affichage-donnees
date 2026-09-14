"""Calculs derives pour le dashboard marche electricite (TV salle de reunion) :
TB2 day-ahead, moyenne glissante 30 jours + ecart en %, curseur "prix courant"
(day-ahead / FCR capacite / aFRR capacite -- toutes des donnees connues a
l'avance, cf. `trouve_valeur_courante`), moyenne glissante 24h + "dernier prix
publie" pour l'activation aFRR (seule serie reellement publiee au fil de
l'eau). Fonctions pures (aucun I/O, aucun appel reseau).

Copie verbatim de dashboard_marche/dashboard_calc.py (pipeline local) : la
logique metier est identique entre la version locale (SQLite/CSV) et cette
version serverless (KV) -- seule la couche de stockage differe. Garder les
deux fichiers synchronises si l'un des deux evolue.

Convention des DataFrame "connus a l'avance" (day-ahead, FCR capacite, aFRR
capacite) attendus ici : colonnes `timestamp` (naive, heure locale
Europe/Paris) et une colonne prix nommee par l'appelant. Convention des
DataFrame aFRR ACTIVATION : colonnes `timestamp`, `upward_afrr_marginal_price_eur_mwh`,
`downward_afrr_marginal_price_eur_mwh`.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def agrege_moyennes_horaires(prix_jour: pd.Series) -> pd.Series:
    """Moyennes horaires d'UNE journee de prix 15 min (Series indexee par
    timestamp). Groupe par heure REELLE (resample) plutot que par un simple
    reshape 96 -> 24x4, pour rester correct sur les journees courtes/longues
    de changement d'heure (23h ou 25h)."""
    return prix_jour.resample("1h").mean()


def calcule_tb2(prix_jour: pd.Series) -> float:
    """TB2 = moyenne(2 heures pleines les plus cheres) - moyenne(2 heures
    pleines les moins cheres), sur les moyennes HORAIRES (agrege_moyennes_horaires),
    PAS sur les quarts d'heure bruts."""
    moyennes_horaires = agrege_moyennes_horaires(prix_jour).dropna().to_numpy()
    if len(moyennes_horaires) < 4:
        return float("nan")
    deux_h_up = np.partition(moyennes_horaires, -2)[-2:]
    deux_h_down = np.partition(moyennes_horaires, 2)[:2]
    return float(np.mean(deux_h_up) - np.mean(deux_h_down))


def moyenne_journaliere(df: pd.DataFrame, colonne: str) -> pd.Series:
    """Moyenne journaliere d'une colonne prix quelconque, indexee par jour
    (minuit local), sur tout l'historique fourni."""
    serie = df.set_index("timestamp")[colonne]
    return serie.groupby(serie.index.normalize()).mean()


def indicateurs_journaliers_da(df_spot_15min: pd.DataFrame) -> pd.DataFrame:
    """DataFrame indexe par jour, colonnes `moyenne_jour` et `tb2`."""
    serie = df_spot_15min.set_index("timestamp")["prix_eur_mwh"]
    par_jour = serie.groupby(serie.index.normalize())
    return pd.DataFrame({
        "moyenne_jour": par_jour.mean(),
        "tb2": par_jour.apply(calcule_tb2),
    })


def min_max_journaliers(df: pd.DataFrame, colonne: str) -> pd.DataFrame:
    """DataFrame indexe par jour, colonnes `bas` et `peak` : min/max des quarts
    d'heure BRUTS de chaque journee (pas de lissage horaire, contrairement a
    calcule_tb2) -- doit correspondre exactement aux extremes visibles sur un
    profil 15 min affiche."""
    serie = df.set_index("timestamp")[colonne]
    par_jour = serie.groupby(serie.index.normalize())

    def _bas(s: pd.Series) -> float:
        h = s.dropna()
        return float(h.min()) if len(h) else float("nan")

    def _peak(s: pd.Series) -> float:
        h = s.dropna()
        return float(h.max()) if len(h) else float("nan")

    return pd.DataFrame({"bas": par_jour.apply(_bas), "peak": par_jour.apply(_peak)})


def moyenne_30j_glissante(historique_journalier: pd.Series, jour_reference: pd.Timestamp) -> float:
    """Moyenne des valeurs journalieres sur les 30 jours PRECEDANT
    jour_reference (jour_reference exclu)."""
    jour_reference = pd.Timestamp(jour_reference).normalize()
    debut = jour_reference - pd.Timedelta(days=30)
    masque = (historique_journalier.index >= debut) & (historique_journalier.index < jour_reference)
    valeurs = historique_journalier.loc[masque].dropna()
    if valeurs.empty:
        return float("nan")
    return float(valeurs.mean())


def ecart_pct(valeur_jour: float, moyenne_30j: float) -> float:
    """ecart_pct = (valeur_jour - moyenne_30j) / moyenne_30j * 100."""
    if moyenne_30j is None or pd.isna(moyenne_30j) or moyenne_30j == 0:
        return float("nan")
    return (valeur_jour - moyenne_30j) / moyenne_30j * 100.0


def trouve_valeur_courante(df: pd.DataFrame, colonne: str, instant: pd.Timestamp) -> tuple[pd.Timestamp, float] | None:
    """Ligne dont l'intervalle [timestamp, timestamp+15min[ contient `instant`.
    C'est un CURSEUR sur une courbe deja connue, pas une nouvelle mesure."""
    instant = pd.Timestamp(instant)
    localise = instant.tz_localize("Europe/Paris", ambiguous="NaT", nonexistent="NaT")
    if pd.isna(localise):
        return None
    masque = (df["timestamp"] <= instant) & (df["timestamp"] + pd.Timedelta(minutes=15) > instant)
    lignes = df.loc[masque]
    if lignes.empty:
        return None
    ligne = lignes.iloc[0]
    return (ligne["timestamp"], float(ligne[colonne]))


def trouve_dernier_prix_publie_afrr(
    df_afrr_15min: pd.DataFrame, colonne_prix: str, instant: pd.Timestamp
) -> tuple[pd.Timestamp, float, float] | None:
    """Derniere ligne <= instant avec une valeur non-NaN dans `colonne_prix`."""
    instant = pd.Timestamp(instant)
    eligible = df_afrr_15min.loc[
        (df_afrr_15min["timestamp"] <= instant) & df_afrr_15min[colonne_prix].notna()
    ]
    if eligible.empty:
        return None
    ligne = eligible.loc[eligible["timestamp"].idxmax()]
    delai_minutes = (instant - ligne["timestamp"]).total_seconds() / 60.0
    return (ligne["timestamp"], float(ligne[colonne_prix]), float(delai_minutes))


def moyenne_glissante_afrr(
    df_afrr_15min: pd.DataFrame,
    colonne_prix: str,
    instant: pd.Timestamp,
    fenetre: pd.Timedelta = pd.Timedelta(hours=24),
) -> float:
    """Moyenne aFRR ACTIVATION sur une fenetre glissante (24h par defaut)."""
    instant = pd.Timestamp(instant)
    debut = instant - fenetre
    masque = (df_afrr_15min["timestamp"] > debut) & (df_afrr_15min["timestamp"] <= instant)
    valeurs = df_afrr_15min.loc[masque, colonne_prix].dropna()
    if valeurs.empty:
        return float("nan")
    return float(valeurs.mean())
