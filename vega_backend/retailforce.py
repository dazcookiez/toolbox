"""Reinstallation de RetailForce Fiscal Webservice (version cible 1.11.2).

Pipeline :
  1) Detection version actuelle via registre (HKLM Uninstall x64 + WOW6432Node)
  2) Desinstallation silencieuse via UninstallString (typiquement msiexec /x {GUID})
  3) Suppression des dossiers residuels :
       C:\\Program Files\\RetailForce
       C:\\ProgramData\\RetailForce
  4) Telechargement du MSI 1.11.2 depuis retailforce.cloud
  5) Installation silencieuse via msiexec /i ... /quiet /norestart

Pre-requis : admin (msiexec en silent + suppression Program Files = elevation requise).
"""
import os
import shutil
import subprocess
import winreg
from pathlib import Path
from urllib.request import Request, urlopen

from ._common import (
    CREATE_NO_WINDOW,
    DOWNLOAD_USER_AGENT,
    OperationError,
    handle_remove_readonly,
    is_admin,
)
from ._compat import os_architecture


RETAILFORCE_TARGET_VERSION = "1.11.21.7255"

# Un installeur par architecture. La cle correspond a la valeur renvoyee par
# _compat.os_architecture() : on propose par defaut celle du poste, tout en
# laissant l'utilisateur forcer l'autre depuis l'interface (certains postes
# 64 bits hebergent un Vega 32 bits, et inversement).
_RETAILFORCE_BASE = (
    "https://retailforce.cloud/downloads/Version%201.11.21/Setup%20FiscalService/"
)
RETAILFORCE_MSI = {
    "x64": {
        "url": _RETAILFORCE_BASE + "SetupFiscalWebservice.x64.1.11.21.7255.msi",
        "filename": "SetupFiscalWebservice.x64.1.11.21.7255.msi",
    },
    "x86": {
        "url": _RETAILFORCE_BASE + "SetupFiscalWebservice.x86.1.11.21.7255.msi",
        "filename": "SetupFiscalWebservice.x86.1.11.21.7255.msi",
    },
}

# Conserve pour compatibilite avec l'existant (defaut = 64 bits).
RETAILFORCE_MSI_URL = RETAILFORCE_MSI["x64"]["url"]
RETAILFORCE_MSI_FILENAME = RETAILFORCE_MSI["x64"]["filename"]

# Dossiers a nettoyer apres uninstall (le MSI ne les retire pas toujours).
RETAILFORCE_LEFTOVER_PATHS = [
    Path(r"C:\Program Files\RetailForce"),
    Path(r"C:\ProgramData\RetailForce"),
]


class RetailForceManager:
    def __init__(self, logger):
        self.logger = logger

    # ------------------------- Detection -------------------------

    def detect_installed(self):
        # Scan les 2 emplacements registre uninstall (x64 + WOW6432Node) et retourne
        # le 1er match RetailForce / Fiscal Webservice.
        for base in (
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
            r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
        ):
            match = self._scan_uninstall_key(winreg.HKEY_LOCAL_MACHINE, base)
            if match:
                return match
        return None

    def _scan_uninstall_key(self, hive, base_path):
        try:
            base = winreg.OpenKey(hive, base_path)
        except OSError:
            return None
        try:
            index = 0
            while True:
                try:
                    sub_name = winreg.EnumKey(base, index)
                except OSError:
                    break
                index += 1
                try:
                    sub = winreg.OpenKey(base, sub_name)
                except OSError:
                    continue
                try:
                    info = self._read_values(sub)
                finally:
                    winreg.CloseKey(sub)
                display = (info.get("DisplayName") or "").lower()
                if "retailforce" in display or "fiscal webservice" in display:
                    return {
                        "key_name": sub_name,
                        "display_name": info.get("DisplayName") or "RetailForce",
                        "version": info.get("DisplayVersion") or "?",
                        "uninstall_string": info.get("UninstallString") or "",
                        "quiet_uninstall_string": info.get("QuietUninstallString") or "",
                        "install_location": info.get("InstallLocation") or "",
                    }
        finally:
            winreg.CloseKey(base)
        return None

    @staticmethod
    def _read_values(key):
        result = {}
        index = 0
        while True:
            try:
                name, value, _kind = winreg.EnumValue(key, index)
            except OSError:
                break
            index += 1
            if isinstance(value, str):
                result[name] = value
        return result

    # ------------------------- Pipeline ------------------------

    def install_or_replace(self, on_progress=None, architecture=None):
        # architecture : "x64" | "x86" | None (= celle du poste).
        if not is_admin():
            raise OperationError(
                "L'installation / desinstallation RetailForce requiert un lancement en administrateur."
            )
        notify = on_progress or (lambda _step, _msg: None)
        arch = architecture or os_architecture()
        if arch not in RETAILFORCE_MSI:
            raise OperationError(f"Architecture RetailForce inconnue : {arch}")

        existing = self.detect_installed()
        if existing:
            notify("detect", f"RetailForce {existing['version']} detecte ({existing['display_name']}).")
            notify("uninstall", "Desinstallation en cours...")
            self._uninstall(existing)
            notify("uninstall", "Desinstallation terminee.")
        else:
            notify("detect", "RetailForce non installe.")

        # Cleanup folders meme si pas detecte (peut etre des restes apres uninstall partiel).
        notify("cleanup", "Suppression des dossiers residuels RetailForce...")
        removed = self._cleanup_leftover_paths()
        if removed:
            notify("cleanup", f"Supprime : {', '.join(removed)}")
        else:
            notify("cleanup", "Aucun dossier residuel a supprimer.")

        notify("download", f"Telechargement du MSI {RETAILFORCE_TARGET_VERSION} ({arch})...")
        msi_path = self._download_msi(arch)
        notify("download", f"MSI telecharge : {msi_path.name}")

        notify("install", f"Installation silencieuse de RetailForce {RETAILFORCE_TARGET_VERSION}...")
        self._install_msi(msi_path)
        notify("install", f"RetailForce {RETAILFORCE_TARGET_VERSION} installe.")

        # Cleanup MSI temporaire.
        try:
            msi_path.unlink(missing_ok=True)
        except Exception:
            pass

        # Verification post-install via registre.
        new_state = self.detect_installed()
        new_version = (new_state or {}).get("version", RETAILFORCE_TARGET_VERSION)

        return {
            "previous_version": existing["version"] if existing else None,
            "new_version": new_version,
            "target_version": RETAILFORCE_TARGET_VERSION,
        }

    # ------------------------- Steps ---------------------------

    def _uninstall(self, info):
        # Strategie : si la cle UninstallString commence par MsiExec, on transforme en /x {GUID}
        # avec /quiet /norestart. Sinon on essaie le QuietUninstallString tel quel. Dernier recours :
        # on lance UninstallString direct (peut afficher une UI mais on ne peut pas faire mieux).
        cmd = self._build_uninstall_command(info)
        if not cmd:
            raise OperationError(
                "Impossible de determiner la commande de desinstallation RetailForce dans la registry."
            )
        self.logger.info(f"Commande desinstallation : {' '.join(cmd) if isinstance(cmd, list) else cmd}")
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,
                creationflags=CREATE_NO_WINDOW,
                shell=isinstance(cmd, str),
            )
        except subprocess.TimeoutExpired as exc:
            raise OperationError("Desinstallation RetailForce : timeout (10 minutes).") from exc
        except Exception as exc:
            raise OperationError(f"Desinstallation impossible : {exc}") from exc
        # msiexec : 0 = OK, 3010 = OK reboot needed, 1605 = produit deja absent
        if result.returncode not in (0, 3010, 1605):
            raise OperationError(
                f"msiexec /x retourne code {result.returncode}. "
                f"Sortie : {result.stderr or result.stdout or '(vide)'}"
            )

    def _build_uninstall_command(self, info):
        key_name = info.get("key_name", "")
        # Si la cle est un GUID MSI (format {XXX-XXX-...}), on construit msiexec /x {GUID}.
        if key_name.startswith("{") and key_name.endswith("}"):
            return ["msiexec", "/x", key_name, "/quiet", "/norestart"]
        # Sinon QuietUninstallString si dispo (deja silencieux).
        quiet = info.get("quiet_uninstall_string", "").strip()
        if quiet:
            return quiet
        # Fallback : UninstallString. Si c'est msiexec on ajoute /quiet /norestart.
        unins = info.get("uninstall_string", "").strip()
        if not unins:
            return None
        if "msiexec" in unins.lower():
            # Force le mode silencieux : on remplace "/I" ou ajoute en fin.
            if "/quiet" not in unins.lower():
                unins = unins + " /quiet /norestart"
        return unins

    def _cleanup_leftover_paths(self):
        removed = []
        for path in RETAILFORCE_LEFTOVER_PATHS:
            if not path.exists():
                continue
            try:
                shutil.rmtree(path, onerror=handle_remove_readonly)
                removed.append(str(path))
                self.logger.info(f"Dossier supprime : {path}")
            except Exception as exc:
                # On loggue mais on ne bloque pas : un fichier verrouille peut empecher la
                # suppression complete sans necessairement empecher la reinstall.
                self.logger.warn(f"Suppression partielle {path} : {exc}")
        return removed

    def _download_msi(self, architecture=None):
        spec = RETAILFORCE_MSI[architecture or os_architecture()]
        target = Path(os.environ.get("TEMP", ".")) / spec["filename"]
        req = Request(spec["url"], headers={"User-Agent": DOWNLOAD_USER_AGENT})
        try:
            with urlopen(req, timeout=300) as resp, open(target, "wb") as out:
                shutil.copyfileobj(resp, out, length=1024 * 1024)
        except Exception as exc:
            raise OperationError(f"Telechargement MSI RetailForce echoue : {exc}") from exc
        if not target.exists() or target.stat().st_size < 1_000_000:
            raise OperationError(f"MSI telecharge invalide : {target}")
        return target

    def _install_msi(self, msi_path):
        try:
            result = subprocess.run(
                ["msiexec", "/i", str(msi_path), "/quiet", "/norestart"],
                capture_output=True,
                text=True,
                timeout=600,
                creationflags=CREATE_NO_WINDOW,
            )
        except subprocess.TimeoutExpired as exc:
            raise OperationError("Installation RetailForce : timeout (10 minutes).") from exc
        except Exception as exc:
            raise OperationError(f"Installation impossible : {exc}") from exc
        if result.returncode not in (0, 3010):
            raise OperationError(
                f"msiexec /i retourne code {result.returncode}. "
                f"Sortie : {result.stderr or result.stdout or '(vide)'}"
            )
