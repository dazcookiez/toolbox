"""Installation automatique de Notepad++ + plugin JsonTools.

Strategie :
  - Recupere la derniere version via l'API GitHub (releases/latest), pas de version codee
    en dur ; le binaire est donc toujours la plus recente disponible
  - Architecture DETECTEE automatiquement (x64 / x86) : on telecharge
    l'installeur correspondant, puis le ZIP JsonTools de la MEME architecture —
    un plugin depareille serait ignore par Notepad++ au chargement.
  - Run silencieux (`/S` flag NSIS), puis extraction du plugin depuis le fork
    molsonkiko de JsonTools : `JsonTools.dll` + traductions sont deposes dans
    `<NPP>\\plugins\\JsonTools\\`

Pre-requis :
  - L'app doit etre lancee admin (verifie via is_admin)
  - Connexion internet (api.github.com + github.com/.../releases/...)
"""
import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

from ._common import (
    CREATE_NO_WINDOW,
    DOWNLOAD_USER_AGENT,
    OperationError,
    is_admin,
)
from ._compat import os_architecture


GITHUB_API = "https://api.github.com"
NPP_REPO = "notepad-plus-plus/notepad-plus-plus"
JSONTOOLS_REPO = "molsonkiko/JsonToolsNppPlugin"


class NotepadInstallerManager:
    def __init__(self, logger):
        self.logger = logger

    # ------------------------- API discovery -------------------------

    def _gh_latest(self, repo):
        url = f"{GITHUB_API}/repos/{repo}/releases/latest"
        req = Request(
            url,
            headers={
                "User-Agent": DOWNLOAD_USER_AGENT,
                "Accept": "application/vnd.github+json",
            },
        )
        try:
            with urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            raise OperationError(f"GitHub API injoignable pour {repo} : {exc}") from exc

    def latest_npp(self, architecture=None):
        # architecture : "x64" | "x86" | None (= detectee automatiquement).
        # Nommage des assets Notepad++ : la version 32 bits n'a AUCUN suffixe
        # ("npp.X.Y.Z.Installer.exe"), les autres portent ".x64" / ".arm64".
        arch = architecture or os_architecture()
        info = self._gh_latest(NPP_REPO)
        installer = None
        for asset in info.get("assets", []):
            lower = asset.get("name", "").lower()
            if not (lower.endswith(".exe") and "installer" in lower):
                continue
            if "arm64" in lower:
                continue
            if ("x64" in lower) == (arch == "x64"):
                installer = asset
                break
        if not installer:
            raise OperationError(
                f"Aucun installeur {arch} trouve dans la derniere release Notepad++ "
                f"({info.get('tag_name', '?')})."
            )
        return {
            "version": (info.get("tag_name") or "?").lstrip("v"),
            "tag": info.get("tag_name"),
            "installer_name": installer["name"],
            "installer_url": installer["browser_download_url"],
            "size": installer.get("size"),
        }

    def latest_jsontools(self, architecture=None):
        arch = architecture or os_architecture()
        info = self._gh_latest(JSONTOOLS_REPO)
        # Le plugin DOIT avoir la meme architecture que Notepad++, sinon il est
        # ignore au chargement : la meme valeur 'arch' pilote les deux.
        zip_asset = None
        for asset in info.get("assets", []):
            lower = asset.get("name", "").lower()
            if lower.endswith(".zip") and arch in lower:
                zip_asset = asset
                break
        if not zip_asset:
            raise OperationError(
                f"Aucune release {arch} trouvee pour JsonTools "
                f"({info.get('tag_name', '?')})."
            )
        return {
            "version": (info.get("tag_name") or "?").lstrip("v"),
            "zip_name": zip_asset["name"],
            "zip_url": zip_asset["browser_download_url"],
            "size": zip_asset.get("size"),
        }

    # ------------------------- Install detection ---------------------

    def detect_npp_dir(self):
        # Cherche d'abord les emplacements standards x64 puis x86.
        candidates = [
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Notepad++",
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Notepad++",
            Path(r"C:\Program Files\Notepad++"),
        ]
        for path in candidates:
            if (path / "notepad++.exe").exists():
                return path
        return None

    def installed_version(self):
        # Lit la version depuis les ressources de notepad++.exe via PowerShell (rapide, fiable).
        npp_dir = self.detect_npp_dir()
        if not npp_dir:
            return None
        exe = npp_dir / "notepad++.exe"
        try:
            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    f"(Get-Item '{exe}').VersionInfo.FileVersion",
                ],
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=CREATE_NO_WINDOW,
            )
            return (result.stdout or "").strip() or None
        except Exception:
            return None

    # ------------------------- Action ------------------------------

    def install_npp_and_jsontools(self, on_progress=None):
        # Pipeline complet, atomique cote logique : on s'arrete a la 1re erreur reportee.
        if not is_admin():
            raise OperationError("L'installation de Notepad++ requiert un lancement en administrateur.")

        notify = on_progress or (lambda _step, _msg: None)

        # Architecture detectee UNE SEULE FOIS et transmise a l'editeur comme au
        # plugin : un JsonTools d'architecture differente serait ignore par
        # Notepad++ au chargement.
        arch = os_architecture()
        notify("arch", f"Architecture detectee : {arch}.")

        # 1) Recherche derniere version NPP
        notify("npp_info", "Recherche de la derniere version de Notepad++...")
        npp = self.latest_npp(arch)
        notify("npp_info", f"Notepad++ {npp['version']} trouve ({npp['installer_name']}).")

        # 2) Telechargement installeur
        notify("npp_download", f"Telechargement {npp['installer_name']}...")
        installer_path = self._download_to_temp(npp["installer_url"], npp["installer_name"])
        notify("npp_download", f"Telecharge : {installer_path}")

        # 3) Install silencieux
        notify("npp_install", "Installation silencieuse en cours (peut prendre 10-30s)...")
        self._run_silent_installer(installer_path)

        npp_dir = self.detect_npp_dir()
        if not npp_dir:
            raise OperationError(
                "Notepad++ a ete installe mais le dossier d'installation est introuvable. "
                "Verifiez Program Files."
            )
        notify("npp_install", f"Notepad++ installe dans {npp_dir}.")

        # 4) Recherche derniere version JsonTools
        notify("plugin_info", "Recherche de la derniere version de JsonTools...")
        jt = self.latest_jsontools(arch)
        notify("plugin_info", f"JsonTools {jt['version']} trouve ({jt['zip_name']}).")

        # 5) Telechargement zip plugin
        notify("plugin_download", f"Telechargement {jt['zip_name']}...")
        zip_path = self._download_to_temp(jt["zip_url"], jt["zip_name"])

        # 6) Extraction dans <NPP>/plugins/JsonTools/
        notify("plugin_install", "Installation du plugin JsonTools...")
        plugin_dir = npp_dir / "plugins" / "JsonTools"
        plugin_dir.mkdir(parents=True, exist_ok=True)
        copied = self._extract_plugin(zip_path, plugin_dir)
        notify("plugin_install", f"Plugin JsonTools extrait ({copied} fichier(s)) dans {plugin_dir}.")

        # 7) Cleanup binaires temporaires
        for tmp in (installer_path, zip_path):
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

        return {
            "npp_version": npp["version"],
            "npp_dir": str(npp_dir),
            "jsontools_version": jt["version"],
            "plugin_dir": str(plugin_dir),
        }

    # ------------------------- Helpers -----------------------------

    def _download_to_temp(self, url, name):
        # Ecrit dans %TEMP% (cleanup plus tard). Streaming pour ne pas saturer la RAM.
        target = Path(os.environ.get("TEMP", ".")) / name
        req = Request(url, headers={"User-Agent": DOWNLOAD_USER_AGENT})
        try:
            with urlopen(req, timeout=180) as resp, open(target, "wb") as out:
                shutil.copyfileobj(resp, out, length=1024 * 1024)
        except Exception as exc:
            raise OperationError(f"Telechargement {name} echoue : {exc}") from exc
        if not target.exists() or target.stat().st_size < 1000:
            raise OperationError(f"Fichier telecharge invalide : {target}")
        return target

    def _run_silent_installer(self, exe_path):
        # /S = NSIS silent mode. Pas de /D ici (laisse le defaut Program Files).
        try:
            result = subprocess.run(
                [str(exe_path), "/S"],
                capture_output=True,
                text=True,
                timeout=300,
                creationflags=CREATE_NO_WINDOW,
            )
        except subprocess.TimeoutExpired as exc:
            raise OperationError("Installeur Notepad++ : timeout (5 minutes).") from exc
        except Exception as exc:
            raise OperationError(f"Lancement installeur impossible : {exc}") from exc
        if result.returncode != 0:
            raise OperationError(
                f"Installeur Notepad++ a renvoye un code {result.returncode}. "
                f"Sortie : {result.stderr or result.stdout or '(vide)'}"
            )

    def _extract_plugin(self, zip_path, plugin_dir):
        # Extrait JsonTools.dll a la racine du dossier plugin et les traductions.
        # Ignore testfiles/ et autres ressources de demo.
        kept = 0
        with zipfile.ZipFile(zip_path) as zf:
            for member in zf.namelist():
                if member.endswith("/"):
                    continue
                if member.lower().startswith("testfiles"):
                    continue
                if member == "JsonTools.dll" or member.startswith("translation/"):
                    target = plugin_dir / member
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(member) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    kept += 1
        if not (plugin_dir / "JsonTools.dll").exists():
            raise OperationError(
                "Extraction JsonTools : la DLL JsonTools.dll est absente du ZIP recupere."
            )
        return kept
