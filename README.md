# Affichage données — dashboard marché électricité

Dépôt **dédié**, séparé volontairement du dépôt principal du pricer BESS
(`Projet_Pricer_BESS`) — pas d'analyses business/CAPEX/TRI ici, uniquement
le code de l'application affichée sur la TV de la salle de réunion. Permet
de connecter ce dépôt à Vercel sans lui donner accès au reste des travaux.

100% côté plateforme (Vercel + GitHub Actions) : rien ne tourne sur un poste
local, aucune base de données à administrer.

## Architecture

```
.python-version      fige la version Python (3.12, defaut Vercel)
vercel.json          config Vercel (vide -- pas de cron natif, cf. section 3)
requirements.txt     deps Python (pandas, entsoe-py, requests)
api/
    kpis.py           fonction Vercel /api/kpis — lit kpis:latest dans le KV (public)
    cron_daily.py     fonction Vercel /api/cron_daily — fetch day-ahead + FCR + capacité aFRR (1x/jour)
    cron_15min.py     fonction Vercel /api/cron_15min — fetch activation aFRR + calcule le snapshot KPI (15 min)
    _lib/
        http_utils.py    helpers de reponse HTTP (JSON/CORS, refus 401) partages par les 3 fonctions
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

**Un fichier `api/*.py` = une fonction Vercel** (fonctions Python "par
fichier", cf. [doc officielle
Vercel](https://vercel.com/docs/functions/runtimes/python/api-directory)) :
chaque fichier définit une classe `handler(BaseHTTPRequestHandler)` au niveau
module, et Vercel route automatiquement `/api/<nom>` vers `api/<nom>.py`.
C'est le mode adapté ici (3 endpoints indépendants, pas de framework web).

Une tentative précédente avait centralisé le routing dans un `app.py` racine
déclaré via `pyproject.toml` (`[tool.vercel] entrypoint = "app:handler"`),
sur l'hypothèse (fausse) que Vercel n'accepterait plus qu'un point d'entrée
unique par projet. En réalité ce mécanisme sert à autre chose : un entrypoint
racine (`app.py`/`main.py`/...) n'est requis QUE si Vercel détecte un
**framework preset** Python (FastAPI/Flask/Django via une dépendance dans
`requirements.txt`/`pyproject.toml`) — et ce mode attend une variable `app`
ou `application` (ASGI/WSGI), pas une classe `BaseHTTPRequestHandler`.
Mélanger les deux mécanismes (comme le faisait l'ancien `app.py`) ne
fonctionne dans aucun des deux modes. La présence même de `pyproject.toml`
avec un bloc `[tool.vercel]` pouvait suffire à créer de l'ambiguïté côté
détection — d'où sa suppression ici, au profit du mode "fichier par fichier"
documenté et sans configuration additionnelle requise.

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

## 3. Déclenchement des cron (GitHub Actions — seul déclencheur)

`.github/workflows/cron.yml` appelle `/api/cron_15min` toutes les 15 min et
`/api/cron_daily` plusieurs fois entre 11h et 15h UTC (large marge autour de
la publication EPEX ~12h Paris) — gratuit, fiable, indépendant du palier
Vercel. Deux secrets à créer sur **ce dépôt GitHub**
(Settings → Secrets and variables → Actions) :
- `SITE_URL` = l'URL Vercel du point 1 (sans slash final)
- `CRON_SECRET` = **exactement** la même valeur que côté Vercel (point 1.3)

**Pas de cron natif Vercel** : le palier Hobby refuse toute fréquence
supérieure à 1x/jour (`vercel.json` doit rester vide sur ce point, sinon le
déploiement échoue avec l'erreur *"Hobby accounts are limited to daily cron
jobs"*) — GitHub Actions n'a pas cette contrainte et gère donc seul le
déclenchement.

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
  environnement précis (risque résiduel sur la taille du package — 500 MB
  max, cf. doc Vercel).
- Le déploiement réel (`vercel --prod` ou push GitHub) reste à faire par
  l'utilisateur — c'est le seul test qui confirme que le mode "fonctions par
  fichier" est bien pris en compte par le projet Vercel (vérifier dans
  l'onglet **Deployments → Functions** du dashboard qu'on voit bien 3
  fonctions distinctes `api/kpis`, `api/cron_daily`, `api/cron_15min`, pas un
  entrypoint unique ni une erreur de framework preset).
- Fréquence réelle des cron Vercel sur le palier Hobby (cf. section 3) — non
  applicable ici puisque GitHub Actions est l'unique déclencheur.
