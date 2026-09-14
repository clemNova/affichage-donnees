# Affichage données — dashboard marché électricité

Dépôt **dédié**, séparé volontairement du dépôt principal du pricer BESS
(`Projet_Pricer_BESS`) — pas d'analyses business/CAPEX/TRI ici, uniquement
le code de l'application affichée sur la TV de la salle de réunion. Permet
de connecter ce dépôt à Vercel sans lui donner accès au reste des travaux.

100% côté plateforme (Vercel + GitHub Actions) : rien ne tourne sur un poste
local, aucune base de données à administrer.

## Architecture

```
vercel.json          cron natif Vercel (cf. limite Hobby, section 3)
requirements.txt     deps Python (pandas, entsoe-py, requests)
api/
    cron_daily.py     GET — fetch day-ahead + FCR + capacité aFRR (1x/jour)
    cron_15min.py     GET — fetch activation aFRR + calcule le snapshot KPI (15 min)
    kpis.py           GET — renvoie le dernier snapshot (lu par la page)
    _lib/
        auth.py         verrou d'accès des endpoints cron (fermé par défaut, cf. section 4)
        calc.py          calculs purs (TB2, moyennes, curseur, bas/peak...)
        store.py          orchestration KV (historique 35j, calcul du snapshot)
        kv.py              client REST Upstash/Vercel KV (pas de dépendance redis)
        rte_client.py       client OAuth2 RTE
        entsoe_client.py     wrapper entsoe-py
        fetchers.py            appels RTE/ENTSO-E (fenêtres courtes, sans cache disque)
public/
    index.html         la page TV — fetch /api/kpis toutes les 60s, aucune donnée en dur
.github/workflows/
    cron.yml           déclencheur planifié (15 min + fetch quotidien), cf. section 2
```

**Stockage** : un store Redis compatible REST (Vercel KV, ou un compte Upstash
autonome) — pas de fichier, pas de disque. Clés utilisées :
- `raw:<domaine>` (da, fcr, afrr_up_capa, afrr_down_capa) — quelques jours de
  points 15 min, remplacés chaque jour par `cron_daily`.
- `raw:afrr_activation` — fenêtre glissante ~2 jours, fusionnée par `cron_15min`.
- `hist:<domaine>` — moyennes (et tb2/bas/peak pour `da`) des 35 derniers
  jours, alimenté par `cron_daily` à chaque bascule de journée — base de la
  comparaison 30j glissante.
- `kpis:latest` — le snapshot complet lu par `/api/kpis` (et donc par la page).

## 1. Déployer sur Vercel (nécessite ton compte)

1. **Compte Vercel** (gratuit) si tu n'en as pas — [vercel.com/signup](https://vercel.com/signup),
   connexion via ton compte GitHub (`clemNova`).
2. Tableau de bord Vercel → **"Add New… → Project"** → sélectionne
   `clemNova/affichage-donnees`. Pas de réglage "Root Directory" à changer
   cette fois — tout le dépôt EST l'app.
3. **Variables d'environnement** (écran "Configure Project", section
   "Environment Variables") — copie depuis ton `.env` local du dépôt
   `Projet_Pricer_BESS` (mêmes noms) :
   - `RTE_BALANCING_CAPACITY_CLIENT_ID` / `RTE_BALANCING_CAPACITY_CLIENT_SECRET`
   - `RTE_BALANCING_ENERGY_CLIENT_ID` / `RTE_BALANCING_ENERGY_CLIENT_SECRET`
   - `ENTSOE_API_KEY` (optionnel — sinon clé publique par défaut)
   - `CRON_SECRET` — une valeur aléatoire que tu inventes toi-même. **Sans
     elle, les endpoints cron refusent tout le monde, y compris toi** (cf.
     section 4) — ce n'est pas optionnel si tu veux que ça marche.
4. **Deploy**. Peut échouer/tourner à vide la première fois tant que le KV
   n'est pas branché (étape suivante) — normal.

## 2. Store KV

1. Projet Vercel → onglet **"Storage"** → **"Create Database"** → un store
   **KV / Redis** (palier gratuit largement suffisant, backend Upstash).
2. **"Connect to Project"** → sélectionne ce projet. Vercel ajoute
   automatiquement `KV_REST_API_URL`/`KV_REST_API_TOKEN` — rien à copier.
3. **"Deployments" → dernier déploiement → "…" → "Redeploy"** pour que la
   variable KV tout juste liée soit prise en compte.

## 3. Déclenchement des cron (recommandé : GitHub Actions, déjà dans ce dépôt)

`.github/workflows/cron.yml` appelle `/api/cron_15min` toutes les 15 min et
`/api/cron_daily` deux fois par heure entre 12h et 16h UTC (large marge
autour de la publication EPEX ~12h Paris) — gratuit, fiable, indépendant du
palier Vercel. Deux secrets à créer sur **ce dépôt GitHub**
(Settings → Secrets and variables → Actions) :
- `SITE_URL` = l'URL Vercel du point 1 (sans slash final)
- `CRON_SECRET` = **exactement** la même valeur que côté Vercel (point 1.3)

`vercel.json` déclare aussi les cron natifs Vercel en redondance — sans
risque (écritures idempotentes), mais le palier Hobby limite historiquement
la fréquence à 1x/jour (à vérifier au déploiement). GitHub Actions reste le
déclencheur principal tant que ce n'est pas confirmé.

## 4. Sécurité

- **Dépôt privé, dédié** — Vercel n'a accès qu'au code de cette app, rien du
  reste de tes travaux (CAPEX, TRI, business plan...).
- **`.env` jamais versionné** (`.gitignore`) — tes identifiants RTE/ENTSO-E ne
  transitent jamais par git, tu les tapes toi-même dans le dashboard Vercel.
- **`/api/cron_daily` et `/api/cron_15min` fermés par défaut** (`_lib/auth.py`,
  comparaison à temps constant) : sans `CRON_SECRET` configuré, refus
  systématique — pas de fenêtre où un oubli de config laisse les endpoints
  ouverts. Ces endpoints déclenchent de vrais appels RTE (quota limité,
  ~50 000/mois) — c'est pour ça qu'ils sont protégés, contrairement à
  `/api/kpis`.
- **`/api/kpis` et la page restent publiques** — volontaire, nécessaire pour
  que la page fetch() côté client sans exposer de secret dans le JS livré au
  navigateur ; n'expose que des prix de marché dérivés (déjà publics via
  EPEX/RTE), aucun secret.

## 5. Test manuel avant d'attendre le premier cron

```powershell
curl.exe -H "Authorization: Bearer <secret>" https://<url>/api/cron_daily
curl.exe -H "Authorization: Bearer <secret>" https://<url>/api/cron_15min
curl.exe https://<url>/api/kpis
```

Puis ouvrir `https://<url>/` dans un navigateur.

## 6. Affichage sur l'écran (Teams Rooms / TV)

Ouvrir `https://<url>/` en plein écran (F11) sur le navigateur du poste
connecté à la TV. Le fuseau horaire du poste doit être réglé sur
**Europe/Paris** : les libellés horaires (créneaux, axe du graphe, curseur
"maintenant") sont calculés depuis l'heure locale du navigateur.

## 7. Ce qui n'a pas pu être testé d'ici

Aucun accès à un vrai compte Vercel/Upstash/GitHub Actions depuis la session
qui a écrit ce code :
- Logique métier (calculs, KV, snapshot) : vérifiée en local avec un faux
  store en mémoire, résultats cohérents — jamais exécutée dans le runtime
  Python réel de Vercel.
- Import `entsoe-py` en fonction serverless Python : jamais testé dans cet
  environnement précis (risque résiduel sur la taille du package).
- Imports relatifs `_lib` : chaque `api/*.py` ajoute son propre répertoire à
  `sys.path` avant d'importer `_lib` — si le déploiement échoue avec une
  erreur d'import, c'est le premier point à regarder (aplatir `_lib/`
  directement dans chaque fichier `api/*.py`, ou ajuster `vercel.json` avec
  `functions`/`includeFiles`, sont les solutions de repli habituelles).
- Fréquence réelle des cron Vercel sur le palier Hobby (cf. section 3).
