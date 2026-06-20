# OS_ISOLATION — agent-scribe-graphify V2

Le coeur MCP reste cross-platform : Linux, macOS et Windows.

L'isolation stricte est optionnelle. Elle dépend du système et doit toujours demander confirmation avant installation.

## Assistant d'installation

Afficher le plan détecté :

```bash
python3 .agent/scripts/isolation_setup.py
```

Installer après confirmation :

```bash
python3 .agent/scripts/isolation_setup.py --install
```

## Linux

Backend recommandé : bubblewrap. Le launcher strict Linux actuel (`agent_sandbox.py`) utilise bubblewrap.

Debian/Ubuntu : installer le paquet `bubblewrap` avec le gestionnaire de paquets.

Avantages : rapide, léger, local.

Inconvénients : dépend de la configuration des namespaces du système et du paquet `bubblewrap`.

## macOS

Le coeur MCP fonctionne sans couche stricte.

Pour une isolation plus forte, préférer un runtime conteneur/VM comme Colima + Docker. La commande Apple `sandbox-exec` existe mais elle est dépréciée, donc elle ne doit pas être la base principale.

Avantages : isolation robuste via VM/conteneurs.

Inconvénients : plus lourd et intégration MCP proxy à adapter. Il n'existe pas encore de launcher macOS strict équivalent au wrapper Linux.

## Windows

Le coeur MCP fonctionne sans couche stricte.

Pour une isolation plus forte, utiliser Windows Sandbox avec un fichier `.wsb` et un dossier projet mappé en lecture seule.

Avantages : environnement Windows jetable.

Inconvénients : demande une édition Windows compatible, la virtualisation et parfois un redémarrage. Il n'existe pas encore de launcher Windows strict équivalent au wrapper Linux.

## Suppression contrôlée

La suppression réelle d'un fichier doit passer par MCP :

```text
workflow_next → claim_resource → file_hash → delete_resource → release_claim → finish_task
```

`delete_resource` refuse d'agir sans phrase de confirmation exacte fournie par l'utilisateur.

Phrase exacte attendue :

```text
DELETE chemin/relatif/du/fichier
```
