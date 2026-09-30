# Fiche methode : cout de revient de la chaleur (gaz vs electrique) et Indice NOW

## Principe general

Un site industriel qui produit de la chaleur (chaudiere) peut arbitrer,
heure par heure, entre deux modes de production :

- une chaudiere **gaz**,
- une chaudiere **electrique**,

et perçoit en parallele une **remuneration de reserve aFRR** (capacite,
EUR/MW/h) selon le mode dans lequel il se trouve. L'**Indice NOW** mesure
l'economie que cet arbitrage permet de realiser par rapport a une reference
"tout gaz, sans arbitrage".

Toutes les formules ci-dessous s'appliquent **heure par heure**, puis sont
moyennees/sommees par mois.

## 1. Cout de revient de la chaleur au gaz

```
prix_gaz_PCI (EUR/MWh PCI) = (prix_PEG + prix_CO2 x 0.182) x 1.11

cout_gaz (EUR/MWh) = prix_gaz_PCI / rendement_chaudiere_gaz
```

- **prix_PEG** : prix marche du gaz (EUR/MWh PCS, colonne `peg_eur_mwh`).
- **prix_CO2** : prix du quota carbone (EUR/tCO2, colonne `co2_eur_t`),
  multiplie par **0.182 tCO2/MWh gaz** (facteur d'emission standard du gaz
  naturel, base PCI).
- **1.11** : facteur de conversion PCS -> PCI (standard GRTgaz), applique
  car le rendement de chaudiere est lui-meme exprime en base PCI.
- **rendement_chaudiere_gaz = 0.92** (parametre `RENDEMENT_GAZ`).

## 2. Cout de revient de la chaleur electrique

```
cout_elec (EUR/MWh) = (prix_EPEX + TURPE_variable x 0.30 + 0.5) / rendement_chaudiere_elec
```

- **prix_EPEX** : prix spot de l'electricite (EUR/MWh, colonne
  `epex_eur_mwh`).
- **TURPE_variable** : composante variable du TURPE (EUR/MWh) du
  raccordement considere (cf. section 4), deja calculee en amont
  (`TURPE_7.py`).
- **0.30** : le site beneficie du statut **electro-intensif**, qui ramene
  le TURPE variable effectivement paye a 30% du tarif nominal
  (`TURPE_REDUCTION_ELECTRO_INTENSIF`).
- **0.5 EUR/MWh** : **accise electricite** au tarif reduit industrie
  (`ACCISE_ELEC_EUR_MWH`).
- **rendement_chaudiere_elec = 0.985** (parametre `RENDEMENT_ELEC`).

## 3. Remuneration de reserve aFRR (vient en deduction du cout)

Le site (chaudiere 1 MW) s'inscrit en reserve secondaire aFRR :

- **en reserve a la hausse** quand il tourne a l'**electrique** (il peut
  reduire sa consommation -> bascule gaz en cas d'activation) ;
- **en reserve a la baisse** quand il tourne au **gaz** (il peut augmenter
  sa consommation -> bascule elec en cas d'activation).

Cette remuneration de capacite (EUR/MW/h, colonnes `prix_rs_hausse` /
`prix_rs_baisse`) est percue **que la reserve soit activee ou non**, et
vient donc **en deduction** du cout de chaque mode (valeurs manquantes
traitees comme 0) :

```
cout_net_elec = cout_elec - prix_rs_hausse
cout_net_gaz  = cout_gaz  - prix_rs_baisse
```

## 4. Arbitrage et Indice NOW

```
cout_arbitrage (EUR/MWh) = min(cout_net_elec, cout_net_gaz)   [heure par heure]

cout_gaz_seul  = cout_net_gaz    (reference : le site reste tout le temps
                                   au gaz, mais touche quand meme la
                                   remuneration de reserve a la baisse)

Indice NOW (EUR/MWh) = cout_gaz_seul - cout_arbitrage
```

- L'Indice NOW est **toujours >= 0** : l'arbitrage ne peut faire ni pire ni
  moins bien que de rester au gaz seul.
- Il represente **l'economie realisee grace a la flexibilite gaz/elec**,
  reserve aFRR comprise.
- **Gain total mensuel (EUR)** pour une chaudiere de 1 MWth tournant en
  continu : `indice_now_eur_mwh x nb_heures_du_mois`.

## 5. Raccordements disponibles

Seule la colonne de TURPE variable elec change selon le raccordement du
site (meme methode sinon) :

| Raccordement | Gestionnaire | Colonne TURPE utilisee |
|---|---|---|
| `HTB1_LU` | RTE (grand industriel) | `RTE-HTB1_LU_ci` |
| `HTA_LU` | Enedis (industriel) | `Enedis HTA-HTA_LU_Fixe_bi` |

(Les colonnes `RTE-HTA_*` du fichier source sont strictement identiques aux
colonnes `Enedis HTA-*` correspondantes ; on retient la variante Enedis, un
site HTA standard etant raccorde au reseau de distribution et non
directement a RTE.)

## Recapitulatif des parametres

| Parametre | Valeur | Role |
|---|---|---|
| `RENDEMENT_GAZ` | 0.92 | Rendement chaudiere gaz (PCI) |
| `RENDEMENT_ELEC` | 0.985 | Rendement chaudiere electrique |
| `FACTEUR_CO2_GAZ_T_PAR_MWH` | 0.182 | tCO2 / MWh gaz (PCI) |
| `RATIO_PCS_PCI_GAZ` | 1.11 | Conversion PCS -> PCI (GRTgaz) |
| `TURPE_REDUCTION_ELECTRO_INTENSIF` | 0.30 | Part du TURPE nominal effectivement payee |
| `ACCISE_ELEC_EUR_MWH` | 0.5 | Accise electricite, tarif reduit industrie |

Cf. `calcul_indice_now.py` pour l'implementation exacte.
