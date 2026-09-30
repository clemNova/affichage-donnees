"""
calcul_indice_now.py (export autonome)
========================================
Copie autonome de `Index_NoW/calcul_indice_now.py` (repo "Pricer Bess"),
destinee a etre importee/executee depuis un autre repo pour l'affichage de
l'historique mensuel de l'indice NOW. Contrairement a l'original, les
chemins sont resolus relativement a ce fichier (pas a la racine du repo
source) et le script exporte aussi le JSON (pas seulement le CSV).

Indice NOW : cout de production de chaleur en arbitrant heure par heure entre
une chaudiere gaz et une chaudiere electrique, compare a un scenario "gaz
seul" sans arbitrage. Calcule pour plusieurs raccordements (cf. RACCORDEMENTS
ci-dessous) : grand industriel en HTB1/LU (RTE), et industriel en HTA/LU
(Enedis, meme methode, seule la colonne TURPE variable elec change).

Hypotheses (validees avec l'utilisateur) :
- Colonne TURPE variable elec (deja en EUR/MWh) : RTE-HTB1_LU_ci pour le
  raccordement HTB1/LU, "Enedis HTA-HTA_LU_Fixe_bi" pour le raccordement
  HTA/LU (les colonnes "RTE-HTA_*" du CSV source sont strictement
  identiques aux colonnes "Enedis HTA-*" correspondantes -- on retient la
  variante Enedis, un site HTA standard etant raccorde au reseau de
  distribution Enedis et non directement a RTE).
- Cote electrique : le site beneficie d'une reduction TURPE electro-intensif
  (30% du TURPE nominal paye), plus l'accise electricite (tarif reduit
  industrie, 0.5 EUR/MWh), le tout divise par le rendement de la chaudiere
  electrique.
- Cote gaz : prix PEG (marche, en PCS) + cout du quota CO2 (0.182 tCO2/MWh de
  gaz), convertis en base PCI (rendement chaudiere exprime en PCI) via le
  facteur standard GRTgaz PCS/PCI = 1.11, puis divise par le rendement de la
  chaudiere gaz.
- aFRR (reserve secondaire, colonnes CSV `prix_rs_hausse` / `prix_rs_baisse`,
  en EUR/MW/h) : la chaudiere (1 MW) s'inscrit en reserve a la hausse quand
  elle tourne a l'electrique (elle peut reduire sa conso -> bascule gaz sur
  activation) et en reserve a la baisse quand elle tourne au gaz (elle peut
  augmenter sa conso -> bascule elec sur activation). Le site percoit la
  remuneration de capacite correspondante (EUR/MW/h, donc EUR/MWh pour 1 MW
  pendant 1h) dans les deux cas, qu'elle soit activee ou non ; elle vient donc
  en deduction du cout de chaque mode. Cette remuneration entre directement
  dans l'arbitrage (elle peut faire basculer le mode choisi).
- Cout net elec = cout_elec - prix_rs_hausse ; cout net gaz = cout_gaz -
  prix_rs_baisse (valeurs manquantes traitees comme 0 EUR/MW/h).
- Cout avec arbitrage = min(cout_net_elec, cout_net_gaz) heure par heure.
- Reference "gaz seul" = cout_net_gaz sans arbitrage (site qui reste tout le
  temps au gaz, mais qui touche quand meme la remuneration de reserve a la
  baisse en continu).
- Indice NOW = l'economie realisee grace a l'arbitrage = cout_gaz_seul -
  cout_avec_arbitrage (toujours >= 0, puisque l'arbitrage ne peut que faire
  aussi bien ou mieux que le gaz seul).

Cf. `FICHE_METHODE_COUT_CHALEUR.md` (dans ce meme dossier) pour le detail
pedagogique des deux couts de revient (gaz / elec).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

DOSSIER = Path(__file__).resolve().parent
CSV_PATH = DOSSIER / "donnees_EPEX_PEG_CO2_RTE_2021-2026_horaire.csv"

# --------------------------------------------------------------------------
# Parametres (a ajuster si les hypotheses changent)
# --------------------------------------------------------------------------
TURPE_REDUCTION_ELECTRO_INTENSIF = 0.30  # part du TURPE nominal effectivement payee
ACCISE_ELEC_EUR_MWH = 0.5
RENDEMENT_ELEC = 0.985

RENDEMENT_GAZ = 0.92
FACTEUR_CO2_GAZ_T_PAR_MWH = 0.182  # tCO2 / MWh gaz (PCI)
RATIO_PCS_PCI_GAZ = 1.11  # standard GRTgaz gaz naturel


def calculer_couts_horaires(df: pd.DataFrame, colonne_turpe_elec: str) -> pd.DataFrame:
    df = df.copy()

    turpe_elec = df[colonne_turpe_elec] * TURPE_REDUCTION_ELECTRO_INTENSIF
    df["cout_elec_eur_mwh"] = (
        df["epex_eur_mwh"] + turpe_elec + ACCISE_ELEC_EUR_MWH
    ) / RENDEMENT_ELEC

    prix_gaz_pci = (
        df["peg_eur_mwh"] + df["co2_eur_t"] * FACTEUR_CO2_GAZ_T_PAR_MWH
    ) * RATIO_PCS_PCI_GAZ
    df["cout_gaz_eur_mwh"] = prix_gaz_pci / RENDEMENT_GAZ

    revenu_afrr_hausse = df["prix_rs_hausse"].fillna(0.0)
    revenu_afrr_baisse = df["prix_rs_baisse"].fillna(0.0)
    df["cout_net_elec_eur_mwh"] = df["cout_elec_eur_mwh"] - revenu_afrr_hausse
    df["cout_net_gaz_eur_mwh"] = df["cout_gaz_eur_mwh"] - revenu_afrr_baisse

    df["cout_arbitrage_eur_mwh"] = df[["cout_net_elec_eur_mwh", "cout_net_gaz_eur_mwh"]].min(axis=1)
    df["mode_choisi"] = df["cout_net_elec_eur_mwh"].lt(df["cout_net_gaz_eur_mwh"]).map(
        {True: "elec", False: "gaz"}
    )

    # Variante "sans les reserves" : arbitrage sur les seuls couts energie/TURPE,
    # sans la remuneration de capacite aFRR (utilisee pour le graphique de
    # croisement gaz/elec et le tableau annuel).
    df["cout_arbitrage_sans_reserve_eur_mwh"] = df[["cout_elec_eur_mwh", "cout_gaz_eur_mwh"]].min(axis=1)
    df["mode_sans_reserve"] = df["cout_elec_eur_mwh"].lt(df["cout_gaz_eur_mwh"]).map(
        {True: "elec", False: "gaz"}
    )

    return df


def agreger_mensuel(df: pd.DataFrame) -> pd.DataFrame:
    mensuel = df.groupby("annee_mois").agg(
        cout_gaz_seul_eur_mwh=("cout_net_gaz_eur_mwh", "mean"),
        cout_elec_seul_eur_mwh=("cout_net_elec_eur_mwh", "mean"),
        cout_arbitrage_eur_mwh=("cout_arbitrage_eur_mwh", "mean"),
        part_heures_elec=("mode_choisi", lambda s: (s == "elec").mean()),
        nb_heures=("cout_arbitrage_eur_mwh", "size"),
    )
    mensuel["indice_now_eur_mwh"] = (
        mensuel["cout_gaz_seul_eur_mwh"] - mensuel["cout_arbitrage_eur_mwh"]
    )
    # Gain total du mois (EUR) pour une chaudiere de 1 MWth tournant en continu :
    # 1 MW pendant nb_heures = nb_heures MWh, donc gain_total = indice_now (EUR/MWh) * nb_heures.
    mensuel["gain_total_eur_mois"] = mensuel["indice_now_eur_mwh"] * mensuel["nb_heures"]
    return mensuel


# Raccordements disponibles : (colonne CSV du TURPE variable elec, nom du fichier de sortie)
RACCORDEMENTS = {
    "HTB1_LU": "RTE-HTB1_LU_ci",
    "HTA_LU": "Enedis HTA-HTA_LU_Fixe_bi",
}


def main() -> None:
    df_source = pd.read_csv(CSV_PATH)
    df_source["datetime_paris"] = pd.to_datetime(
        df_source["datetime_paris"], utc=True
    ).dt.tz_convert("Europe/Paris")
    df_source["annee_mois"] = df_source["datetime_paris"].dt.to_period("M")

    for nom, colonne in RACCORDEMENTS.items():
        df = calculer_couts_horaires(df_source, colonne)
        mensuel = agreger_mensuel(df)

        mensuel_export = mensuel.reset_index()
        mensuel_export["annee_mois"] = mensuel_export["annee_mois"].astype(str)

        out_csv = DOSSIER / f"indice_now_mensuel_{nom}.csv"
        out_json = DOSSIER / f"indice_now_mensuel_{nom}.json"
        mensuel_export.to_csv(out_csv, index=False, float_format="%.2f")
        mensuel_export.to_json(out_json, orient="records")

        print(f"-> {out_csv} / {out_json} ({len(mensuel_export)} mois)")
        print(mensuel_export.round(2).to_string(index=False))
        print()


if __name__ == "__main__":
    main()
