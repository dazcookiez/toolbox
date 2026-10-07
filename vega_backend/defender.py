"""Exclusion Windows Defender pour neutraliser le popup python312.dll.

Le bootloader PyInstaller --onefile extrait Python + dependances dans
%TEMP%\\_MEI<rand>\\ a chaque lancement, puis appelle LoadLibrary sur
python312.dll juste apres l'ecriture. Windows Defender scanne les
fichiers nouvellement ecrits en temps reel et peut bloquer brievement
l'acces : LoadLibrary echoue avec "module could not be found", popup
"Failed to load Python DLL" affiche.

Solution : ajouter %TEMP%\\_MEI* a la liste d'exclusion Defender. Vega
Toolbox tourne deja en admin (uac_admin=True dans le spec), donc on a
les droits. L'operation est idempotente : Add-MpPreference ignore les
chemins deja presents.
"""
import os
import subprocess
import sys
from pathlib import Path

from ._compat import has_modern_cmdlets

CREATE_NO_WINDOW = 0x08000000


def is_frozen():
    return bool(getattr(sys, "frozen", False))


def ensure_exclusions(logger=None):
    """Ajoute les chemins critiques a l'exclusion Windows Defender (silencieux).

    Cible :
      - %TEMP%\\_MEI* (extraction PyInstaller)
      - sys.executable (le binaire Vega Toolbox lui-meme)

    Echec silencieux si Defender absent / desactive / non-admin.
    Retourne True si Add-MpPreference a ete invoque, False sinon.
    """
    if not is_frozen():
        # En mode source, pas de bootloader = pas de popup. Inutile.
        return False

    # Le module Defender (Add-MpPreference) n'existe qu'a partir de Windows 8 :
    # Windows 7 utilise Microsoft Security Essentials, qui n'expose aucune
    # cmdlet d'exclusion. On evite de lancer PowerShell pour rien au demarrage.
    if not has_modern_cmdlets():
        return False

    temp_dir = os.environ.get("TEMP") or os.environ.get("TMP")
    if not temp_dir:
        return False

    mei_pattern = str(Path(temp_dir) / "_MEI*")
    exe_path = sys.executable

    # Add-MpPreference -ExclusionPath accepte les wildcards.
    # -ErrorAction SilentlyContinue : pas d'exception si Defender absent.
    ps_script = (
        f"Add-MpPreference -ExclusionPath '{mei_pattern}' -ErrorAction SilentlyContinue; "
        f"Add-MpPreference -ExclusionPath '{exe_path}' -ErrorAction SilentlyContinue"
    )

    try:
        # Popen non-bloquant : on ne veut pas ralentir le startup pour ca.
        # Le startup PowerShell prend ~1-2s ; pas critique de l'attendre.
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0  # SW_HIDE
        subprocess.Popen(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy", "Bypass",
                "-WindowStyle", "Hidden",
                "-Command", ps_script,
            ],
            creationflags=CREATE_NO_WINDOW,
            startupinfo=startupinfo,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        if logger is not None:
            logger.info("Exclusion Windows Defender appliquee sur _MEI* et l'executable.")
        return True
    except Exception as exc:
        if logger is not None:
            logger.warn(f"Exclusion Windows Defender impossible : {exc}")
        return False
