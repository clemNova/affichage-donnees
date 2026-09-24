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


def calcule_tbn(prix_jour: pd.Series, n_periodes: int) -> float:
    """Definition generale TBx : moyenne des n PERIODES DE 15 MIN les plus
    cheres de la journee - moyenne des n periodes de 15 min les moins cheres,
    calculee directement sur les prix 15 min bruts (pas de moyenne horaire
    intermediaire), avec n_periodes = 4 * x (x = nombre d'HEURES designe par
    TBx). Ex. TB2 = 2 heures = 4*2 = 8 periodes de 15 min -> calcule_tbn(_, 8)."""
    valeurs = prix_jour.dropna().to_numpy()
    if len(valeurs) < 2 * n_periodes:
        return float("nan")
    n_plus_chers = np.partition(valeurs, -n_periodes)[-n_periodes:]
    n_moins_chers = np.partition(valeurs, n_periodes)[:n_periodes]
    return float(np.mean(n_plus_chers) - np.mean(n_moins_chers))


def calcule_tb2(prix_jour: pd.Series) -> float:
    """TB2 = 2 HEURES = 2*4 = 8 periodes de 15 min. TB2 = moyenne des 8
    quarts d'heure les plus chers de la journee - moyenne des 8 quarts
    d'heure les moins chers (cf. calcule_tbn pour la definition generale)."""
    return calcule_tbn(prix_jour, 8)


def calcule_tb4(prix_jour: pd.Series) -> float:
    """TB4 = 4 HEURES = 4*4 = 16 periodes de 15 min (cf. calcule_tbn)."""
    return calcule_tbn(prix_jour, 16)


def calcule_peak(prix_jour: pd.Series, heure_debut: int = 8, heure_fin: int = 20) -> float:
    """Prix Peak (convention marche EPEX) = moyenne des prix sur le bloc
    horaire [heure_debut, heure_fin) de la journee -- 8h-20h par defaut.
    Calcul simplifie : pas de distinction jours ouvres/feries. Le prix Base
    (convention EPEX = moyenne des 24h de la journee) n'a pas besoin de
    fonction dediee : c'est exactement `moyenne_journaliere`."""
    fenetre = prix_jour.between_time(f"{heure_debut:02d}:00", f"{heure_fin:02d}:00", inclusive="left")
    valeurs = fenetre.dropna()
    return float(valeurs.mean()) if len(valeurs) else float("nan")


def moyenne_journaliere(df: pd.DataFrame, colonne: str) -> pd.Series:
    """Moyenne journaliere d'une colonne prix quelconque, indexee par jour
    (minuit local), sur tout l'historique fourni."""
    serie = df.set_index("timestamp")[colonne]
    return serie.groupby(serie.index.normalize()).mean()


def indicateurs_journaliers_da(df_spot_15min: pd.DataFrame) -> pd.DataFrame:
    """DataFrame indexe par jour, colonnes `moyenne_jour` (= prix Base EPEX,
    moyenne 24h), `tb2`, `tb4` et `peak` (prix Peak EPEX, moyenne 8h-20h)."""
    serie = df_spot_15min.set_index("timestamp")["prix_eur_mwh"]
    par_jour = serie.groupby(serie.index.normalize())
    return pd.DataFrame({
        "moyenne_jour": par_jour.mean(),
        "tb2": par_jour.apply(calcule_tb2),
        "tb4": par_jour.apply(calcule_tb4),
        "peak": par_jour.apply(calcule_peak),
    })


def moyenne_nj_glissante(historique_journalier: pd.Series, jour_reference: pd.Timestamp, jours: int = 30) -> float:
    """Moyenne des valeurs journalieres sur les `jours` jours PRECEDANT
    jour_reference (jour_reference exclu)."""
    jour_reference = pd.Timestamp(jour_reference).normalize()
    debut = jour_reference - pd.Timedelta(days=jours)
    masque = (historique_journalier.index >= debut) & (historique_journalier.index < jour_reference)
    valeurs = historique_journalier.loc[masque].dropna()
    if valeurs.empty:
        return float("nan")
    return float(valeurs.mean())


def valeur_veille(historique_journalier: pd.Series, jour_reference: pd.Timestamp) -> float:
    """Valeur du jour precedant immediatement jour_reference (J-1) -- pour la
    comparaison 'vs veille', distincte d'une moyenne glissante."""
    veille = pd.Timestamp(jour_reference).normalize() - pd.Timedelta(days=1)
    if veille not in historique_journalier.index:
        return float("nan")
    valeur = historique_journalier.loc[veille]
    return float(valeur) if pd.notna(valeur) else float("nan")


def ecart_pct(valeur_jour: float, reference: float) -> float:
    """ecart_pct = (valeur_jour - reference) / reference * 100, quelle que
    soit la nature de `reference` (moyenne glissante, valeur de la veille,
    moyenne du meme mois l'annee precedente...)."""
    if reference is None or pd.isna(reference) or reference == 0:
        return float("nan")
    return (valeur_jour - reference) / reference * 100.0


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
