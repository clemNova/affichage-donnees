# Affichage données — dashboard marché électricité

Dépôt **dédié**, séparé volontairement du dépôt principal du pricer BESS
(`Projet_Pricer_BESS`) — pas d'analyses business/CAPEX/TRI ici, uniquement
le code de l'application affichée sur la TV de la salle de réunion. Permet
de connecter ce dépôt à Vercel sans lui donner accès au reste des travaux.

100% côté plateforme (Vercel + GitHub Actions) : rien ne tourne sur un poste
local, aucune base de données à administrer.

## Architecture

```
pyproject.toml        deps Python (pandas, numpy, requests, entsoe-py) + declare
                       le point d'entree Python unique (app:handler) -- necessaire
                       sur CE projet Vercel, cf. encadre ci-dessous
uv.lock               versions figees des dependances (genere par `uv lock`,
                       regenerer apres toute modif de pyproject.toml)
.python-version       fige la version Python (3.12, defaut Vercel)
vercel.json          config Vercel (vide -- pas de cron natif, cf. section 3)
app.py               point d'entree Python unique -- route lui-meme /api/kpis,
                     /api/cron_daily, /api/cron_15min vers la logique
                     correspondante dans api/ (imports directs, pas de HTTP interne)
api/
    cron_daily.py     logique (executer()) — fetch day-ahead + FCR + capacité aFRR (1x/jour)
    cron_15min.py     logique (executer()) — fetch activation aFRR + calcule le snapshot KPI (15 min)
    _lib/
        http_utils.py    helpers de reponse HTTP (JSON/CORS, refus 401) utilises par app.py
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

**Pourquoi un seul `app.py` et pas un fichier par endpoint dans `api/`** : la
doc Vercel décrit un mode "fonctions Python par fichier dans `/api`" (chaque
fichier définit sa propre classe `handler`, sans entrypoint requis) —
en pratique, sur ce projet, ce mode ne s'active pas : un déploiement avec
`api/kpis.py`, `api/cron_daily.py`, `api/cron_15min.py` définissant chacun
`class handler(BaseHTTPRequestHandler)` échoue au build avec l'erreur *"No
python entrypoint found in default locations, but found potential
entrypoints: api/cron_15min.py (variable: handler), api/cron_daily.py
(variable: handler), api/kpis.py (variable: handler)"* — Vercel trouve 3
candidats et refuse de choisir tout seul. Le message d'erreur suggère
lui-même la solution : déclarer explicitement UN entrypoint via
`pyproject.toml` (`[tool.vercel] entrypoint = "module:variable"`) — et ce
mécanisme accepte bien une classe `BaseHTTPRequestHandler` comme cible (pas
uniquement `app`/`application` ASGI/WSGI, contrairement à ce que suggère la
doc générale sur les entrypoints "framework"). D'où `app.py` : un routeur
unique déclaré explicitement, qui dispatche lui-même vers la logique de
`api/cron_daily.py` / `api/cron_15min.py` (imports directs de `executer()`)
et le store KV pour `/api/kpis` — cette fois sans ambiguïté puisque `app.py`
est le seul fichier du projet à définir un symbole `handler`.

**Dépendances via `pyproject.toml` (pas `requirements.txt`)** : dès qu'un
`pyproject.toml` existe, Vercel résout les dépendances avec `uv lock` plutôt
qu'avec `pip install -r requirements.txt` — et `uv lock` exige une table
`[project]` valide (`name`, `dependencies`, ...), sinon le build échoue avec
*"error: No `project` table found in: pyproject.toml"* (rencontré ici quand
le fichier ne contenait que `[tool.vercel]`). Les deux mécanismes ne se
cumulent pas : avoir à la fois `pyproject.toml` et `requirements.txt`
laisserait `requirements.txt` ignoré silencieusement une fois `uv` actif —
d'où la suppression de `requirements.txt`, toutes les dépendances (pandas,
numpy, requests, entsoe-py) vivent désormais dans `pyproject.toml`.

**Stockage** : un store Redis compatible REST (intégration Marketplace
"Upstash for Redis", ou un compte Upstash autonome) — pas de fichier, pas de
disque. Clés utilisées :
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

"Vercel KV" natif n'existe plus — c'est désormais une intégration du
**Marketplace Vercel**, backend Upstash.

1. Projet Vercel → onglet **"Storage"** → **"Create Database"** (ou
   Marketplace) → **"Upstash for Redis"** (pas "Upstash Vector"/"QStash"/
   "Search" — bien "Upstash for Redis").
2. Crée une base sur le palier gratuit et **lie-la à ce projet** (l'étape de
   liaison est intégrée à la création via le Marketplace).
3. Vercel ajoute automatiquement `KV_REST_API_URL`/`KV_REST_API_TOKEN` aux
   variables d'environnement du projet — rien à copier, le code
   (`api/_lib/kv.py`) les lit tel quel.
4. **"Deployments" → dernier déploiement → "…" → "Redeploy"** pour que les
   variables tout juste liées soient prises en compte (obligatoire — un
   déploiement déjà en cours ne les voit pas).

## 3. Déclenchement des cron

`.github/workflows/cron.yml` appelle `/api/cron_15min` toutes les 15 min et
`/api/cron_daily` plusieurs fois entre 11h et 15h UTC (large marge autour de
la publication EPEX ~12h Paris) — gratuit, indépendant du palier Vercel.
Deux secrets à créer sur **ce dépôt GitHub**
(Settings → Secrets and variables → Actions) :
- `SITE_URL` = l'URL Vercel du point 1, **la vraie URL de production**, sans
  slash final (onglet Deployments → déploiement marqué "Production", ou
  Settings → Domains — PAS une URL de déploiement avec un hash aléatoire du
  type `...-hyb367pqh-....vercel.app`, celles-ci sont protégées et changent
  à chaque déploiement)
- `CRON_SECRET` = **exactement** la même valeur que côté Vercel (point 1.3)

**Pas de cron natif Vercel** : le palier Hobby refuse toute fréquence
supérieure à 1x/jour (`vercel.json` doit rester vide sur ce point, sinon le
déploiement échoue avec l'erreur *"Hobby accounts are limited to daily cron
jobs"*) — GitHub Actions n'a pas cette contrainte.

**Limite connue de GitHub Actions** : les schedules très fréquents comme
`*/15 * * * *` sont les plus demandés de toute la plateforme (tout le monde
programme sur les minutes rondes) — GitHub documente lui-même que ces
déclenchements peuvent être **retardés ou sautés** en cas de forte charge,
en particulier sur les comptes gratuits. En pratique, ça peut se traduire
par des trous de plusieurs heures entre deux runs plutôt qu'un run toutes
les 15 min pile. Écritures KV idempotentes (append/overwrite) : ce n'est pas
un problème de fiabilité applicative, juste une fraîcheur des données moins
garantie qu'annoncée.

**Déclencheur externe additionnel (recommandé pour une cadence fiable)** :
[cron-job.org](https://console.cron-job.org/signup) (gratuit) appelle les
mêmes endpoints de façon bien plus régulière que le scheduler GitHub Actions.
Garder les deux actifs en même temps n'est pas un problème (idempotent) —
juste de la redondance utile. Pour chaque cronjob créé sur cron-job.org :
- **URL** : `https://<url-production>/api/cron_15min` (ou `/api/cron_daily`,
  planifié une fois par jour vers 12h30 heure serveur — pas besoin des
  répétitions 11h-15h utilisées côté GitHub Actions, un seul call suffit ici
  puisque la cadence est fiable)
- Onglet **Advanced** → **Custom headers** → `Authorization` =
  `Bearer <CRON_SECRET>` (même valeur que les deux autres endroits)

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

**Remplir l'historique 30j sans attendre 30 jours** : les pills "vs 30j" du
dashboard restent vides (`null`) tant que `hist:da`/`hist:fcr`/... ne
contiennent pas assez de jours passés — normal juste après le premier
déploiement, `cron_daily` ne les alimente qu'un jour à la fois. RTE/ENTSO-E
exposant aussi les prix passés, `/api/backfill_historique` récupère les 35
derniers jours et les range dans `hist:<domaine>` (cf. `api/backfill.py`).
Deux contraintes constatées en usage réel, toutes deux gérées :
- **Découpé en fenêtres de 5 jours qui se suivent**, pas un seul appel sur
  35 jours d'un coup : un appel unique sur une fenêtre aussi large ne
  renvoie en pratique que 2-3 jours de points (troncature silencieuse côté
  API ENTSO-E/RTE, pas d'erreur levée).
- **Les 3 domaines (day-ahead, FCR, aFRR capacité) sont récupérés en
  parallèle** (3 threads, I/O-bound) : 7 fenêtres × 3 appels strictement
  séquentiels dépassait les 60s max du palier Hobby
  (`FUNCTION_INVOCATION_TIMEOUT`).

La réponse inclut `detail_par_chunk` par domaine (nombre de points par
fenêtre de 5 jours) pour vérifier qu'aucune fenêtre n'est anormalement
vide :

```powershell
curl.exe -H "Authorization: Bearer <secret>" https://<url>/api/backfill_historique
```

Endpoint protégé par le même `CRON_SECRET`, mais **pas** ajouté au
déclencheur GitHub Actions (`.github/workflows/cron.yml`) — c'est une
opération ponctuelle, à relancer à la main si besoin (ex. après un reset du
KV), pas à chaque run.

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
  l'utilisateur — c'est le seul test qui confirme que `app.py` est bien
  reconnu comme l'entrypoint (build sans erreur *"No python entrypoint
  found"*, et l'onglet **Deployments → Functions** du dashboard montre une
  seule fonction Python servant `/api/*`).
- Fréquence réelle des cron Vercel sur le palier Hobby (cf. section 3) — non
  applicable ici puisque GitHub Actions est l'unique déclencheur.
