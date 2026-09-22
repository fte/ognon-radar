# Apple Container — conversion de `docker-compose.yml`

Ce dossier contient l'équivalent **Apple Container** (CLI `container`, GitHub
[apple/container](https://github.com/apple/container)) de la pile décrite dans
`docker-compose.yml`.

> ⚠️ **Le CLI `container` d'Apple ne sait pas lire les fichiers
> `docker-compose.yml`** (pas de sous-commande `compose` au moment de la
> rédaction, v1.2.x). La « conversion » est donc un jeu de scripts qui
> reproduisent le même comportement avec des commandes `container` natives.
> Les Dockerfiles restent utilisés tels quels.

## Prérequis

- macOS 26 (Tahoe) ou ultérieur, **Apple silicon** (M1/M2/M3/M4…).
- CLI `container` installé :
  <https://github.com/apple/container/releases>
- Service système démarré :

  ```bash
  container system start
  ```

## Démarrage rapide

Le `Makefile` détecte automatiquement le backend installé : si le CLI
`container` est présent il route vers `scripts/container/*.sh`, sinon vers
`docker-compose` (binaire standalone ou plugin v2 `docker compose`). Si
aucun backend n'est installé, `make` échoue avec des instructions
(`_guard-runtime`). Forcer un backend avec `RUNTIME=docker` ou
`RUNTIME=container`, et vérifier le choix avec `make runtime`.

```bash
# Tout construire puis lancer tor + api
scripts/container/up.sh        # équivalent direct
make up                        # même chose, via auto-détection

# API sur http://localhost:8337/docs — SOCKS5 Tor sur 127.0.0.1:9050

# Arrêter / nettoyer
scripts/container/down.sh            # arrête et supprime les conteneurs
scripts/container/down.sh --clean    # + supprime le réseau darkweb-net

# Logs, état, shell, restart, tests
scripts/container/logs.sh            # logs api (ou: logs.sh tor)
scripts/container/ps.sh
scripts/container/shell.sh
scripts/container/restart.sh
scripts/container/test.sh            # équivalent de `make test`

# Profils compose (crawler / testing)
scripts/container/crawler.sh python dark_crawler.py -u http://<onion> --json
scripts/container/locust.sh          # UI sur http://localhost:8089
```

## Table de correspondance `docker-compose.yml` → `container`

| docker-compose.yml | Apple Container |
|---|---|
| `services.tor` (image `dockurr/tor`) | `container run -d --name darkweb-tor --network darkweb-net -p 9050:9050 … dockurr/tor:latest` (script `up.sh`) |
| `services.api` (`build: .`) | `container build -t darkweb-api -f Dockerfile .` puis `container run -d --name darkweb-api -p 8337:8000 --ulimit nofile=4096:8192 …` |
| `services.crawler` (profil) | `scripts/container/crawler.sh …` (lancement one-shot `--rm`) |
| `services.locust` (profil) | `scripts/container/locust.sh` |
| `networks.darkweb-net` (bridge) | `container network create darkweb-net` (plugin `container-network-vmnet`, NAT) |
| `environment:` | `-e KEY=value` / `--env-file` |
| `ports:` | `-p HOST:CONTAINER` |
| `volumes:` (bind mounts) | `-v /chemin/mac:/chemin/linux` |
| `ulimits:` | `--ulimit nofile=4096:8192` |
| `entrypoint:` | `--entrypoint /bin/sh` + arguments |
| `command:` | arguments passés après le nom d'image |
| `depends_on: tor: service_healthy` + `healthcheck:` | boucle d'attente `wait_for_tor()` dans `lib.sh` (curl via SOCKS `127.0.0.1:9050`) |
| `container_name:` | `--name darkweb-api` (etc.) |
| `restart: unless-stopped` | **non supporté** — voir limites |
| `profiles:` | scripts dédiés (`crawler.sh`, `locust.sh`) |

## Différences / limites connues

1. **Pas de `restart: unless-stopped`.** Le CLI `container` ne propose pas de
   politique de redémarrage (feature request en cours côté Apple). En test,
   relancer `scripts/container/up.sh` (idempotent : il redémarre les
   conteneurs arrêtés). Pour un usage plus durable, un agent launchd peut
   superviser `up.sh` :

   ```xml
   <!-- ~/Library/LaunchAgents/com.ognon-radar.container.plist -->
   <?xml version="1.0" encoding="UTF-8"?>
   <plist version="1.0">
   <dict>
     <key>Label</key><string>com.ognon-radar.container</string>
     <key>ProgramArguments</key>
     <array>
       <string>/bin/bash</string>
       <string>/chemin/vers/scripts/container/up.sh</string>
       <string>--no-build</string>
     </array>
     <key>RunAtLoad</key><true/>
     <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
   </dict>
   </plist>
   ```

2. **Pas de `healthcheck` ni de `depends_on`.** Remplacés par des boucles
   d'attente dans `up.sh` (Tor d'abord, API ensuite).

3. **Pas de `:ro`, et pas de montage de fichiers sources.** Le `:ro` de
   `./config.yaml:/app/config.yaml:ro` (option Docker) n'est pas garanti par
   le CLI `container` — il est donc omis. Surtout, le CLI v1.2.x **ignore
   silencieusement TOUS les `-v` si l'un d'eux a un FICHIER comme source**
   (seuls les répertoires sont supportés) : le conteneur démarre alors avec
   la copie figée dans l'image, sans aucun montage. C'est pourquoi `up.sh`
   ne monte que des répertoires — `config.yaml` est déjà couvert par le
   montage `$REPO_ROOT:/app` (le fichier est dans le dépôt, et l'application
   ne fait que le lire).

4. **Limite de 16 Ko sur les Dockerfiles** (bug
   [#735](https://github.com/apple/container/issues/735)) — sans impact ici.

5. **Pas de souci de registre** : `container run` tire automatiquement les
   images `dockurr/tor` et `locustio/locust` depuis Docker Hub.

6. **Utilisateur** : le Dockerfile crée l'utilisateur `apiuser` (uid 1000).
   Docker honore le `USER` de l'image ; `container` démarre en root par défaut,
   donc `up.sh` passe `--user 1000` pour reproduire le comportement. Les tests
   (`test.sh`) tournent en root : pytest écrit des `.pyc` / `.pytest_cache`
   dans le dépôt monté (`/app`), et contrairement à Docker Desktop, le
   virtiofs d'Apple n'assouplit pas forcément les permissions hôte pour un
   uid arbitraire.

7. **Permissions des répertoires de données.** `ensure_data_dirs()` crée
   `ognon-jobs/` et `crawler_output/` et leur applique `chmod a+rwX` pour que
   l'uid 1000 du conteneur puisse y écrire (bases SQLite, captures WARC) sans
   dépendre de l'assouplissement de permissions de Docker Desktop.

8. **DNS inter-conteneurs : non résolu par vmnet (constaté sur le CLI
   v1.2.2).** Sur `darkweb-net` (`container-network-vmnet`), les conteneurs ne
   se résolvent **pas** par leur nom — seule la résolution DNS externe passe
   par la passerelle (`.1` du sous-réseau). Les scripts contournent ce
   manque : ils passent l'IP réelle de tor aux conteneurs api/crawler via les
   variables d'environnement `TOR_PROXY` et `TOR_CONTROL_HOST`, que
   `config.py` et `core/screenshot.py` préfèrent aux valeurs YAML `tor:9050`
   (celles-ci ne fonctionnent que sous le DNS intégré de Docker). Sous
   docker-compose, rien ne change : le DNS de Docker résout `tor` et `api`
   comme avant.

9. **Quirk `container run` (v1.2.x)** : la création d'un conteneur peut
   échouer avec « container already exists » juste après l'avoir créé.
   `run_detached()` dans `lib.sh` traite ce cas comme un succès si le
   conteneur existe bien ensuite.

10. **Checks d'existence** : `container run --name X` utilise X comme ID du
    conteneur, donc les scripts utilisent `container list -a -q` (et
    `container network list -q`) pour détecter les ressources existantes.

## Ce qui n'est pas touché

Le `docker-compose.yml`, les `Dockerfile`, le `Makefile` et les scripts
existants (`scripts/deploy_live.sh`, `scripts/check_playwright.py`) sont
conservés à l'identique — ce dossier s'ajoute en parallèle pour tester le
runtime Apple sans casser le workflow Docker.
