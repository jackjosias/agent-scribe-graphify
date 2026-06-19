# Workflow MCP: chrome-devtools

Derniere verification locale: 2026-05-29.

Ce workflow documente l'integration de `chrome-devtools-mcp` avec Codex CLI et
Antigravity. Il sert de runbook pour installer, verifier, depanner et utiliser
le MCP Chrome DevTools sans confondre les erreurs de configuration, les limites
du client MCP et les risques de securite.

## Position technique

`chrome-devtools-mcp` donne a un agent des yeux, des mains et des instruments
de mesure dans un vrai Chrome:

- yeux: snapshots d'accessibilite, captures d'ecran, console, reseau;
- mains: navigation, clics, saisie clavier, formulaires, upload, dialogs;
- instruments: Lighthouse, traces performance, emulation responsive, memoire.

L'avantage majeur avec Antigravity est le branchement sur le navigateur integre
ou deja ouvert. L'agent peut reprendre une page existante, voir l'etat reel,
cliquer, remplir les formulaires, lire les erreurs et inspecter les requetes.
Dans ce perimetre navigateur, la prise en main UI est une capacite centrale:
simuler une interaction humaine par souris, clic, double-clic, drag, clavier,
formulaire, navigation et manipulation de page est attendu via le duo
Antigravity Browser Preview + MCP Chrome DevTools. L'agent ne doit pas se
limiter a decrire quoi faire si les outils `take_snapshot`, `click`, `fill`,
`type_text`, `press_key`, `drag` ou `evaluate_script` sont disponibles.
C'est puissant pour debugger une app locale ou un etat deja reproduit. Ce n'est
pas magique: si le navigateur n'est pas lance, si le port CDP n'est pas expose,
ou si le client MCP ne charge pas le serveur, l'agent n'aura aucun controle.


## Capacites obligatoires: MCP + Antigravity Browser

Si `chrome-devtools` MCP est disponible avec le navigateur Antigravity, agent doit considerer que la prise en main active du navigateur est possible. Il peut naviguer, cliquer, remplir des champs, taper au clavier, survoler, glisser-deposer, manipuler interface, attendre des elements, lire la page, lire console, inspecter reseau, prendre captures, lancer Lighthouse, profiler performances, inspecter memoire selon outils exposes, et executer JavaScript dans la page.

Regle anti-timidite: agent ne doit pas repondre "je ne peux pas cliquer", "je ne peux pas interagir" ou "je ne peux pas manipuler le navigateur" sans avoir verifie les outils MCP disponibles et tente une action adaptee. Conclusion operationnelle: si une action est faisable par un humain dans Chrome, agent doit d abord tenter de la faire via `chrome-devtools` MCP avant de declarer une impossibilite.

Interactions humaines simulables:

- clic sur element;
- clic a coordonnees precises quand `click_at` est expose;
- hover / survol souris;
- drag and drop;
- remplissage de champ;
- remplissage de formulaire complet;
- frappe clavier;
- touches speciales: Enter, Tab, Escape, fleches;
- upload de fichier;
- gestion des dialogues navigateur: alert, confirm, prompt;
- scroll via clavier, JavaScript ou manipulation UI selon le cas;
- attente texte, element ou etat de page;
- navigation multi-pages et multi-onglets.

Avant de dire "je ne peux pas", agent doit:

1. verifier que `chrome-devtools` MCP est connecte;
2. lister les pages avec `list_pages`;
3. prendre `take_snapshot` ou `take_screenshot`;
4. tenter action via `click`, `click_at`, `fill`, `type_text`, `press_key`, `drag`, `upload_file`, `handle_dialog`, `evaluate_script` ou outil adapte;
5. seulement apres echec reel, expliquer la limite concrete.

Limites reelles:

- ne pas contourner captcha;
- ne pas deviner mot de passe ou code 2FA;
- ne pas agir sur donnees sensibles sans validation humaine;
- ne pas promettre manipulation OS hors navigateur avec seul Chrome DevTools MCP;
- ne pas garantir qu un site tiers acceptera automatisation;
- ne pas executer action destructive sans permission explicite.

Formule courte: Chrome DevTools MCP + navigateur Antigravity = controle actif du navigateur. Agent peut cliquer, taper, naviguer, inspecter, profiler, auditer, lire le reseau et manipuler UI. Il ne doit pas refuser par timidite: il doit tenter via MCP puis expliquer une limite reelle si outil echoue.

## Sources de reference

- Chrome DevTools for agents:
  https://developer.chrome.com/docs/devtools/agents
- Get started with Chrome DevTools for agents:
  https://developer.chrome.com/docs/devtools/agents/get-started
- Depot officiel:
  https://github.com/ChromeDevTools/chrome-devtools-mcp
- Reference officielle des outils:
  https://github.com/ChromeDevTools/chrome-devtools-mcp/blob/main/docs/tool-reference.md
- Guide MCP Codex:
  la commande locale `codex mcp --help` est la source pratique sur cette machine.

## Prerequis

- Node.js LTS et npm.
- Google Chrome stable ou Chrome for Testing.
- Un client compatible MCP: Codex CLI, Antigravity, Gemini CLI, Claude Code,
  Cursor, Copilot, etc.
- Pour connecter une session existante: Chrome doit exposer le Chrome DevTools
  Protocol sur `127.0.0.1:9222` ou via `--autoConnect` selon le setup.

Verification rapide:

```bash
node -v
npm -v
google-chrome --version
curl -s http://127.0.0.1:9222/json/version
```

Si `curl` ne renvoie pas un JSON avec `webSocketDebuggerUrl`, le MCP ne peut pas
se connecter a cette session Chrome via `--browser-url`.

## Installation Codex CLI

Commande officielle:

```bash
codex mcp add chrome-devtools -- npx chrome-devtools-mcp@latest
```

Configuration portable recommandee:

```toml
[mcp_servers.chrome-devtools]
command = "chrome-devtools-mcp"
args = [
  "--browser-url=http://127.0.0.1:9222",
  "--no-usage-statistics",
  "--no-performance-crux",
]
startup_timeout_sec = 60
```

Pourquoi ce choix portable:

- le chemin absolu evite les problemes de `PATH` quand Codex ne lance pas un
  shell interactif `nvm`;
- `--browser-url=http://127.0.0.1:9222` force la connexion au Chrome deja ouvert;
- `--no-usage-statistics` coupe la telemetrie du serveur MCP;
- `--no-performance-crux` evite l'appel CrUX pendant les analyses performance.

Commandes de controle:

```bash
codex mcp list
codex mcp get chrome-devtools
codex mcp remove chrome-devtools
```

Dans le TUI Codex, `/mcp` doit afficher `chrome-devtools`, `Status enabled` et
une liste d'outils. `Auth: Unsupported` est normal: ce serveur local ne supporte
pas `codex mcp login/logout` et n'en a pas besoin.

## Integration Antigravity

La documentation officielle indique qu'Antigravity 2.0 integre Chrome DevTools
for agents avec son browser sub-agent. Pour connecter un MCP externe au browser
utilise par Antigravity, le principe est:

```json
{
  "mcpServers": {
    "chrome-devtools": {
      "command": "npx",
      "args": [
        "-y",
        "chrome-devtools-mcp@latest",
        "--browser-url=http://127.0.0.1:9222"
      ]
    }
  }
}
```

Points critiques:

- ce mode ne demarre pas Chrome a la place d'Antigravity;
- il faut d'abord ouvrir le navigateur integre Antigravity via `Open Browser Preview`
  ou lancer la session Chrome cible;
- si le MCP echoue alors que la config pointe vers `127.0.0.1:9222`, la premiere
  action de l'agent est de demander a l'utilisateur d'ouvrir `Open Browser Preview`
  dans Antigravity, puis de relancer la verification;
- si Antigravity utilise un autre port CDP que `9222`, adapter `--browser-url`;
- l'agent controle la page ouverte dans ce navigateur: clics, saisie, navigation,
  drag, clavier, manipulation DOM via script, console, reseau, captures,
  performance.

## Lancer Chrome manuellement avec CDP

Si aucun navigateur Antigravity n'expose le port:

```bash
google-chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/chrome-devtools-mcp-profile
```

Utiliser un profil dedie est preferable. Cela evite de donner a l'agent les
sessions Gmail, Stripe, console cloud, admin prod ou comptes personnels deja
authentifies dans le profil principal.

## Inventaire des outils

La reference officielle actuelle regroupe les outils comme suit. Le client exact
peut exposer seulement un sous-ensemble selon la version, les flags et les
capacites activees.

### Navigation

- `new_page`
- `navigate_page`
- `select_page`
- `list_pages`
- `close_page`
- `wait_for`

### Interaction UI

- `click`
- `click_at` si `--experimentalVision=true`
- `hover`
- `drag`
- `type_text`
- `fill`
- `fill_form`
- `press_key`
- `upload_file`
- `handle_dialog`

### Debug frontend

- `evaluate_script`
- `list_console_messages`
- `get_console_message`
- `take_screenshot`
- `take_snapshot`
- `lighthouse_audit`
- `screencast_start` si `--experimentalScreencast=true`
- `screencast_stop` si `--experimentalScreencast=true`

### Reseau

- `list_network_requests`
- `get_network_request`

Permet de voir les requetes, statuts, erreurs API, headers et bodies selon ce
qui est expose par le navigateur et le client MCP.

### Performance

- `performance_start_trace`
- `performance_stop_trace`
- `performance_analyze_insight`

Usage typique: diagnostiquer LCP, INP, CLS, scripts lourds, hydration lente,
ressources bloquantes ou requetes qui retardent le rendu.

### Responsive et emulation

- `resize_page`
- `emulate`

Permet de tester viewport, mobile, desktop, dark/light mode, reseau ralenti,
user-agent, geolocation et throttling CPU selon les options exposees.

### Memoire

- `take_heapsnapshot`
- `get_heapsnapshot_class_nodes` si `--experimentalMemory=true`
- `get_heapsnapshot_details` si `--experimentalMemory=true`
- `get_heapsnapshot_retainers` si `--experimentalMemory=true`
- `get_heapsnapshot_summary` si `--experimentalMemory=true`

Dans cette session Codex locale, l'outil expose s'appelle
`take_memory_snapshot`; les autres outils de details heap ne sont pas visibles.
Conclusion: toujours verifier `/mcp` ou la liste d'outils de la session active,
pas seulement la documentation generale.

### Extensions Chrome

- `install_extension` si `--categoryExtensions=true`
- `list_extensions` si `--categoryExtensions=true`
- `reload_extension` si `--categoryExtensions=true`
- `trigger_extension_action` si `--categoryExtensions=true`
- `uninstall_extension` si `--categoryExtensions=true`

### Outils tiers et WebMCP

- `list_3p_developer_tools`
- `execute_3p_developer_tool`
- `list_webmcp_tools`
- `execute_webmcp_tool`

Ces categories dependent de la version et des flags. Ne pas promettre leur
presence sans verification dans la session active.

## Workflow d'utilisation agent

1. Verifier la connexion:

```text
list_pages
```

2. Selectionner l'onglet cible:

```text
select_page(pageId)
```

3. Lire l'etat accessible avant de cliquer:

```text
take_snapshot
```

4. Interagir par UID quand possible:

```text
click(uid)
drag(from_uid, to_uid)
fill(uid, value)
fill_form(elements)
type_text("texte")
press_key("Enter")
```

Directive agent: si la page est visible dans `Open Browser Preview` et que le
MCP repond, considerer les interactions humaines de navigateur comme faisables.
Ne pas repondre "je ne peux pas cliquer" sans avoir d'abord tente `take_snapshot`
puis `click`/`fill`/`type_text`/`press_key`/`drag`, ou explique la limite
concrete rencontree.

5. Debugger apres interaction:

```text
list_console_messages
list_network_requests
get_network_request(reqid)
take_screenshot
```

6. Pour la performance:

```text
navigate_page(url)
performance_start_trace(reload=true, autoStop=true)
performance_analyze_insight(insightName, insightSetId)
```

7. Pour le responsive:

```text
resize_page(width=390, height=844)
emulate(viewport="390x844x2,mobile,touch")
```

Regle pratique: preferer `take_snapshot` pour agir avec precision, puis
`take_screenshot` pour verifier le rendu visuel.

## Problemes rencontres ici

### `Auth: Unsupported`

Symptome:

```text
chrome-devtools Status enabled Auth Unsupported
```

Diagnostic: ce n'est pas un refus. Le MCP est local en transport `stdio`; il ne
supporte pas le login Codex, donc l'auth est marquee unsupported. Si les outils
sont listes, le serveur est charge.

### Session Codex ouverte avant la config

Symptome: `codex mcp list` montre le serveur, mais l'agent courant dit ne pas
voir les outils.

Correction: fermer completement le TUI Codex, relancer `codex`, puis verifier
`/mcp`.

### Mauvais fichier de configuration

Erreur precedente: donner des chemins Cline/Gemini/Antigravity comme source de
verite pour Codex CLI.

Correction: pour Codex CLI, la configuration utilisateur est dans
`~/.codex/config.toml`; la commande de reference est `codex mcp`.

### Port CDP absent ou mauvais

Symptome:

```bash
curl -s http://127.0.0.1:9222/json/version
```

ne renvoie rien ou refuse la connexion.

Corrections:

- lancer Chrome avec `--remote-debugging-port=9222`;
- si le setup vise Antigravity, demander d'abord a l'utilisateur d'ouvrir
  `Open Browser Preview`; sans cette fenetre, il n'y a souvent aucun navigateur
  CDP disponible pour le MCP;
- ajuster `--browser-url` si le port n'est pas `9222`;
- verifier qu'un firewall ou namespace reseau ne coupe pas `127.0.0.1`.

### Node/NPM introuvable dans le client MCP

Symptome: marche dans un terminal interactif, echoue dans Codex.

Correction: utiliser le chemin absolu du binaire installe, comme:

```toml
command = "chrome-devtools-mcp"
```

### `Transport closed` cote outil Codex, mais Chrome/CDP vivant

Symptome:

```text
tool call failed for `chrome-devtools/list_pages`
Caused by:
    Transport closed
```

Diagnostic: le transport MCP `stdio` attache a la session Codex courante est
ferme. Ce n est pas automatiquement une panne de Chrome, du port CDP, du binaire
`chrome-devtools-mcp`, ni de la configuration `codex mcp`.

Verification de separation des couches:

```bash
codex mcp get chrome-devtools
curl -s http://127.0.0.1:9222/json/version
chrome-devtools-mcp --version
```

Si `curl` renvoie un `webSocketDebuggerUrl` et que le binaire repond, Chrome/CDP
et le serveur MCP sont sains. Le probleme restant est le pont client Codex deja
ouvert. Dans ce cas, `codex mcp remove/add`, `npm install` ou `pkill` ne
recollent pas le transport a chaud. La correction normale est de fermer puis
relancer completement Codex/Antigravity, puis verifier `/mcp` et `list_pages`.

Reflexe de secours valide en session: lancer temporairement le binaire global et
lui parler en JSON-RPC pour piloter Chrome via CDP/WebSocket, uniquement comme
pont de diagnostic ou de smoke test quand outil MCP natif est ferme.

Handshake minimal:

```bash
chrome-devtools-mcp \
  --browser-url=http://127.0.0.1:9222 \
  --no-usage-statistics \
  --no-performance-crux
```

Puis envoyer sur stdin:

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"manual-probe","version":"0"}}}
{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}
{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}
```

Si `tools/list` retourne `list_pages`, `evaluate_script`, `take_snapshot`,
`take_screenshot`, etc., le serveur MCP est fonctionnel. Pour appeler un outil:

```json
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"list_pages","arguments":{}}}
```

### Fallback avance: second navigateur CDP dedie

Decouverte locale 2026-06-10: quand le transport Codex reste `Transport closed`
alors que `chrome-devtools-mcp` et Chrome/CDP sont sains, ouvrir une seconde
surface navigateur isolee peut debloquer les tests sans redemarrer le host.
Cela ne recolle pas l'outil `mcp__chrome_devtools` deja ferme, mais fournit un
nouveau port CDP propre pour un probe MCP manuel ou un fallback CDP.

Commande testee:

```bash
antigravity --new-window --user-data-dir /tmp/antigravity-second-session .
google-chrome \
  --remote-debugging-port=9223 \
  --user-data-dir=/tmp/antigravity-browser-cdp-9223 \
  --no-first-run \
  --no-default-browser-check \
  http://localhost:3000/equity/technical-analysis
```

Si `antigravity` ne peut pas ouvrir directement son Browser Preview, le Chrome
dedie sur `9223` suffit comme deuxieme surface navigateur. Utiliser un profil
isole evite de donner acces aux onglets personnels ou aux sessions sensibles.

Verification:

```bash
curl -s http://127.0.0.1:9223/json/version
chrome-devtools-mcp \
  --browser-url=http://127.0.0.1:9223 \
  --no-usage-statistics \
  --no-performance-crux
```

Resultat attendu du probe manuel:

- `tools/list` retourne `list_pages`, `take_snapshot`, `evaluate_script`;
- `tools/call` avec `list_pages` voit l'onglet local cible;
- le navigateur de secours reste utilisable meme si l'outil natif de la
  session Codex courante continue de renvoyer `Transport closed`.

Regle de decision:

1. Si `mcp__chrome_devtools` repond, utiliser l'outil natif.
2. Si `mcp__chrome_devtools` dit `Transport closed`, mais `curl :9222` et le
   probe manuel sont OK, ne pas perdre de temps a reinstall/readd MCP.
3. Ouvrir une seconde surface isolee sur un port libre (`9223`, puis `9224`
   si besoin), probe manuel, continuer les smoke tests.
4. Redemarrer Codex/Antigravity plus tard pour recuperer l'outil natif.

Conclusion operationnelle: ne pas confondre `Transport closed` du client Codex
avec une panne CDP. Si le WebSocket DevTools est vivant, il peut servir de voie
de secours pour les tests navigateur jusqu au redemarrage du host Codex.

### Bubblewrap Codex Linux

Symptome observe:

```text
bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted
```

Diagnostic: probleme de sandbox Linux Codex, pas du MCP Chrome DevTools. Codex
peut echouer a executer certaines commandes shell sandboxees si les user
namespaces ou la creation loopback sont bloquees par le systeme.

Action: ne pas melanger ce probleme avec `/mcp`. Verifier separement:

```bash
codex mcp list
curl -s http://127.0.0.1:9222/json/version
```

Si ces deux commandes sont bonnes, le MCP Chrome est operationnel meme si le
sandbox shell a besoin d'un reglage systeme ou d'une execution approuvee.

## Securite operationnelle

Ce MCP expose le contenu du navigateur a l'agent. L'agent peut lire, inspecter,
debugger et modifier les pages accessibles, puis agir avec les sessions deja
authentifiees.

Regles:

- ne pas connecter le MCP a un profil Chrome personnel charge de secrets;
- eviter Gmail, Stripe, banques, consoles cloud et admin production;
- utiliser un profil dedie avec `--user-data-dir=/tmp/chrome-devtools-mcp-profile`;
- commencer par `list_pages` et fermer les onglets sensibles;
- demander validation humaine avant toute action irreversible dans une page;
- privilegier les environnements locaux, staging ou comptes de test;
- ne jamais mettre de tokens dans `evaluate_script`, les URLs ou les captures.

## Ce que ce MCP permet vraiment

Il permet de faire du test et du debug frontend avec le navigateur reel:

- ouvrir l'app locale;
- cliquer comme un utilisateur;
- remplir des formulaires;
- lire les erreurs React/Next dans la console;
- inspecter les appels API;
- capturer des screenshots;
- lancer des audits Lighthouse;
- profiler les performances;
- tester mobile/desktop;
- analyser certains problemes memoire;
- verifier des regressions visuelles simples.

Il ne remplace pas:

- les tests unitaires;
- les tests e2e reproductibles;
- les validations serveur;
- la revue de securite;
- la discipline de ne pas agir sur une session sensible.

## Check final avant de dire "le MCP marche"

```bash
codex mcp list
codex mcp get chrome-devtools
curl -s http://127.0.0.1:9222/json/version
```

Puis dans Codex:

```text
/mcp
list_pages
```

Etat attendu local:

- `chrome-devtools` est `enabled`;
- `Auth: Unsupported` est accepte comme normal;
- `curl` renvoie `webSocketDebuggerUrl`;
- `list_pages` renvoie les onglets Chrome;
- les interactions se font via `take_snapshot` puis `click`/`fill`/`press_key`.

Etat attendu fallback second navigateur:

- `curl -s http://127.0.0.1:9223/json/version` renvoie `webSocketDebuggerUrl`;
- le probe manuel du binaire `chrome-devtools-mcp --browser-url=http://127.0.0.1:9223` expose les outils;
- `list_pages` voit l'onglet local cible sur le port de secours.
