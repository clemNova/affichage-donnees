# Export Indice NOW

Dossier autonome, pret a etre copie dans un autre repo pour affichage de
l'historique mensuel de l'indice NOW (et recalcul complet si besoin).

## Correction des colonnes aFRR (2026)

Dans le CSV horaire d'origine, `prix_rs_hausse` et `prix_rs_baisse` sont
**inversés à partir du 01/01/2026 00:00 (heure de Paris)** par rapport à
l'API RTE / ENTSO-E (vérifié heure par heure : « hausse » = série UP, « baisse »
= série DOWN ; jusqu'au 31/12/2025 le fichier était dans le bon sens). Les deux
colonnes ont donc été **échangées pour les lignes ≥ 2026-01-01** dans
`donnees_EPEX_PEG_CO2_RTE_2021-2026_horaire.csv`, puis les fichiers mensuels et
le PNG ont été régénérés (mois modifiés : 2026-01 à 2026-08 ; 2021-2025
inchangés). **À ne pas refaire** si la source est corrigée ; en revanche, tout
nouvel export depuis le repo « Pricer Bess » doit recevoir la même correction
tant que la source n'est pas réparée.

## Contenu

- `calcul_indice_now.py` : script de calcul (copie autonome, chemins
  relatifs au dossier). `python calcul_indice_now.py` relit la source
  horaire et regenere les 4 fichiers de sortie ci-dessous.
- `donnees_EPEX_PEG_CO2_RTE_2021-2026_horaire.csv` : source horaire brute
  (prix EPEX, PEG, CO2, TURPE variable par raccordement, prix reserve aFRR
  hausse/baisse), 2021 -> aujourd'hui.
- `indice_now_mensuel_HTB1_LU.csv` / `.json` : historique mensuel, grand
  industriel raccorde HTB1/LU (RTE).
- `indice_now_mensuel_HTA_LU.csv` / `.json` : historique mensuel,
  industriel raccorde HTA/LU (Enedis).
- `plot_indice_now.py` : genere `indice_now_mensuel.png`, graphique
  statique (ligne, 2 series) de l'historique mensuel des deux
  raccordements. `python plot_indice_now.py` (lit les CSV mensuels
  ci-dessus, pas la source horaire).
- `indice_now_mensuel.png` : rendu du graphique ci-dessus, pret a l'emploi.
- `FICHE_METHODE_COUT_CHALEUR.md` : methode de calcul du cout de revient
  des deux chaleurs (gaz / electrique) et de l'indice NOW, pour affichage
  ou explication aux utilisateurs.

## Colonnes des fichiers mensuels

| Colonne | Sens |
|---|---|
| `annee_mois` | Mois (AAAA-MM) |
| `cout_gaz_seul_eur_mwh` | Cout net moyen du mode gaz seul (EUR/MWh), reference sans arbitrage |
| `cout_elec_seul_eur_mwh` | Cout net moyen du mode electrique seul (EUR/MWh) |
| `cout_arbitrage_eur_mwh` | Cout net moyen avec arbitrage heure par heure (EUR/MWh) |
| `part_heures_elec` | Part des heures du mois ou l'arbitrage choisit l'electrique |
| `nb_heures` | Nombre d'heures du mois couvertes par la source |
| `indice_now_eur_mwh` | Indice NOW = `cout_gaz_seul_eur_mwh` - `cout_arbitrage_eur_mwh` (economie de l'arbitrage, toujours >= 0) |
| `gain_total_eur_mois` | Gain total du mois en EUR pour une chaudiere de 1 MWth (= `indice_now_eur_mwh` x `nb_heures`) |

## Mise a jour depuis le repo source

Ce dossier est un instantane. Pour le rafraichir avec des donnees plus
recentes, regenerer `donnees_EPEX_PEG_CO2_RTE_2021-2026_horaire.csv` depuis
le repo "Pricer Bess" (`Index_NoW/`) et relancer `calcul_indice_now.py` ici.
