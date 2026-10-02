"""Orchestration KV : gere l'etat persistant (cache brut court + historique
journalier borne a 35 jours) et calcule le snapshot de KPI lu par /api/kpis.

Modele de stockage (cf. dashboard_web/README.md pour le detail) :
- `raw:<domaine>` : liste de points recents (quelques jours) pour les series
  "connues a l'avance" (da, fcr, afrr_up_capa, afrr_down_capa, mfrr_up_capa,
  mfrr_down_capa), ou fenetre glissante ~2 jours pour `raw:afrr_activation`.
- `hist:<domaine>` : dict {date_iso: {moyenne, [tb2, tb4, peak si DA]}},
  borne aux 35 derniers jours -- sert de base aux comparaisons glissantes
  (30j par defaut, 7j pour TB2/TB4, veille pour da -- cf.
  `_reference_comparaison`), meme principe que dashboard_marche/dashboard_calc
  mais applique a un historique KV au lieu d'un CSV local.
- `hist_mensuel:<domaine>` : dict {"YYYY-MM": moyenne}, PERMANENT (jamais
  purge, contrairement a `hist:<domaine>`) -- alimente separement (donnees
  fournies par l'utilisateur, RTE Open Data n'expose pas un historique aussi
  long via l'API) pour la comparaison "vs meme mois l'annee precedente" de
  FCR et aFRR capacite, cf. `_valeur_mois_an_dernier`.
- `kpis:latest` : snapshot final, ecrit par le cron 15 min, lu tel quel par
  /api/kpis (aucun calcul au moment de la requete -- reponse instantanee).
"""

from __future__ import annotations

import pandas as pd

from . import calc, kv

PRUNE_JOURS = 35
FENETRE_ACTIVATION_JOURS = 2

DIRECTIONS_CAPACITE = {"up": "afrr_up_capa", "down": "afrr_down_capa"}
DIRECTIONS_CAPACITE_MFRR = {"up": "mfrr_up_capa", "down": "mfrr_down_capa"}
DIRECTIONS_ACTIVATION = {"up": "up", "down": "down"}

# --- Cogé serre/industriel (clean spark spread) : memes constantes/formules
# que coge.html cote frontend (a garder synchronisees si l'une des deux
# evolue) -- calculees et stockees ICI (Python) pour beneficier d'une vraie
# comparaison "vs 7j" (hist_coge:<serre|indus>), au lieu d'une reference
# inventee cote client. CO2 reste une constante mock (pas de pipeline reelle
# pour l'instant) -- cf. echange utilisateur.
FACTEUR_SERRE_COGE = 1.8
FACTEUR_INDUS_COGE = 2.2
FACTEUR_EMISSION_CO2_COGE = 0.185  # tCO2 / MWh_gaz (PCI)
RATIO_PCS_PCI_COGE = 1.11
CO2_PRIX_MOCK_COGE = 80.0  # €/tCO2 -- POC, pas de pipeline reelle


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


def _saison_pp(jour: pd.Timestamp) -> tuple[str, str]:
    """Saison PP (mecanisme de capacite, 1er novembre -> 31 mars) contenant
    `jour` -- si `jour` est hors saison (avril-octobre), renvoie la saison A
    VENIR (1er novembre de la meme annee), donc 0 jour ecoule tant qu'elle
    n'a pas commence. Renvoie (debut_iso, fin_iso), fin EXCLUSIVE."""
    debut_nov = pd.Timestamp(year=jour.year, month=11, day=1)
    if jour >= debut_nov:
        debut = debut_nov
    elif jour < pd.Timestamp(year=jour.year, month=4, day=1):
        debut = pd.Timestamp(year=jour.year - 1, month=11, day=1)
    else:
        debut = debut_nov  # avril-octobre : saison a venir
    fin = pd.Timestamp(year=debut.year + 1, month=4, day=1)
    return debut.date().isoformat(), fin.date().isoformat()


def maj_serie_connue_avance(nom_domaine: str, points_bruts: list[dict], avec_indicateurs_da: bool = False) -> int:
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
            if avec_indicateurs_da:
                indicateurs = calc.indicateurs_journaliers_da(jours_complets.rename(columns={"prix": "prix_eur_mwh"}))
                for jour in indicateurs.index:
                    cle = jour.date().isoformat()
                    hist[cle] = {
                        "moyenne": indicateurs.loc[jour, "moyenne_jour"],
                        "tb2": indicateurs.loc[jour, "tb2"],
                        "tb4": indicateurs.loc[jour, "tb4"],
                        "peak": indicateurs.loc[jour, "peak"],
                    }
            else:
                moyennes = calc.moyenne_journaliere(jours_complets, "prix")
                for jour, valeur in moyennes.items():
                    hist[jour.date().isoformat()] = {"moyenne": float(valeur)}

    kv.set_json(f"hist:{nom_domaine}", _prune_hist(hist))
    kv.set_json(f"raw:{nom_domaine}", points_bruts)
    return len(points_bruts)


def maj_prix_journalier(nom_domaine: str, jour_iso: str, prix: float) -> None:
    """Ecrit/remplace directement le prix du jour dans hist:<domaine>, meme
    forme {"moyenne": prix} que les series 15 min -- pour un domaine qui n'a
    qu'UN point par jour (pas de raw:<domaine> a faire vieillir), cas du PEG
    gaz NOOS (publie une fois par jour). Compatible avec calc.moyenne_nj_glissante
    utilise dans calcule_et_sauvegarde_snapshot pour la comparaison vs 7j."""
    hist = kv.get_json(f"hist:{nom_domaine}", {})
    hist[jour_iso] = {"moyenne": float(prix)}
    kv.set_json(f"hist:{nom_domaine}", _prune_hist(hist))


def maj_forward(nom_domaine: str, valeurs: dict) -> None:
    """Ecrase forward:<domaine> avec les dernieres valeurs forward connues
    (gaz -> {"m1": prix, ...} ; elec -> {"m1": {"base":..,"peak":..}, ...} --
    peu importe la forme, juste stockee/renvoyee telle quelle). Pas
    d'historique -- les fenetres "M+1/M+2/1er trimestre complet apres
    M+2" sont relatives a AUJOURD'HUI, donc glissent avec le calendrier a
    chaque execution du cron, cf. cron_daily._fenetre_mois_plus)."""
    kv.set_json(f"forward:{nom_domaine}", valeurs)


def _clean_spark_spread_coge(prix_elec: float, prix_gaz_pcs: float, prix_co2: float, facteur: float) -> float:
    """Meme formule que cleanSparkSpread() dans coge.html : facteur = MWh_gaz
    (PCI) par MWh_elec (pas un rendement <1, cf. echange utilisateur)."""
    prix_gaz_pci = prix_gaz_pcs * RATIO_PCS_PCI_COGE
    return prix_elec - (prix_gaz_pci + FACTEUR_EMISSION_CO2_COGE * prix_co2) * facteur


def _super_peak_coge(serie: pd.Series) -> float:
    """Moyenne des 3 meilleures heures CONSECUTIVES (fenetre glissante de 12
    quarts d'heure) -- meme definition que superPeak() dans coge.html."""
    valeurs = serie.dropna().to_numpy()
    fenetre_n = 12
    meilleur = None
    for i in range(len(valeurs) - fenetre_n + 1):
        fenetre = valeurs[i:i + fenetre_n]
        moy = float(fenetre.mean())
        if meilleur is None or moy > meilleur:
            meilleur = moy
    return meilleur if meilleur is not None else float("nan")


def _serie_jour_complete(df: pd.DataFrame, cle_valeur: str, jour: pd.Timestamp) -> pd.Series | None:
    """Serie 15 min (96 points) du jour demande, indexee par timestamp --
    None si le jour n'est pas complet dans `df` (ex. raw:da pas encore
    rafraichi aujourd'hui)."""
    masque = (df["timestamp"] >= jour) & (df["timestamp"] < jour + pd.Timedelta(days=1))
    sous_ensemble = df.loc[masque].sort_values("timestamp")
    if len(sous_ensemble) < 96:
        return None
    return sous_ensemble.set_index("timestamp")[cle_valeur]


def maj_historique_coge(nom_domaine: str, jour_iso: str, valeurs: dict) -> None:
    """Ecrit le jour dans hist_coge:<serre|indus> ({"base":..,"peak":..,
    "superpeak":..}), retention 35j (PRUNE_JOURS) comme les autres hist --
    permet une vraie comparaison vs 7j (calc.moyenne_nj_glissante), au lieu
    d'une reference inventee cote frontend, cf. echange utilisateur."""
    hist = kv.get_json(f"hist_coge:{nom_domaine}", {})
    hist[jour_iso] = valeurs
    kv.set_json(f"hist_coge:{nom_domaine}", _prune_hist(hist))


def _maj_kpis_coge(ligne: dict, prefixe: str, serie_jour_elec: pd.Series | None, prix_gaz_pcs: float | None, jour: pd.Timestamp, facteur: float) -> None:
    if serie_jour_elec is None or prix_gaz_pcs is None:
        return
    jour_iso = jour.date().isoformat()
    clean = serie_jour_elec.apply(lambda p: _clean_spark_spread_coge(p, prix_gaz_pcs, CO2_PRIX_MOCK_COGE, facteur))
    valeurs_jour = {
        "base": float(clean.mean()),
        "peak": float(clean.between_time("08:00", "20:00", inclusive="left").mean()),
        "superpeak": _super_peak_coge(clean),
    }
    maj_historique_coge(prefixe, jour_iso, valeurs_jour)

    hist = kv.get_json(f"hist_coge:{prefixe}", {})
    for cle, valeur in valeurs_jour.items():
        historique_cle = pd.Series({pd.Timestamp(d): v[cle] for d, v in hist.items() if cle in v})
        reference = calc.moyenne_nj_glissante(historique_cle, jour, 7)
        ligne[f"coge_{prefixe}_{cle}"] = valeur
        # Un spread peut changer de signe, donc le % vs 7j peut legitimement
        # etre tres grand quand la reference est proche de 0 -- affiche tel
        # quel, pas une erreur de calcul (cf. echange utilisateur : a laisser
        # visible plutot que masque).
        ligne[f"coge_{prefixe}_{cle}_ecart_pct"] = calc.ecart_pct(valeur, reference)


PRUNE_JOURS_SIGNAL_PP = 400  # couvre une saison PP complete (nov-mars) + marge


def maj_signal_pp(signaux: dict[str, bool]) -> None:
    """Fusionne les nouveaux signaux jours PP (API RTE Signal, cf.
    fetchers.fetch_signal_pp) dans signal_pp:jours -- retention PLUS LONGUE
    que hist:<domaine> (35j, cf. PRUNE_JOURS) car il faut pouvoir compter les
    jours PP depuis le debut de la saison (nov-mars, ~5 mois)."""
    jours = kv.get_json("signal_pp:jours", {})
    jours.update(signaux)
    limite = (pd.Timestamp.now(tz="Europe/Paris").normalize() - pd.Timedelta(days=PRUNE_JOURS_SIGNAL_PP)).date().isoformat()
    kv.set_json("signal_pp:jours", {d: v for d, v in jours.items() if d >= limite})


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


def _valeur_mois_an_dernier(nom_domaine: str, jour: pd.Timestamp) -> float:
    """Moyenne du meme mois calendaire, un an plus tot, lue dans
    `hist_mensuel:<domaine>` -- stockage PERMANENT (pas de purge, contrairement
    a `hist:<domaine>` limite a 35 jours), cle 'YYYY-MM' -> moyenne mensuelle.
    Alimente separement (RTE Open Data n'expose pas un historique aussi long
    via l'API) -- renvoie NaN tant que la cle n'existe pas encore."""
    mois_reference = (pd.Timestamp(jour).normalize() - pd.DateOffset(years=1)).strftime("%Y-%m")
    hist_mensuel = kv.get_json(f"hist_mensuel:{nom_domaine}", {})
    valeur = hist_mensuel.get(mois_reference)
    return float(valeur) if valeur is not None else float("nan")


def _reference_comparaison(mode: str, nom_domaine: str, historique_moyennes: pd.Series, jour: pd.Timestamp) -> float:
    if mode == "veille":
        return calc.valeur_veille(historique_moyennes, jour)
    if mode == "mois_an_dernier":
        return _valeur_mois_an_dernier(nom_domaine, jour)
    jours = 7 if mode == "7j" else 30
    return calc.moyenne_nj_glissante(historique_moyennes, jour, jours) if len(historique_moyennes) else float("nan")


def _kpis_courbe_connue(ligne: dict, prefixe: str, nom_domaine: str, jour: pd.Timestamp, instant: pd.Timestamp, mode: str = "30j") -> None:
    """`mode` fixe la base de comparaison du pourcentage d'ecart : "30j"
    (moyenne glissante 30j, defaut), "veille" (J-1), "7j" (moyenne glissante
    7j) ou "mois_an_dernier" (meme mois, annee precedente, cf.
    `_valeur_mois_an_dernier`)."""
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
    # Complete avec les jours presents dans raw:<domaine> mais pas encore
    # archives dans hist:<domaine> (ex. hier, juste apres minuit, avant que
    # cron_daily n'ait tourne aujourd'hui) -- sans ca, "vs veille" resterait
    # sans donnee (donc pas de pill) jusqu'au premier cron_daily du jour.
    for jour_present, valeur_presente in quotidien_aujourdhui.items():
        if jour_present != jour:
            historique_moyennes[jour_present] = float(valeur_presente)
    reference = _reference_comparaison(mode, nom_domaine, historique_moyennes, jour)
    ligne[f"{prefixe}_moyenne_jour"] = moyenne_jour
    ligne[f"{prefixe}_ecart_pct"] = calc.ecart_pct(moyenne_jour, reference)


def calcule_et_sauvegarde_snapshot() -> dict:
    instant = _maintenant()
    jour = instant.normalize()
    ligne: dict = {"fetched_at": instant.isoformat()}

    # Prix moyen du jour (= prix Base EPEX, moyenne 24h) compare a la veille
    # (J-1), pas a une moyenne glissante -- cf. echange utilisateur.
    _kpis_courbe_connue(ligne, "da", "da", jour, instant, mode="veille")

    df_da = _df_points(kv.get_json("raw:da", []), "prix")
    hist_da = kv.get_json("hist:da", {})
    if not df_da.empty:
        indicateurs = calc.indicateurs_journaliers_da(df_da.rename(columns={"prix": "prix_eur_mwh"}))
        if jour in indicateurs.index:
            tb2 = float(indicateurs.loc[jour, "tb2"])
            tb4 = float(indicateurs.loc[jour, "tb4"])
            peak = float(indicateurs.loc[jour, "peak"])
        elif jour.date().isoformat() in hist_da:
            entree = hist_da[jour.date().isoformat()]
            tb2, tb4, peak = entree.get("tb2"), entree.get("tb4"), entree.get("peak")
        else:
            tb2 = tb4 = peak = None
        if tb2 is not None:
            hist_tb2 = pd.Series({pd.Timestamp(d): v["tb2"] for d, v in hist_da.items() if "tb2" in v})
            hist_tb4 = pd.Series({pd.Timestamp(d): v["tb4"] for d, v in hist_da.items() if "tb4" in v})
            hist_peak = pd.Series({pd.Timestamp(d): v["peak"] for d, v in hist_da.items() if "peak" in v})
            # Complete avec les jours de raw:da pas encore archives dans
            # hist:da (meme raison que dans _kpis_courbe_connue -- evite un
            # trou quotidien juste apres minuit, avant le premier cron_daily).
            for jour_present in indicateurs.index:
                if jour_present == jour:
                    continue
                hist_tb2[jour_present] = float(indicateurs.loc[jour_present, "tb2"])
                hist_tb4[jour_present] = float(indicateurs.loc[jour_present, "tb4"])
                hist_peak[jour_present] = float(indicateurs.loc[jour_present, "peak"])
            # TB2/TB4 compares a 7j (spreads plus volatils qu'un prix moyen).
            # Peak compare a la veille (comme Base/Prix moyen du jour) --
            # cf. echange utilisateur.
            tb2_ref = calc.moyenne_nj_glissante(hist_tb2, jour, 7) if len(hist_tb2) else float("nan")
            tb4_ref = calc.moyenne_nj_glissante(hist_tb4, jour, 7) if len(hist_tb4) else float("nan")
            peak_ref = calc.valeur_veille(hist_peak, jour)
            ligne["da_tb2"] = tb2
            ligne["da_tb2_ecart_pct"] = calc.ecart_pct(tb2, tb2_ref)
            ligne["da_tb4"] = tb4
            ligne["da_tb4_ecart_pct"] = calc.ecart_pct(tb4, tb4_ref)
            ligne["da_peak_jour"] = peak
            ligne["da_peak_ecart_pct"] = calc.ecart_pct(peak, peak_ref)

    # FCR, aFRR et mFRR capacite : compares au meme mois l'annee precedente
    # (pas a une moyenne glissante 30j) -- historique fourni separement par
    # l'utilisateur dans hist_mensuel:<domaine>, cf. _valeur_mois_an_dernier.
    # mFRR cote RTE ne remonte qu'au 20/10/2025, mais le CSV fourni par
    # l'utilisateur couvre un historique plus long (mfrr_up_capa depuis
    # 2021, mfrr_down_capa depuis 2026-05 seulement pour l'instant) -- le
    # pill reste simplement absent tant que le mois de reference n'existe
    # pas encore dans hist_mensuel:mfrr_down_capa, cf. echange utilisateur.
    _kpis_courbe_connue(ligne, "fcr", "fcr", jour, instant, mode="mois_an_dernier")
    for prefixe, nom_domaine in DIRECTIONS_CAPACITE.items():
        _kpis_courbe_connue(ligne, f"afrr_{prefixe}_capa", nom_domaine, jour, instant, mode="mois_an_dernier")
    for prefixe, nom_domaine in DIRECTIONS_CAPACITE_MFRR.items():
        _kpis_courbe_connue(ligne, f"mfrr_{prefixe}_capa", nom_domaine, jour, instant, mode="mois_an_dernier")

    # PEG gaz (NOOS) : une seule valeur par jour (pas de courbe 15 min, donc
    # pas de "prix courant" ni de raw:<domaine> -- cf. maj_prix_journalier,
    # appelee par cron_daily). Comparaison vs moyenne glissante 7j, pour la
    # pill "vs 7j" du dashboard Cogenerations.
    hist_peg = kv.get_json("hist:noos_peg", {})
    jour_iso = jour.date().isoformat()
    if jour_iso in hist_peg:
        prix_peg = hist_peg[jour_iso]["moyenne"]
        hist_peg_series = pd.Series({pd.Timestamp(d): v["moyenne"] for d, v in hist_peg.items()})
        ref_peg_7j = calc.moyenne_nj_glissante(hist_peg_series, jour, 7)
        ligne["peg_prix"] = prix_peg
        ligne["peg_ecart_pct"] = calc.ecart_pct(prix_peg, ref_peg_7j)

        # Cogé serre/industriel (clean spark spread) : calcule et stocke
        # SEULEMENT si le prix gaz du jour est reel (jamais de valeur
        # mock stockee dans l'historique -- garde hist_coge propre) ET si
        # raw:da a bien les 96 points du jour (jour-ahead deja publie).
        serie_jour_da = _serie_jour_complete(df_da, "prix", jour) if not df_da.empty else None
        _maj_kpis_coge(ligne, "serre", serie_jour_da, prix_peg, jour, FACTEUR_SERRE_COGE)
        _maj_kpis_coge(ligne, "indus", serie_jour_da, prix_peg, jour, FACTEUR_INDUS_COGE)

    # Forward PEG (NOOS) : {"m1": prix, "m2": prix, "q1": prix}, EUR/MWh PCS
    # -- ecrit par cron_daily (cf. store.maj_forward), pas d'historique propre.
    forward_peg = kv.get_json("forward:noos_peg", {})
    if forward_peg:
        ligne["peg_forward"] = forward_peg

    # Forward elec France (NOOS PWRTE) : {"m1": {"base":..,"peak":..}, ...},
    # EUR/MWh -- meme principe, ecrit par cron_daily.
    forward_elec = kv.get_json("forward:noos_elec", {})
    if forward_elec:
        ligne["elec_forward"] = forward_elec

    # Jours PP (API RTE Signal) : ecrit par cron_daily (cf. store.maj_signal_pp).
    # ATTENTION : donnees non opposables (officielles) avant le 01/11/2026,
    # cf. fetchers.fetch_signal_pp -- pp_jours_ecoules vaudra normalement 0
    # tant que la saison n'a pas commence.
    signal_pp = kv.get_json("signal_pp:jours", {})
    jour_iso = jour.date().isoformat()
    if jour_iso in signal_pp:
        ligne["pp_en_pp"] = bool(signal_pp[jour_iso])
    debut_saison, fin_saison = _saison_pp(jour)
    ligne["pp_jours_ecoules"] = sum(
        1 for d, v in signal_pp.items() if v and debut_saison <= d < fin_saison
    )

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
        "mfrr_up_capa": _serie_du_jour(_df_points(kv.get_json("raw:mfrr_up_capa", []), "prix"), "prix", jour),
        "mfrr_down_capa": _serie_du_jour(_df_points(kv.get_json("raw:mfrr_down_capa", []), "prix"), "prix", jour),
    }

    ligne_json = {k: (None if isinstance(v, float) and pd.isna(v) else v) for k, v in ligne.items()}
    kv.set_json("kpis:latest", ligne_json)
    return ligne_json
