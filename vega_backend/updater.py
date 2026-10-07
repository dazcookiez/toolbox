"""Auto-update via GitLab Releases (API publique).

Bascule de l'ancienne methode "raw git" (commit de l'exe dans le repo) vers
GitLab Releases qui hebergent le binaire dans le package registry generique.
Avantages :
  - Plus de bloat dans l'historique git (un exe = +25 Mo par release)
  - Releases listables, traçables, avec notes de version dans GitLab UI
  - Pattern standard, API stable

Pipeline :
  1. check() interroge /releases/permalink/latest -> tag + URL de l'asset
  2. download_and_apply() telecharge l'asset (URL fournie par check)
  3. Spawn d'un batch externe qui attend la fermeture du process courant,
     remplace l'exe, relance. Le caller (UI) doit appeler sys.exit immediatement.

Fallback : si l'API Releases echoue (reseau, repo passe en prive, etc.), on
retombe sur l'ancien fetch raw `vega_gui/theme.py` + raw `dist/vega_toolbox.exe`
pour ne pas casser les anciennes versions deployees pendant la transition.

Pre-requis : version compilee (sys.frozen). En mode source, l'update affiche
juste la nouvelle version sans rien faire.
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen

from ._common import DOWNLOAD_USER_AGENT, OperationError
from ._compat import build_flavor


GITLAB_PROJECT_PATH = "Gryvernn/Vega-Migration-tool"
# Path URL-encode pour l'API
_PROJECT_ID = GITLAB_PROJECT_PATH.replace("/", "%2F")
GITLAB_API_BASE = f"https://gitlab.com/api/v4/projects/{_PROJECT_ID}"
RELEASES_LATEST_URL = f"{GITLAB_API_BASE}/releases/permalink/latest"

# Nom de l'asset a telecharger selon la saveur du build. CRITIQUE : un poste
# Windows 7 execute le build legacy (Python 3.8) ; s'il telechargeait l'exe
# moderne (Python 3.12, Windows 8.1+) il ne redemarrerait plus. Chaque saveur
# ne se met donc a jour qu'avec son propre binaire.
EXE_ASSET_NAMES = {
    "modern": "vega_toolbox.exe",
    "legacy_x64": "vega_toolbox_legacy_x64.exe",
    "legacy_x86": "vega_toolbox_legacy_x86.exe",
}


def expected_asset_name():
    return EXE_ASSET_NAMES.get(build_flavor(), "vega_toolbox.exe")


# Fallback raw URLs (ancienne methode, conservee pour transition)
_RAW_BRANCH = "master"
_RAW_BASE = f"https://gitlab.com/{GITLAB_PROJECT_PATH}/-/raw/{_RAW_BRANCH}"
RAW_VERSION_URL = f"{_RAW_BASE}/vega_gui/theme.py"
RAW_EXE_URL = f"{_RAW_BASE}/dist/{expected_asset_name()}"


def parse_version(value):
    # "v3.7.7" ou "3.7.7" ou "3.7.7 (beta)" -> (3, 7, 7). None si format inattendu.
    value = (value or "").lstrip("v ").strip()
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)", value)
    if not m:
        return None
    return tuple(int(g) for g in m.groups())


def _http_get_json(url, timeout=15):
    req = Request(url, headers={
        "User-Agent": DOWNLOAD_USER_AGENT,
        "Accept": "application/json",
    })
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="ignore"))


def fetch_latest_release():
    # GET /releases/permalink/latest -> JSON avec tag_name, assets.links[]
    data = _http_get_json(RELEASES_LATEST_URL)
    tag = data.get("tag_name", "")
    version = tag.lstrip("v")
    # On exige une correspondance EXACTE avec l'asset de notre saveur : pas de
    # repli sur un autre binaire, qui rendrait le poste inutilisable.
    wanted = expected_asset_name().lower()
    exe_url = None
    for link in (data.get("assets") or {}).get("links") or []:
        if (link.get("name") or "").strip().lower() == wanted:
            exe_url = link.get("url") or link.get("direct_asset_url")
            break
    return {
        "version": version,
        "tag": tag,
        "exe_url": exe_url,
        "name": data.get("name"),
        "description": data.get("description") or "",
    }


def fetch_remote_version_raw():
    # Ancienne methode : parse theme.py distant.
    req = Request(RAW_VERSION_URL, headers={"User-Agent": DOWNLOAD_USER_AGENT})
    with urlopen(req, timeout=15) as resp:
        body = resp.read().decode("utf-8", errors="ignore")
    m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', body)
    if not m:
        raise OperationError("Impossible de parser la version distante (theme.py).")
    return m.group(1).strip()


class UpdateChecker:
    def __init__(self, logger, current_version):
        self.logger = logger
        self.current_version = current_version
        # Cache du dernier check pour reutiliser l'URL exe sans re-fetch.
        self.last_check = None

    def is_frozen(self):
        return bool(getattr(sys, "frozen", False))

    def check(self):
        # Tente d'abord les GitLab Releases. Si echec, fallback raw.
        # Retourne dict { current, remote, has_update, exe_url, source } ou None.
        try:
            rel = fetch_latest_release()
            remote_version = rel["version"]
            exe_url = rel["exe_url"]
            source = "release"
            release_notes = rel.get("description", "")
        except Exception as exc_rel:
            self.logger.warn(f"GitLab Releases indisponible ({exc_rel}), fallback raw...")
            try:
                remote_version = fetch_remote_version_raw()
                exe_url = RAW_EXE_URL
                source = "raw"
                release_notes = ""
            except Exception as exc_raw:
                self.logger.warn(f"Verification mise a jour impossible : {exc_raw}")
                return None

        cur = parse_version(self.current_version)
        rem = parse_version(remote_version)
        if not cur or not rem:
            return None
        result = {
            "current": self.current_version,
            "remote": remote_version,
            "has_update": rem > cur,
            "exe_url": exe_url,
            "source": source,
            "release_notes": release_notes,
        }
        self.last_check = result
        return result

    def download_and_apply(self, on_progress=None):
        # Pipeline: download nouvel exe -> ecrit batch -> spawn batch -> caller fait sys.exit(0).
        # Le caller (UI) doit appeler sys.exit immediatement apres pour liberer le fichier.
        notify = on_progress or (lambda _step, _msg: None)

        if not self.is_frozen():
            raise OperationError(
                "Auto-update reserve a la version compilee (vega_toolbox.exe). "
                "En mode source, mettez a jour via git pull."
            )

        # On a besoin d'une URL d'exe : utilise le cache si dispo, sinon re-check.
        if not self.last_check or not self.last_check.get("exe_url"):
            self.check()
        if not self.last_check or not self.last_check.get("exe_url"):
            raise OperationError(
                "Impossible de localiser l'executable de mise a jour "
                "(release GitLab introuvable et fallback raw indisponible)."
            )
        exe_url = self.last_check["exe_url"]

        notify("download", "Telechargement de la nouvelle version...")
        target = Path(tempfile.gettempdir()) / "vega_toolbox_update.exe"
        req = Request(exe_url, headers={"User-Agent": DOWNLOAD_USER_AGENT})
        try:
            with urlopen(req, timeout=300) as resp, open(target, "wb") as out:
                shutil.copyfileobj(resp, out, length=1024 * 1024)
        except Exception as exc:
            raise OperationError(f"Telechargement update echoue : {exc}") from exc

        if not target.exists() or target.stat().st_size < 1_000_000:
            raise OperationError(
                f"Update telechargee invalide (taille = {target.stat().st_size if target.exists() else 0})."
            )

        current_exe = Path(sys.executable)
        notify("apply", "Lancement du remplacement...")
        bat_path = self._write_apply_script(target, current_exe)
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        CREATE_NO_WINDOW = 0x08000000
        SW_HIDE = 0
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = SW_HIDE
        subprocess.Popen(
            ["cmd", "/c", str(bat_path)],
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
            startupinfo=startupinfo,
            close_fds=True,
        )

    @staticmethod
    def _write_apply_script(source_exe, target_exe):
        # Batch : 1) attend 2s, 2) tue ancien process, 3) move avec retry,
        # 4) pre-warm via type (force AV scan), 5) relance, 6) self-delete.
        bat = Path(tempfile.gettempdir()) / "vega_toolbox_apply_update.bat"
        script = (
            "@echo off\r\n"
            "ping -n 3 127.0.0.1 > nul\r\n"
            "taskkill /f /im vega_toolbox.exe > nul 2>&1\r\n"
            "ping -n 2 127.0.0.1 > nul\r\n"
            ":retry\r\n"
            f'move /y "{source_exe}" "{target_exe}" > nul 2>&1\r\n'
            "if errorlevel 1 (\r\n"
            "  ping -n 2 127.0.0.1 > nul\r\n"
            "  goto retry\r\n"
            ")\r\n"
            f'type "{target_exe}" > nul 2>&1\r\n'
            "ping -n 4 127.0.0.1 > nul\r\n"
            f'start "" "{target_exe}"\r\n'
            'del "%~f0"\r\n'
        )
        bat.write_text(script, encoding="utf-8")
        return bat
