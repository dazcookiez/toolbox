"""Auto-update via GitHub Releases.

Les binaires sont publies dans un depot GitHub PUBLIC dedie (UPDATE_REPO),
le code source restant dans un depot prive. Chaque release contient :
  - les exe (un par saveur de build, cf. EXE_ASSET_NAMES)
  - latest.json : { "version", "notes", "sha256": { nom_exe: empreinte } }

Pipeline :
  1. check() lit releases/latest/download/latest.json (simple redirection
     GitHub, pas de limite de l'API REST) -> version + empreinte attendue
  2. download_and_apply() telecharge l'exe de CETTE version, verifie son
     SHA-256, puis spawn un batch externe qui attend la fermeture du process
     courant, remplace l'exe et relance. Le caller (UI) doit appeler sys.exit
     immediatement.

Un exe dont l'empreinte ne correspond pas a latest.json n'est jamais applique.

Pre-requis : version compilee (sys.frozen). En mode source, l'update affiche
juste la nouvelle version sans rien faire.
"""
import hashlib
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


UPDATE_REPO = "dazcookiez/toolbox_releases"
_RELEASES_BASE = f"https://github.com/{UPDATE_REPO}/releases"
LATEST_MANIFEST_URL = f"{_RELEASES_BASE}/latest/download/latest.json"

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
    # latest.json de la derniere release -> version, notes, empreinte de notre exe.
    data = _http_get_json(LATEST_MANIFEST_URL)
    version = str(data.get("version", "")).lstrip("v")
    # On exige une correspondance EXACTE avec l'asset de notre saveur : pas de
    # repli sur un autre binaire, qui rendrait le poste inutilisable.
    wanted = expected_asset_name()
    sha256 = (data.get("sha256") or {}).get(wanted)
    exe_url = f"{_RELEASES_BASE}/download/v{version}/{wanted}" if sha256 else None
    return {
        "version": version,
        "tag": f"v{version}",
        "exe_url": exe_url,
        "sha256": (sha256 or "").lower() or None,
        "name": f"Vega Toolbox {version}",
        "description": data.get("notes") or "",
    }


def _file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class UpdateChecker:
    def __init__(self, logger, current_version):
        self.logger = logger
        self.current_version = current_version
        # Cache du dernier check pour reutiliser l'URL exe sans re-fetch.
        self.last_check = None

    def is_frozen(self):
        return bool(getattr(sys, "frozen", False))

    def check(self):
        # Retourne dict { current, remote, has_update, exe_url, sha256, source } ou None.
        try:
            rel = fetch_latest_release()
        except Exception as exc:
            self.logger.warn(f"Verification mise a jour impossible : {exc}")
            return None
        remote_version = rel["version"]
        exe_url = rel["exe_url"]
        source = "release"
        release_notes = rel.get("description", "")

        cur = parse_version(self.current_version)
        rem = parse_version(remote_version)
        if not cur or not rem:
            return None
        result = {
            "current": self.current_version,
            "remote": remote_version,
            "has_update": rem > cur,
            "exe_url": exe_url,
            "sha256": rel["sha256"],
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
                "(release GitHub introuvable ou sans binaire pour ce poste)."
            )
        exe_url = self.last_check["exe_url"]
        expected_sha256 = self.last_check.get("sha256")

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

        notify("verify", "Verification de l'empreinte SHA-256...")
        actual_sha256 = _file_sha256(target)
        if not expected_sha256 or actual_sha256 != expected_sha256:
            try:
                target.unlink()
            except OSError:
                pass
            raise OperationError(
                "Update refusee : l'empreinte SHA-256 du fichier telecharge ne "
                "correspond pas a celle publiee dans la release."
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
            f'taskkill /f /im "{Path(target_exe).name}" > nul 2>&1\r\n'
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
