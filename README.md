<p align="center">
  <img src="media/logo_vega.png" alt="Vega Toolbox" width="140">
</p>

<h1 align="center">Vega Toolbox</h1>

<p align="center">
  <strong>Boîte à outils interne pour le support et la migration VEGA6</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-5.0.2-blue?style=flat-square" alt="Version">
  <img src="https://img.shields.io/badge/plateforme-Windows%207%20%E2%86%92%2011-0078d6?style=flat-square" alt="Windows">
  <img src="https://img.shields.io/badge/python-3.12%20%2F%203.7-3776ab?style=flat-square" alt="Python">
  <img src="https://img.shields.io/badge/UI-Tkinter-orange?style=flat-square" alt="Tkinter">
  <img src="https://img.shields.io/badge/build-PyInstaller%20onefile-green?style=flat-square" alt="PyInstaller">
</p>

<p align="center">
  <a href="#vue-densemble">Vue d'ensemble</a> ·
  <a href="#modules">Modules</a> ·
  <a href="#installation">Installation</a> ·
  <a href="#auto-mise-à-jour">Auto-MAJ</a> ·
  <a href="#développement">Développement</a> ·
  <a href="#support">Support</a>
</p>

---

## Vue d'ensemble

Vega Toolbox centralise toutes les opérations de support et de migration VEGA6 dans une interface unique style Windows XP. Conçu pour le support interne Zucchetti, il regroupe en un seul exécutable :

- la **migration VEGA5 → VEGA6** (sauvegarde, retour arrière, gestion des fichiers verrouillés / éléments manquants avec rollback) ;
- la **gestion du partage `\\PC\ImpressionsVega$`** ;
- la **configuration du pare-feu Windows** (RetailForce + HFSQL) ;
- la **bascule DHCP / IP fixe** sur les cartes réseau ;
- le **nettoyage des fichiers obsolètes** dans la racine Vega ;
- l'**installation / mise à jour du moteur HFSQL** Client/Serveur PCSoft en silencieux ;
- la **sauvegarde CerberIT** (Kiwi Backup en marque blanche) : installation silencieuse, enregistrement, sélection des dossiers, planning et diagnostic ;
- un **rapport Info PC** (matériel + logiciel) et une **vérification de compatibilité matérielle Vega** ;
- un **test SMTP** complet avec bascule OAuth2 automatique pour Microsoft 365 ;
- un **catalogue de téléchargements** (HFSQL, VC++ redist, builds VEGA, modèles mail Syspay…) ;
- l'**installation en un clic** de Notepad++ + JsonTools et de RetailForce.

L'exécutable final est livré ici :

```
dist/vega_toolbox.exe
```

---

## Modules

### Accueil — tableau de bord

| Section | Rôle |
|---|---|
| Racine Vega détectée | Auto-détection du dossier d'installation, partagée entre modules |
| État du poste | Synthèse des 6 contrôles critiques (admin, pare-feu, partage, structure, HFSQL, JSON RetailForce). Bouton **Bases** (dégrisé après « Tout vérifier ») pour cibler une base sur tout l'outil |
| Démarrage rapide | Quatre raccourcis directs : Migration, Téléchargement, Nettoyage, IP fixe |
| Outils complémentaires | Installer Notepad++ + JsonTools · Installer RetailForce · Mettre à jour RetailForce |

### Modules métier

| Module | Description |
|---|---|
| **Migration** | Vérification des prérequis, migration VEGA5 → VEGA6 avec sauvegarde, retour arrière, pilotage du service HFSQL, sélection de base (**Bases ▾**). En cas d'anomalie (fichier verrouillé ou élément manquant), popup : continuer/ignorer les suivants, ou tout annuler (rollback complet) |
| **Impressions Vega** | Vérifie et répare `C:\ImpressionsVega` + le partage `ImpressionsVega$` (NTFS et SMB) |
| **Pare-feu** | Règles `Service Fiscal RetailForce` (TCP 7678) et `HFSQL Server` (TCP 4900), lecture directe du registre |
| **IP fixe** | Bascule une carte réseau de DHCP vers IP fixe en conservant l'adresse courante |
| **Nettoyage Vega** | Range les fichiers obsolètes de la racine Vega dans `vega.dos` (anciennes DLL/exe, PDF, archives, copies, images, FDJ). Sélection de base (**Bases ▾**) |
| **Envoi mail** | Test SMTP avec auto-bascule OAuth2 si MFA refusé par Microsoft 365 |
| **Téléchargement** | Catalogue intégré : HFSQL Client/Server, Visual C++ redist, Syspay, builds VEGA4/5/6 |
| **HFSQL Serveur** | Installe / met à jour / réinstalle le moteur HFSQL Client/Serveur PCSoft en silencieux. Bouton **Vérifier** (présence du moteur) et **Bases ▾** (choix du répertoire serveur) |
| **Info PC** | Instantané matériel + logiciel (style CPU-Z), moniteur temps réel, **vérification de compatibilité Vega** (détection SSD fiable, y compris en VM) et export d'un rapport HTML |
| **CerberIT** | Installe et configure la sauvegarde CerberIT (Kiwi Backup) : enregistrement par clé de contrat, **sélection visuelle** des disques/dossiers à cocher, exclusions, planning, sauvegarde manuelle, **diagnostic** (service, connexion port 4443, journal) et désinstallation |

---

## Deux builds, un seul code source

Vega Toolbox est publie en deux saveurs issues du **meme depot**. Une couche de
compatibilite (`vega_backend/_compat.py`) detecte la version de Windows a
l'execution et route vers les commandes adaptees.

| Binaire | Cible | Interpreteur |
|---|---|---|
| `vega_toolbox.exe` | Windows 8.1+ / Server 2012 R2+ | Python 3.12 |
| `vega_toolbox_legacy_x86.exe` | **Windows 7 SP1 -> Server 2012** (32 et 64 bits) | Python 3.7 |
| `vega_toolbox_legacy_x64.exe` | idem, 64 bits | Python 3.7 |

Les postes anciens ne disposent ni de PowerShell 3.0, ni des modules `Net*` /
`Smb*` / `Storage`. Le build legacy retombe donc sur les equivalents
historiques, tous testes :

| Fonction | Windows 8.1+ | Windows 7 |
|---|---|---|
| Inventaire materiel | `Get-CimInstance` | `Get-WmiObject` |
| Serialisation JSON | `ConvertTo-Json` | serialiseur .NET 3.5 |
| Regles pare-feu | `New-NetFirewallRule` | `netsh advfirewall` |
| Partage SMB | `New-SmbShare` | `net share` + `Win32_Share` |
| Ports en ecoute | `Get-NetTCPConnection` | `netstat -ano` |

Le build legacy **embarque l'Universal C Runtime** (44 DLL du SDK Windows) :
le correctif KB2999226 n'est donc pas requis. Chaque saveur possede son propre
canal de mise a jour : un poste Windows 7 ne peut pas telecharger le binaire
moderne, qui ne demarrerait pas.

> **Seul prerequis restant sur un Windows 7 nu** : le .NET Framework 4.x, pour
> l'ecran de connexion (il repose sur une DLL .NET). En son absence,
> l'application demarre et affiche un message explicite.

### Construire le build legacy

```bash
pip install -r requirements-legacy.txt
python -m PyInstaller VegaV6_Migration_legacy.spec --noconfirm --clean
```

A lancer avec **Python 3.8** (x86 ou x64) : le nom de sortie suit
l'architecture de l'interpreteur.

---

## Installation

### Pour les utilisateurs

Téléchargez `vega_toolbox.exe` et lancez-le. L'application demandera une élévation administrateur (UAC) car la plupart des modules le nécessitent.

> **Note** : à la première exécution, l'application ajoute une exclusion Windows Defender sur ses fichiers temporaires d'extraction (`%TEMP%\_MEI*`) pour neutraliser le faux-positif `Failed to load Python DLL` lors des auto-mises à jour.

### Pour les développeurs

```bash
git clone https://github.com/dazcookiez/toolbox.git
cd toolbox
pip install -r requirements.txt
python script.py
```

---

## Auto-mise à jour

Au démarrage, Vega Toolbox lit `latest.json` dans la dernière release du dépôt public des binaires ([dazcookiez/toolbox_releases](https://github.com/dazcookiez/toolbox_releases/releases)) pour comparer la version locale à la dernière publiée. Si une mise à jour est disponible, une fenêtre de confirmation s'affiche après quelques secondes.

Le pipeline de mise à jour :

1. Téléchargement du nouvel exécutable dans `%TEMP%`, puis vérification de son empreinte **SHA-256** contre celle publiée dans `latest.json` (refus si elle diffère).
2. Le processus courant se termine immédiatement (`os._exit`).
3. Un script batch détaché attend, force-kill l'exécutable courant résiduel via `taskkill /f`, déplace le nouveau binaire, force la lecture complète pour finaliser le scan Windows Defender, puis relance l'application.

L'exclusion Defender appliquée au démarrage rend ce processus silencieux et sans popup.

---

## Mode Tutoriel

Un bouton **Tutoriel** dans l'en-tête lance une visite guidée en 26 étapes : overlay sombre semi-transparent, spotlight jaune autour du widget pertinent, callout style XP avec navigation Précédent / Suivant / Quitter. Le tutoriel switche automatiquement de module pour présenter chaque écran et chacun de ses boutons.

Échap quitte à tout moment. La visite suit le déplacement et le redimensionnement de la fenêtre.

---

## Raccourcis clavier

| Raccourci | Action |
|---|---|
| `F1` | Écran Infos / support |
| `F5` | Rafraîchit le module courant |
| `F12` | Maximise / restaure la fenêtre |
| `Ctrl+L` | Déplie / replie le tiroir journal |
| `Ctrl+1` … `Ctrl+9` | Navigation directe entre les modules |
| `Échap` | Ferme une boîte de dialogue ou le tutoriel |

---

## Structure du projet

```
Vega-Migration-tool/
├── script.py                    # Point d'entrée (injection truststore + lancement GUI)
├── version_info.txt             # Métadonnées Windows embarquées dans l'exe
├── VegaV6_Migration.spec        # Recette PyInstaller (onefile, uac_admin, icône)
├── vega_security.py             # Login (règle !…! >= 4 chars) + persistance config
├── lib/                         # DLL .NET embarquées (CryptoTools, Newtonsoft.Json)
├── vega_gui/                    # Couche présentation Tkinter
│   ├── app.py                   # Classe VegaToolApp (Tk root, sidebar, header, lifecycle)
│   ├── theme.py                 # Constantes : couleurs XP, version, fonts, icônes
│   ├── resources.py             # Chargement images / icônes (compatible PyInstaller _MEIPASS)
│   ├── widgets.py               # ScrollableHost, Sidebar, LogDrawer, tool_button…
│   ├── _tutorial.py             # Mode Tutoriel : overlay + spotlight + 26 étapes
│   ├── _task_runner.py          # Mixin asynchrone pour les opérations longues
│   ├── _status_bar.py           # Mixin barre d'état + indicateur d'activité
│   ├── tabs/                    # Un module = un fichier
│   │   ├── migration.py
│   │   ├── impressions.py
│   │   ├── firewall.py
│   │   ├── fixed_ip.py
│   │   ├── clean.py
│   │   ├── smtp_test.py
│   │   ├── download.py
│   │   ├── pc_info.py
│   │   ├── hfsql.py
│   │   └── cerberit.py
│   └── views/                   # Vues spéciales (Accueil, Login, Support)
└── vega_backend/                # Couche métier (sans Tkinter)
    ├── _common.py               # Helpers + lecture pare-feu via registre
    ├── migration.py             # Pipeline migration + sauvegarde + rollback + gestion anomalies
    ├── impressions.py           # Création / réparation partage SMB
    ├── firewall.py              # Règles pare-feu RetailForce + HFSQL
    ├── fixed_ip.py              # Bascule DHCP / IP fixe
    ├── clean.py                 # Analyse + déplacement fichiers obsolètes
    ├── smtp.py                  # Test SMTP + bascule OAuth2 (MSAL)
    ├── downloads.py             # Catalogue + téléchargement parallèle
    ├── notepad.py               # Notepad++ + plugin JsonTools
    ├── retailforce.py           # Désinstall + install RetailForce
    ├── pc_info.py               # Collecte matérielle/logicielle (WMI + Get-PhysicalDisk)
    ├── compatibility.py         # Vérification des prérequis matériels Vega (dont détection SSD)
    ├── hfsql_installer.py       # Installation / MAJ / désinstallation moteur HFSQL PCSoft
    ├── cerberit.py              # Sauvegarde CerberIT/Kiwi : install /S, register, kiwi.conf, diagnostic
    ├── audit.py                 # Journal d'audit des actions (export HTML/JSON)
    ├── updater.py               # Auto-mise à jour via GitHub Releases (+ SHA-256)
    └── defender.py              # Exclusion Windows Defender pour les _MEI*
```

---

## Développement

### Build de l'exécutable

```bash
python -m PyInstaller VegaV6_Migration.spec --noconfirm --clean
```

Le binaire généré (~26 Mo) se trouve dans `dist/vega_toolbox.exe` (nom fixe : l'auto-mise à jour en dépend). Il embarque tkinter, les images, les icônes, MSAL, truststore, PyYAML et tous les modules métier.

### Convention de versioning

Format `X.Y.Z` (pas SemVer strict) :

- `X` — refonte majeure ;
- `Y` — nouvelle fonctionnalité ou groupe de correctifs ;
- `Z` — correctif mineur.

La version unique est dans `vega_gui/theme.py` (`APP_VERSION`) et `version_info.txt` (métadonnées Windows). Les deux doivent être synchronisées à chaque release (le build GitHub Actions échoue sinon).

### Publier une release

Les trois exécutables sont construits par GitHub Actions (`.github/workflows/build.yml`) à chaque pull request et à chaque push sur `main`. Pour publier :

1. Bumper `APP_VERSION` et `version_info.txt`, ajouter la ligne du journal des versions, merger sur `main`.
2. Onglet **Actions** → **Build** → **Run workflow** sur `main`, cocher **publish**, saisir les notes de version.
3. Le workflow publie `vX.Y.Z` (les 3 exe + `latest.json` avec leurs SHA-256) sur `dazcookiez/toolbox_releases`, puis crée le tag `vX.Y.Z` sur ce dépôt.

Prérequis (une seule fois) : un secret `RELEASES_TOKEN` dans ce dépôt — jeton GitHub *fine-grained* avec le droit **Contents: Read and write** sur `dazcookiez/toolbox_releases`.

### Convention de logging

Tous les modules écrivent via `ToolLogger`, thread-safe. Les messages sont consommés par le thread Tk via `queue.Queue` puis affichés dans le tiroir journal.

```python
self.logger.info("Action démarrée")
self.logger.warn("Cas de bord détecté")
self.logger.error("Échec de l'opération")
```

---

## Métadonnées Windows

Visibles via clic-droit → Propriétés → Détails sur `vega_toolbox.exe` :

| Champ | Valeur |
|---|---|
| Description du fichier | vega toolbox |
| Société | Zucchetti |
| Nom du produit | Vega Toolbox |
| Version du fichier | 5.0.2.0 |
| Version du produit | 5.0.2 |
| Copyright | © Zucchetti |
| Langue | Français (France) |
| Nom de fichier original | vega_toolbox.exe |

---

## Journal des versions

| Version | Faits marquants |
|---|---|
| **5.0.2** | **Téléchargement** : nouveau canal **VEGA6 BETA** (`VEGA6/PROD/BETA/`) listé à côté de VEGA6 PROD, extraction automatique dans `vega.dos\V6` comme PROD. **Reprise du projet sur GitHub** : auto-mise à jour via GitHub Releases avec vérification SHA-256 (fin de GitLab), nom d'exe fixe `vega_toolbox.exe` rétabli, build et publication des 3 binaires par GitHub Actions. Les postes en 5.0.0 / 5.0.1 doivent être mis à jour une fois manuellement. |
| **5.0.1** | **RetailForce** : installeur mis à jour de 1.11.2 → 1.11.21.7255 (x64 + x86), URL `retailforce.cloud/downloads/Version 1.11.21/...`. **Login** : logique de mot de passe CryptoTools.dll remplacée par règle locale — doit commencer et finir par `!`, minimum 4 caractères (ex. `!xx!`). Suppression des dépendances pythonnet/clr/CryptoTools.dll du build. |
| **5.0.0** | **Support Windows 7 SP1 -> Server 2012** : second binaire (x86 / x64) en Python 3.7, couche de compatibilite, UCRT embarque, canal de mise a jour dedie. Detection automatique d'architecture pour Notepad++/JsonTools et RetailForce. Desinstallation CerberIT en cascade. Journal de crash. Correctifs : ports HFSQL, raccourcis clavier invalides, detection HFSQL 32 bits. |
| **4.0.8** | Nouveau module **CerberIT** (sauvegarde Kiwi Backup) : installation silencieuse, enregistrement par clé de contrat, sélection visuelle des dossiers, planning, diagnostic et désinstallation. Test SMTP plus réactif (timeout 30 s → 10 s). |
| **4.0.7** | Mise à jour du tutoriel guidé (26 étapes) et du README. |
| **4.0.6** | Correctif détection JSON RetailForce : une apiKey de test ne marque plus « à réinitialiser » un vrai client. |
| **4.0.5** | Boutons **Bases** (Accueil / Migration / Nettoyage / HFSQL), bouton **Vérifier** HFSQL, détection SSD fiable (VM incluses), gestion des anomalies de migration avec rollback, bouton **Mettre à jour RetailForce**. |

---

## Support

En cas de problème, contactez l'équipe support interne :

- **Bastien Tillier** — auteur principal
- **David Chalengeas**
- **David Viard**

L'écran **Infos** dans l'application contient les coordonnées détaillées et un bouton de contact direct.

---

<p align="center">
  <sub>© Zucchetti — Outil interne. Distribution publique non prévue.</sub>
</p>
