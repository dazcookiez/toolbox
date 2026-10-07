"""Installation / desinstallation silencieuse de HFSQL Client/Serveur.

S'appuie sur l'installeur officiel PC SOFT WX{ver}PACKHFSQLCS.exe qui supporte
le mode silent via /Silent=<chemin .INI>. La doc PC SOFT :
  https://doc.pcsoft.fr/fr-FR/?8010003=&name=installation_silencieuse

Workflow :
  1. detect_install() : verifie si HFSQL est deja la (service Manta + registre)
  2. scan_vega_roots() : cherche VEGAHF ou VEGACS sur tous les disques LOCAUX,
     valide en exigeant un BDD\\vega.exe ou BDD\\vega6.exe a l'interieur
  3. download_installer(version) : telecharge le pack depuis quatuhore.fr
  4. install(repertoire, port) : genere un .INI, lance le silent, parse le log
  5. uninstall_and_purge() : MAJ=3 silent + suppression service + dossier + cle registre
  6. verify_install(port) : verification en cascade (service + port + log)
"""
import ctypes
import socket
import string
import subprocess
import tempfile
import time
import winreg
from pathlib import Path

from ._common import (
    CREATE_NO_WINDOW,
    DOWNLOAD_USER_AGENT,
    OperationError,
    powershell_output,
)
from . import _download_core
from ._compat import cim_cmdlet


# URLs de telechargement des installeurs (hebergement Zucchetti)
HFSQL_INSTALLERS = {
    "31": "https://telechargement.quatuhore.fr/CS/WX310PACKHFSQLCS.exe",
    "28": "https://telechargement.quatuhore.fr/CS/WX280PACKHFSQLCS.exe",
}
DEFAULT_HFSQL_VERSION = "31"
DEFAULT_HFSQL_PORT = 4900

# Cle registre uninstall posee par l'installeur HFSQL
HFSQL_UNINSTALL_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\HyperFileManager"
# Cle 64 bits classique
HFSQL_UNINSTALL_KEY_WOW = r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\HyperFileManager"

# Fichiers attendus dans le sous-dossier BDD\<base>\ pour valider qu'un dossier
# VEGAHF / VEGACS detecte est bien une vraie installation Vega. On accepte :
#   - vega.exe / vega6.exe : binaires Vega V5/V6 (parfois absents si le client
#     n'est pas le poste principal)
#   - vega.wdd : fichier d'analyse WinDev TOUJOURS present dans une vraie
#     base Vega (modele de donnees) - case insensitive
VEGA_EXE_MARKERS = ("vega.exe", "vega6.exe", "vega.wdd")

# Repertoires Windows ou HFSQL peut avoir laisse des residus apres une
# desinstallation incomplete.
RESIDUAL_PATH_HINTS = [
    r"C:\Program Files\PC SOFT",
    r"C:\Program Files (x86)\PC SOFT",
    r"C:\2DCOM\PC SOFT",
]


def _on_rmtree_err(func, path, exc_info):
    # Callback shutil.rmtree pour Windows : retire les flags read-only/system
    # qui peuvent bloquer la suppression, puis re-tente l'operation.
    import os as _os
    import stat as _stat
    try:
        _os.chmod(path, _stat.S_IWRITE)
        func(path)
    except Exception:
        raise


class HfsqlInstallerManager:
    def __init__(self, logger):
        self.logger = logger

    # -------------------------------------------------------------------
    # Detection installation existante
    # -------------------------------------------------------------------

    def detect_install(self, vega_candidates=None):
        # Cherche le moteur HFSQL Serveur + le Centre de Controle.
        # vega_candidates (optionnel) : resultat deja calcule de scan_vega_roots()
        # pour eviter de re-scanner les disques 2 fois (le caller scanne deja
        # pour son propre UI). Si None, on scanne en interne.
        result = {
            "installed": False,
            "version": None,         # ex: "31", "28"
            "server_path": None,     # chemin du dossier contenant mantamanager64.exe
            "control_center_path": None,
            "service": self._detect_service(),
            "registry": self._detect_registry(),
        }

        if vega_candidates is None:
            vega_candidates = self.scan_vega_roots()

        server_exe = self._find_server_binary(
            registry_hint=(result["registry"] or {}).get("install_location"),
            vega_candidates=vega_candidates,
        )
        if server_exe:
            result["server_path"] = str(server_exe.parent)
            result["version"] = self._guess_version_from_dir(server_exe.parent)

        # Centre de Controle
        cc = self._find_control_center()
        if cc:
            result["control_center_path"] = str(cc)
            if not result["version"]:
                # Si on n'a pas eu la version cote server, deduit du nom CC{XXX}HF64.exe
                result["version"] = self._guess_version_from_filename(cc.name)

        # Verdict 'installed' : on EXIGE le service ou la cle registre.
        # Des binaires orphelins (manta64.exe + DLL) qui trainent dans VEGAHF
        # apres une desinstallation incomplete NE doivent PAS faire passer
        # 'installed' a True - sinon le bouton Installer reste grise et on
        # peut jamais re-installer.
        result["installed"] = bool(result["service"] or result["registry"])
        # On expose aussi si des binaires resiuels existent pour permettre
        # un nettoyage cible.
        result["orphan_binaries_path"] = (
            result["server_path"]
            if (result["server_path"] and not result["installed"])
            else None
        )
        return result

    def _find_server_binary(self, registry_hint=None, vega_candidates=None):
        # Cherche mantamanager64.exe : registre + VEGAHF/VEGACS racine + Program Files.
        # vega_candidates fourni par le caller pour eviter un re-scan complet
        # des disques.
        # NB : sur un Windows 32 bits, PCSoft installe les binaires SANS le
        # suffixe "64" (manta.exe / MantaManager.exe). On teste donc les deux
        # familles de noms, sinon HFSQL reste invisible sur les postes 32 bits.
        server_names = (
            "mantamanager64.exe", "MantaManager64.exe", "manta64.exe",
            "mantamanager.exe", "MantaManager.exe", "manta.exe",
        )
        candidates = []
        if registry_hint:
            p = Path(registry_hint)
            for name in server_names:
                candidates.append(p / name)
        # Racines VEGAHF / VEGACS sur disques locaux (deja scannees)
        if vega_candidates is None:
            vega_candidates = self.scan_vega_roots()
        for vega in vega_candidates:
            base = Path(vega["path"])
            for name in server_names:
                candidates.append(base / name)
        # Path PC SOFT classique
        for hint in RESIDUAL_PATH_HINTS:
            base = Path(hint)
            if not base.exists():
                continue
            try:
                for sub in base.iterdir():
                    if sub.is_dir() and "hfsql" in sub.name.lower() and "cc" not in sub.name.lower():
                        for name in server_names:
                            candidates.append(sub / name)
            except (OSError, PermissionError):
                continue
        for c in candidates:
            try:
                if c.is_file():
                    return c
            except OSError:
                continue
        return None

    def _find_control_center(self):
        # Centre de Controle = CC{XXX}HF64.exe (64 bits) ou CC{XXX}HF.exe
        # (32 bits) dans C:\Program Files\PC SOFT\CC HFSQL\
        # XXX = 310 (V31), 280 (V28), etc.
        for hint in RESIDUAL_PATH_HINTS:
            cc_dir = Path(hint) / "CC HFSQL"
            if not cc_dir.is_dir():
                continue
            try:
                for entry in cc_dir.iterdir():
                    name = entry.name.lower()
                    if entry.is_file() and name.startswith("cc") and (
                            name.endswith("hf64.exe") or name.endswith("hf.exe")):
                        return entry
            except (OSError, PermissionError):
                continue
        return None

    def _guess_version_from_dir(self, dir_path):
        # Cherche les fichiers wd{XXX}*.dll dans le dossier et extrait XXX -> ex
        # "wd310hf64.dll" -> "310" -> version courte "31".
        # IMPORTANT : apres une mise a jour (28 -> 31), des DLL de l'ancienne
        # version (wd280*.dll) peuvent subsister a cote des nouvelles
        # (wd310*.dll). iterdir() etant trie alphabetiquement sur NTFS, prendre
        # la PREMIERE version renvoyait l'ancienne (28) au lieu de la nouvelle.
        # On retourne donc la version la PLUS RECENTE trouvee.
        best = None
        best_key = -1
        try:
            for entry in Path(dir_path).iterdir():
                if not entry.is_file():
                    continue
                v = self._guess_version_from_filename(entry.name)
                if not v:
                    continue
                try:
                    key = int(v)
                except ValueError:
                    continue
                if key > best_key:
                    best_key = key
                    best = v
        except (OSError, PermissionError):
            pass
        return best

    @staticmethod
    def _guess_version_from_filename(name):
        # "wd310hf64.dll" -> "31", "CC310HF64.exe" -> "31".
        import re as _re
        m = _re.search(r"(?:wd|cc)(\d{3})", name.lower())
        if m:
            three = m.group(1)
            # XXX = "310" -> "31", "280" -> "28"
            return three[:2] if three.endswith("0") else three
        return None

    def _detect_service(self):
        # Reutilise la meme logique que session_closer : recherche par nom OU
        # display name contenant HFSQL ou Manta.
        script = """
$ErrorActionPreference = 'SilentlyContinue'
$svc = Get-Service | Where-Object {
    $_.Name -like '*HFSQL*' -or $_.DisplayName -like '*HFSQL*' -or
    $_.Name -like '*Manta*' -or $_.DisplayName -like '*Manta*'
} | Select-Object -First 1
if (-not $svc) { exit 0 }
"$($svc.Name)|$($svc.DisplayName)|$($svc.Status)|$($svc.StartType)"
"""
        r = powershell_output(script)
        if r.returncode != 0 or not r.stdout.strip():
            return None
        try:
            name, display, status, start_type = r.stdout.strip().split("|", 3)
            return {
                "name": name,
                "display_name": display,
                "status": status,
                "start_type": start_type,
            }
        except ValueError:
            return None

    def detect_all_hfsql_instances(self):
        # Liste TOUTES les instances HFSQL detectees sur le poste, avec leur
        # service Windows + le(s) port(s) TCP en ecoute associes au PID.
        # Permet d'avertir l'utilisateur si plusieurs HFSQL coexistent
        # (un Vega + un autre logiciel sur un port different).
        # Retourne une liste de dicts :
        #   [
        #     {
        #       "service_name": "MantaManager",
        #       "display_name": "Hyper File Server",
        #       "status": "Running" | "Stopped" | ...,
        #       "pid": 1234 | None,
        #       "binary_path": "C:\\... \\manta64.exe",
        #       "ports": [4900, 4901],
        #     },
        #     ...
        #   ]
        script = r"""
$ErrorActionPreference = 'SilentlyContinue'
$services = Get-CimInstance Win32_Service | Where-Object {
    $_.Name -like '*HFSQL*' -or $_.DisplayName -like '*HFSQL*' -or
    $_.Name -like '*Manta*' -or $_.DisplayName -like '*Manta*'
}
$out = @()
# Get-NetTCPConnection (module NetTCPIP) n'existe qu'a partir de Windows 8 /
# Server 2012 : on retombe sinon sur netstat -ano. Le filtre "en ecoute" se fait
# sur l'adresse distante (0.0.0.0:0 / [::]:0) et non sur le libelle d'etat, qui
# est traduit selon la langue de Windows.
$useNetTcp = [bool](Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue)
$netstatLines = $null
if (-not $useNetTcp) { $netstatLines = @(& netstat -ano) }

foreach ($svc in $services) {
    # NB : ne pas nommer cette variable $pid — c'est une variable automatique
    # en lecture seule de PowerShell ; l'affectation echoue silencieusement et
    # on recupere alors le PID de PowerShell au lieu de celui du service.
    $procId = $svc.ProcessId
    $ports = @()
    if ($procId -and $procId -gt 0) {
        try {
            if ($useNetTcp) {
                $conns = Get-NetTCPConnection -OwningProcess $procId -State Listen -ErrorAction SilentlyContinue
                if ($conns) {
                    $ports = $conns | Select-Object -ExpandProperty LocalPort -Unique | Sort-Object
                }
            } else {
                foreach ($line in $netstatLines) {
                    $parts = ($line.Trim() -split '\s+')
                    if ($parts.Count -eq 5 -and $parts[0] -eq 'TCP' -and
                        $parts[4] -eq "$procId" -and
                        $parts[2] -match '^(0\.0\.0\.0:0|\[::\]:0)$') {
                        $addr = $parts[1]
                        $p = $addr.Substring($addr.LastIndexOf(':') + 1)
                        if ($p -match '^\d+$') { $ports += [int]$p }
                    }
                }
                $ports = $ports | Sort-Object -Unique
            }
        } catch {}
    }
    $portsStr = if ($ports.Count -gt 0) { ($ports -join ',') } else { '' }
    $binaryPath = $svc.PathName
    if ($binaryPath) {
        # Strip quotes + args (PathName peut etre "C:\..\manta64.exe" -RUN)
        $binaryPath = $binaryPath.Trim('"')
        $sp = $binaryPath.IndexOf(' ')
        if ($sp -gt 0) { $binaryPath = $binaryPath.Substring(0, $sp).Trim('"') }
    }
    $out += "{0}|{1}|{2}|{3}|{4}|{5}" -f $svc.Name, $svc.DisplayName, $svc.State, ($procId -as [string]), $binaryPath, $portsStr
}
$out -join "`n"
"""
        try:
            # Get-CimInstance -> Get-WmiObject sur Windows 7 (PowerShell 2.0).
            r = powershell_output(script.replace("Get-CimInstance", cim_cmdlet()))
        except Exception as exc:
            self.logger.warn(f"detect_all_hfsql_instances : powershell KO ({exc})")
            return []
        if r.returncode != 0 or not r.stdout.strip():
            return []
        result = []
        for line in r.stdout.strip().splitlines():
            parts = line.split("|", 5)
            if len(parts) < 6:
                continue
            name, display, state, pid_str, binary, ports_str = parts
            try:
                pid = int(pid_str) if pid_str.strip() else None
            except ValueError:
                pid = None
            ports = []
            for p in ports_str.split(","):
                p = p.strip()
                if p.isdigit():
                    ports.append(int(p))
            result.append({
                "service_name": name.strip(),
                "display_name": display.strip(),
                "status": state.strip(),
                "pid": pid,
                "binary_path": binary.strip(),
                "ports": ports,
            })
        return result

    def _detect_registry(self):
        # Lit la cle Uninstall HyperFileManager (32 ou 64 bits).
        for hive_path in (HFSQL_UNINSTALL_KEY, HFSQL_UNINSTALL_KEY_WOW):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, hive_path) as key:
                    out = {"key_path": hive_path}
                    for name in ("DisplayName", "DisplayVersion", "InstallLocation", "UninstallString", "Publisher"):
                        try:
                            val, _ = winreg.QueryValueEx(key, name)
                            out[name.lower().replace("display", "")] = val
                        except FileNotFoundError:
                            pass
                    # Normalise : on s'attend aux cles 'name', 'version', 'install_location', etc.
                    return {
                        "display_name": out.get("name"),
                        "version": out.get("version"),
                        "install_location": out.get("installlocation"),
                        "uninstall_string": out.get("uninstallstring"),
                        "publisher": out.get("publisher"),
                        "key_path": hive_path,
                    }
            except FileNotFoundError:
                continue
            except OSError:
                continue
        return None

    # -------------------------------------------------------------------
    # Scan dossiers Vega
    # -------------------------------------------------------------------

    def scan_vega_roots(self):
        # Scanne toutes les lettres de disques LOCAUX (skip reseau) a la
        # recherche de VEGAHF ou VEGACS. Validation : au moins un sous-dossier
        # BDD\<base>\ contenant vega.exe ou vega6.exe.
        # Retourne liste de candidats : [{path, name, bases: [list], valid: bool}]
        candidates = []
        kernel32 = ctypes.windll.kernel32
        # GetDriveType : 0=UNKNOWN, 1=NO_ROOT, 2=REMOVABLE, 3=FIXED,
        #                4=REMOTE (reseau), 5=CDROM, 6=RAMDISK
        for letter in string.ascii_uppercase:
            drive = f"{letter}:\\"
            try:
                dtype = kernel32.GetDriveTypeW(ctypes.c_wchar_p(drive))
            except Exception:
                continue
            if dtype in (0, 1, 4, 5):
                # exclu : inconnu, pas de racine, reseau, cdrom
                continue
            for share_name in ("VEGAHF", "VEGACS"):
                share_path = Path(drive) / share_name
                try:
                    if not share_path.is_dir():
                        continue
                except OSError:
                    continue
                # Cherche BDD\<base>\vega.exe ou vega6.exe pour valider
                bdd = share_path / "BDD"
                bases_found = []
                valid = False
                if bdd.is_dir():
                    try:
                        for base_dir in bdd.iterdir():
                            if not base_dir.is_dir():
                                continue
                            for marker in VEGA_EXE_MARKERS:
                                if (base_dir / marker).is_file():
                                    bases_found.append(base_dir.name)
                                    valid = True
                                    break
                    except (OSError, PermissionError):
                        pass
                candidates.append({
                    "path": str(share_path),
                    "name": share_name,
                    "drive": letter,
                    "bases": sorted(set(bases_found)),
                    "valid": valid,
                })
        return candidates

    # -------------------------------------------------------------------
    # Telechargement installeur
    # -------------------------------------------------------------------

    def download_installer(self, version=DEFAULT_HFSQL_VERSION, on_progress=None):
        # Pipeline telechargement WX{ver}PACKHFSQLCS.exe dans %TEMP% :
        #   1. Re-use cache si deja telecharge (>100 Mo)
        #   2. HEAD pour Content-Length + Accept-Ranges
        #   3. Si Range supporte + fichier > seuil : multi-part 4 connexions
        #      paralleles. Gain typique 2-4x si le mirror throttle per-connection.
        #   4. Sinon fallback single-stream classique (avec MB/s affiche).
        url = HFSQL_INSTALLERS.get(version)
        if not url:
            raise OperationError(f"Version HFSQL inconnue : {version}")
        target = Path(tempfile.gettempdir()) / f"WX{version}0PACKHFSQLCS.exe"
        notify = on_progress or (lambda *_a, **_k: None)

        # Cache hit
        if target.exists() and target.stat().st_size > 100 * 1024 * 1024:
            self.logger.info(
                f"Installeur deja present : {target} "
                f"({target.stat().st_size // (1024*1024)} Mo)"
            )
            notify("cache", f"Installeur deja telecharge ({target.stat().st_size // (1024*1024)} Mo).")
            return target

        self.logger.info(f"Telechargement installeur HFSQL v{version} depuis {url}")
        notify("download", f"Telechargement de l'installeur HFSQL v{version}...")

        # Wrapper de progression : le shared module emet (downloaded, total, bps),
        # on traduit en notification texte 'Telechargement : X % (Y / Z Mo, V Mo/s)'.
        def _progress(downloaded, total, speed_bps):
            mb_done = downloaded // (1024 * 1024)
            mb_total = (total // (1024 * 1024)) if total else 0
            mbps = speed_bps / (1024 * 1024)
            pct = int(100 * downloaded / total) if total else 0
            notify(
                "progress",
                f"Telechargement : {pct} % ({mb_done} / {mb_total} Mo, {mbps:.1f} Mo/s)",
            )

        try:
            _download_core.download_file(
                url, target,
                on_progress=_progress,
                logger=self.logger,
            )
        except OperationError:
            raise
        except Exception as exc:
            raise OperationError(f"Telechargement installeur HFSQL echoue : {exc}") from exc

        # Sanity check final
        if target.stat().st_size < 100 * 1024 * 1024:
            raise OperationError(
                f"Installeur HFSQL telecharge trop petit "
                f"({target.stat().st_size} octets), corrompu ?"
            )
        self.logger.info(
            f"Installeur telecharge : {target} ({target.stat().st_size // (1024*1024)} Mo)"
        )
        return target

    # -------------------------------------------------------------------
    # Pipeline install / uninstall
    # -------------------------------------------------------------------

    def install(self, vega_root, port=DEFAULT_HFSQL_PORT, version=DEFAULT_HFSQL_VERSION,
                on_progress=None):
        # Pipeline complet : download -> .ini -> silent install -> verify.
        # vega_root = chemin VEGAHF ou VEGACS validé.
        # Repertoire serveur = vega_root directement (le user a confirme ce choix).
        notify = on_progress or (lambda *_a, **_k: None)
        vega_root = Path(vega_root)
        if not vega_root.is_dir():
            raise OperationError(f"Dossier Vega introuvable : {vega_root}")

        # Telechargement
        installer = self.download_installer(version=version, on_progress=on_progress)

        # Generation du .INI
        ini_path = self._build_ini(
            repertoire=str(vega_root),
            port=port,
            maj=2,  # 2 = installation
        )
        log_path = Path(tempfile.gettempdir()) / f"hfsql_install_{int(time.time())}.log"

        # Pre-flight : taskkill prophylactique sur les processus HFSQL qui
        # pourraient encore tourner (cas typique : juste apres un uninstall).
        # Sans ca, l'installeur peut detecter des handles ouverts et abort.
        self._kill_hfsql_processes()

        notify("install", "Installation silencieuse en cours (peut prendre plusieurs minutes)...")
        self.logger.info(f"Lancement install HFSQL silent : repertoire={vega_root}, port={port}")
        self.logger.info(f"  INI : {ini_path}")
        self.logger.info(f"  LOG : {log_path}")
        rc = self._run_installer(installer, ini_path, log_path)
        self.logger.info(f"Installeur HFSQL retour : {rc}")

        # Verification cascade. On poll jusqu'a 60s avant de declencher le
        # retry : sur PC lent le service peut mettre 20-30s a demarrer apres
        # la fin du process installeur (cas client : install OK mais Toolbox
        # croit que ca a rate parce que le service n'est pas encore monte).
        notify("verify", "Vérification du service HFSQL (jusqu'à 60s)...")
        verification = self._verify_install_with_wait(
            port=port, log_path=log_path, repertoire=str(vega_root), max_wait_s=60,
        )
        self.logger.info(f"Verification post-install : {verification}")

        # Retry auto si service ET port ne sont toujours pas la apres 60s.
        # Install.log est informatif mais ne declenche pas a lui seul un
        # retry : son absence peut etre due a une particularite PCSoft
        # (encodage, sous-dossier, etc.) alors que l'install est bonne.
        needs_retry = not (
            verification.get("service_ok") and verification.get("port_ok")
        )
        if needs_retry:
            self.logger.warn(
                "Service HFSQL non actif ou port inaccessible apres 60s. "
                "Tentative #2 dans 5s..."
            )
            notify("retry", "L'installation a échoué. Nouvelle tentative...")
            self._kill_hfsql_processes()
            time.sleep(5)
            log_path2 = log_path.with_name(log_path.stem + "_retry.log")
            rc = self._run_installer(installer, ini_path, log_path2)
            self.logger.info(f"Installeur HFSQL retour (retry) : {rc}")
            verification = self._verify_install_with_wait(
                port=port, log_path=log_path2, repertoire=str(vega_root), max_wait_s=60,
            )
            log_path = log_path2
            self.logger.info(f"Verification apres retry : {verification}")

        return {
            "version": version,
            "repertoire": str(vega_root),
            "port": port,
            "return_code": rc,
            "log_path": str(log_path),
            "verification": verification,
        }

    def run_custom(self, ini_params, version=DEFAULT_HFSQL_VERSION, on_progress=None):
        # Mode avance : prend un dict complet de parametres .INI et lance
        # l'installeur en silent. ini_params doit contenir :
        #   plateforme (1/2/3), maj (1/2/3), cchf (0/1),
        #   serveur, machine, port, repertoire
        notify = on_progress or (lambda *_a, **_k: None)
        installer = self.download_installer(version=version, on_progress=on_progress)
        ini_path = self._build_ini_custom(ini_params)
        import time as _time
        log_path = Path(tempfile.gettempdir()) / f"hfsql_advanced_{int(_time.time())}.log"
        notify("install", "Exécution silencieuse en cours...")
        self.logger.info(f"HFSQL mode avance : INI={ini_path}, LOG={log_path}")
        rc = self._run_installer(installer, ini_path, log_path)
        self.logger.info(f"Retour : {rc}")
        verification = self.verify_install(
            port=int(ini_params.get("port") or DEFAULT_HFSQL_PORT),
            log_path=log_path,
            repertoire=ini_params.get("repertoire"),
        )
        return {
            "version": version,
            "return_code": rc,
            "log_path": str(log_path),
            "params": ini_params,
            "verification": verification,
        }

    # Liste des processus HFSQL qui peuvent traîner et bloquer une reinstall.
    # Inclut aussi les EXE de l'installeur lui-meme (WX*PACKHFSQLCS.exe) :
    # PCSoft pose un mutex pendant l'install et un process orphelin peut
    # faire que la nouvelle invocation rate silencieusement (exit 0, pas de
    # Install.log produit).
    HFSQL_PROCESSES = (
        "MantaManager64.exe", "MantaManager.exe", "Manta64.exe", "Manta.exe",
        "hflogger64.exe", "hflogger.exe", "hfmailer64.exe", "hfmailer.exe",
        "WX310PACKHFSQLCS.exe", "WX280PACKHFSQLCS.exe",
    )

    def _kill_hfsql_processes(self):
        # Taskkill /F sur tous les processus HFSQL connus. Idempotent : retourne
        # code 128 si pas de process correspondant. Bloque jusqu'a ce qu'ils
        # soient vraiment morts (taskkill /F est synchrone).
        for name in self.HFSQL_PROCESSES:
            try:
                subprocess.run(
                    ["taskkill", "/F", "/IM", name],
                    creationflags=CREATE_NO_WINDOW, timeout=10,
                )
            except Exception:
                pass

    def _wait_for_clean_state(self, install_path=None, max_wait_s=30):
        # Apres une desinstallation, on doit attendre que :
        #   1. Le service MantaManager n'existe plus (sc.exe le supprime mais
        #      Windows SCM peut garder la trace jusqu'a ce que tous les handles
        #      soient liberes - si on lance New-Service trop tot ca echoue)
        #   2. Aucun processus HFSQL n'est en cours
        #   3. Le dossier d'install est vide ou supprime (sinon l'installeur
        #      detecte un install existant et bascule en mode 'repair' qui rate)
        # Poll toutes les 1s, timeout max_wait_s. Retourne True si propre, False sinon.
        import time as _t
        deadline = _t.time() + max_wait_s
        while _t.time() < deadline:
            svc = self._detect_service()
            procs_running = self._any_hfsql_process_running()
            dir_dirty = (install_path and Path(install_path).is_dir()
                         and any(Path(install_path).iterdir()))
            if not svc and not procs_running and not dir_dirty:
                return True
            _t.sleep(1)
        # Timeout : on retourne ce qu'on a comme info dans les logs
        if svc:
            self.logger.warn(f"Apres {max_wait_s}s : le service {svc.get('name')} existe encore")
        if procs_running:
            self.logger.warn(f"Apres {max_wait_s}s : des processus HFSQL tournent encore")
        if dir_dirty:
            self.logger.warn(f"Apres {max_wait_s}s : le dossier {install_path} contient encore des fichiers")
        return False

    def _any_hfsql_process_running(self):
        # Check rapide via tasklist + grep
        try:
            result = subprocess.run(
                ["tasklist", "/FO", "CSV", "/NH"],
                capture_output=True, text=True,
                creationflags=CREATE_NO_WINDOW, timeout=10,
            )
            if result.returncode != 0:
                return False
            names_low = {n.lower() for n in self.HFSQL_PROCESSES}
            for line in result.stdout.splitlines():
                # CSV : "image","pid","session",...
                parts = [p.strip('"') for p in line.split('","')]
                if not parts:
                    continue
                image = parts[0].lstrip('"').lower()
                if image in names_low:
                    return True
        except Exception:
            pass
        return False

    def update(self, vega_root, port=DEFAULT_HFSQL_PORT, version=DEFAULT_HFSQL_VERSION,
               on_progress=None):
        # Mise a jour sur place (MAJ=1) : pas de uninstall, pas de purge.
        # L'installeur PCSoft remplace les binaires et DLL en gardant les
        # bases de donnees et la configuration. Le service est arrete /
        # redemarre par l'installeur lui-meme. Plus rapide qu'une reinstall.
        notify = on_progress or (lambda *_a, **_k: None)
        vega_root = Path(vega_root)
        if not vega_root.is_dir():
            raise OperationError(f"Dossier Vega introuvable : {vega_root}")

        installer = self.download_installer(version=version, on_progress=on_progress)
        ini_path = self._build_ini(
            repertoire=str(vega_root),
            port=port,
            maj=1,  # 1 = mise a jour
        )
        log_path = Path(tempfile.gettempdir()) / f"hfsql_update_{int(time.time())}.log"

        # Pre-flight : kill prophylactique des processus residuels qui
        # pourraient bloquer la mise a jour des binaires.
        self._kill_hfsql_processes()

        notify("update", "Mise à jour silencieuse en cours...")
        self.logger.info(f"Lancement update HFSQL silent : repertoire={vega_root}, port={port}")
        self.logger.info(f"  INI : {ini_path}")
        self.logger.info(f"  LOG : {log_path}")
        rc = self._run_installer(installer, ini_path, log_path)
        self.logger.info(f"Installeur HFSQL retour (MAJ=1) : {rc}")

        notify("verify", "Vérification du service HFSQL (jusqu'à 60s)...")
        verification = self._verify_install_with_wait(
            port=port, log_path=log_path, repertoire=str(vega_root), max_wait_s=60,
        )
        self.logger.info(f"Verification post-update : {verification}")
        return {
            "version": version,
            "repertoire": str(vega_root),
            "port": port,
            "return_code": rc,
            "log_path": str(log_path),
            "verification": verification,
        }

    def uninstall_and_purge(self, version=DEFAULT_HFSQL_VERSION, vega_root=None,
                             on_progress=None):
        # Pipeline desinstallation complete :
        #   1. Detecte le service + chemin install
        #   2. Stop service + kill process (y compris EXE installeur orphelin)
        #   3. Lance silent uninstall (MAJ=3)
        #   4. Verifie l'Install.log du Repertoire (PCSoft = source de verite)
        #   5. Purge fichiers residuels + cle registre + service si toujours la
        # vega_root (optionnel) : chemin VEGAHF/VEGACS selectionne par l'UI.
        # Si fourni, on l'utilise comme Repertoire dans l'INI (sinon on bascule
        # sur l'install_location du registre).
        notify = on_progress or (lambda *_a, **_k: None)
        notify("detect", "Detection de l'installation HFSQL existante...")
        info = self.detect_install()
        if not info["installed"]:
            self.logger.info("Aucune installation HFSQL detectee, rien a desinstaller.")
            return {"already_clean": True}

        install_path = (info.get("registry") or {}).get("install_location")
        service_name = (info.get("service") or {}).get("name")
        # Le Repertoire passe a l'INI doit pointer sur le vrai dossier d'install
        # HFSQL. Priorite : vega_root explicite > install_path registre.
        repertoire_for_uninstall = str(vega_root) if vega_root else (install_path or None)
        self.logger.info(
            f"Detection : service={service_name}, install_path={install_path}, "
            f"repertoire_uninstall={repertoire_for_uninstall}"
        )

        # 1. Stop service + kill processus residuels HFSQL (y compris installeur)
        if service_name:
            notify("stop", f"Arret du service {service_name}...")
            self._stop_service(service_name)
        notify("kill_procs", "Fermeture des processus HFSQL residuels...")
        self._kill_hfsql_processes()

        # 2. Silent uninstall
        uninstall_log_ok = False
        uninstall_log_content = None
        try:
            installer = self.download_installer(version=version, on_progress=on_progress)
            # PCSoft doc : pour MAJ=3, si Repertoire ne pointe pas sur le vrai
            # dossier d'install l'installeur exit 0 sans rien faire. On passe
            # donc le chemin reel detecte.
            ini_path = self._build_ini(
                repertoire=repertoire_for_uninstall or "C:\\",
                port=DEFAULT_HFSQL_PORT,
                maj=3,
            )
            log_path = Path(tempfile.gettempdir()) / f"hfsql_uninstall_{int(time.time())}.log"
            notify("uninstall", "Lancement de la desinstallation silencieuse...")
            rc = self._run_installer(installer, ini_path, log_path)
            self.logger.info(f"Desinstalleur HFSQL retour : {rc}")
            # Verifie l'Install.log : si present et 'OK' = uninstall valide
            if repertoire_for_uninstall:
                inst_log = Path(repertoire_for_uninstall) / "Install.log"
                if inst_log.is_file():
                    try:
                        uninstall_log_content = inst_log.read_text(
                            encoding="utf-8", errors="ignore"
                        ).strip()[:500]
                        uninstall_log_ok = (uninstall_log_content.upper() == "OK")
                        self.logger.info(
                            f"Install.log uninstall : present, OK={uninstall_log_ok}, "
                            f"contenu={uninstall_log_content!r}"
                        )
                    except OSError:
                        pass
                else:
                    self.logger.warn(
                        f"Install.log absent dans {repertoire_for_uninstall} : "
                        "le desinstalleur PCSoft n'a peut-etre pas tourne (mutex ? "
                        "Repertoire invalide ?)"
                    )
        except Exception as exc:
            self.logger.warn(f"Silent uninstall echoue ({exc}), on continue avec la purge manuelle.")

        # 3. Purge residuels (fichiers + cle registre + service residuel)
        notify("purge", "Suppression des fichiers et cles residuels...")
        purged = self._purge_residuals(install_path=install_path, service_name=service_name)
        self.logger.info(f"Purge terminee : {purged}")

        # 4. Kill une 2e fois (l'installeur PCSoft peut avoir respawn des
        # processus pendant son cleanup)
        self._kill_hfsql_processes()

        # 5. Attente d'un etat propre : service vraiment supprime cote SCM +
        # plus aucun process HFSQL + dossier d'install vide. Sans ca, une
        # reinstall immediate peut rater silencieusement (cas du client :
        # 'le 1er install ne marche pas, le 2e oui').
        notify("wait", "Attente d'un état propre (jusqu'à 30s)...")
        clean = self._wait_for_clean_state(install_path=install_path, max_wait_s=30)
        self.logger.info(f"Etat propre atteint : {clean}")
        return {
            "already_clean": False,
            "purged": purged,
            "clean_state": clean,
            "uninstall_log_ok": uninstall_log_ok,
            "uninstall_log_content": uninstall_log_content,
        }

    def _verify_install_with_wait(self, port=DEFAULT_HFSQL_PORT, log_path=None,
                                  repertoire=None, max_wait_s=60):
        # Poll verify_install jusqu'a service_ok+port_ok ou timeout. Sur PC
        # lent, le service peut prendre 20-30s a demarrer apres la fin de
        # l'installeur. On evite ainsi un faux negatif qui declencherait un
        # retry inutile (cas client : install OK mais Toolbox croit que ca a
        # rate parce que le service n'est pas encore monte).
        deadline = time.time() + max_wait_s
        verification = None
        while time.time() < deadline:
            verification = self.verify_install(port=port, log_path=log_path, repertoire=repertoire)
            if verification.get("service_ok") and verification.get("port_ok"):
                return verification
            time.sleep(2)
        # Timeout : retourne le dernier resultat (probablement service_ok=False)
        return verification or self.verify_install(port=port, log_path=log_path, repertoire=repertoire)

    def verify_install(self, port=DEFAULT_HFSQL_PORT, log_path=None, repertoire=None):
        # Verification cascade :
        #   - install_log_ok : <Repertoire>\Install.log contient literalement "OK"
        #     (la source de verite officielle PCSoft, doc.pcsoft.fr/?8010003).
        #     Si le fichier n'existe pas, l'installeur n'a meme pas demarre
        #     correctement (mutex ? droits ? Repertoire invalide ?).
        #   - service_ok : service MantaManager existe et tourne
        #   - port_ok : port TCP 4900 accepte des connexions
        #   - log_ok : pas de mention 'erreur/error' dans le /LOG installeur
        # Le verdict 'overall_ok' exige les 4 conditions.
        result = {
            "install_log_present": False,
            "install_log_ok": False,
            "install_log_content": None,
            "service_ok": False,
            "port_ok": False,
            "log_ok": True,
            "service_name": None,
            "log_errors": [],
        }

        # 1. Install.log dans le Repertoire (source de verite PCSoft)
        if repertoire:
            install_log = Path(repertoire) / "Install.log"
            if install_log.is_file():
                result["install_log_present"] = True
                try:
                    content = install_log.read_text(encoding="utf-8", errors="ignore").strip()
                except OSError:
                    content = ""
                result["install_log_content"] = content[:500]
                # PCSoft ecrit literalement "OK" sur succes
                result["install_log_ok"] = (content.upper() == "OK")

        # 2. Service MantaManager
        svc = self._detect_service()
        if svc:
            result["service_name"] = svc.get("name")
            result["service_ok"] = (svc.get("status") == "Running")

        # 3. Port TCP
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=3) as _s:
                result["port_ok"] = True
        except OSError:
            result["port_ok"] = False

        # 4. /LOG installeur (PCSoft) — scan d'erreurs
        if log_path:
            log_path = Path(log_path)
            if log_path.is_file():
                try:
                    content = log_path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    content = ""
                err_lines = []
                for line in content.splitlines():
                    low = line.lower()
                    if any(kw in low for kw in ("erreur", "error", "echec", "failed", "failure")):
                        err_lines.append(line.strip()[:200])
                result["log_errors"] = err_lines[:10]
                result["log_ok"] = len(err_lines) == 0

        # Verdict global : on se fie a la realite operationnelle
        # (service Running + port TCP accessible). L'Install.log de PCSoft
        # est informatif mais PAS bloquant : en pratique, dans certains cas
        # (PC lent, structure Repertoire imbriquee, encodage atypique) le
        # fichier peut etre absent ou ne pas contenir exactement 'OK' alors
        # que l'installation a parfaitement reussi (service tourne, port
        # repond). On ne veut pas dire 'echec' a l'utilisateur dans ce cas.
        result["overall_ok"] = (
            result["service_ok"] and result["port_ok"] and result["log_ok"]
        )
        return result

    # -------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------

    def _build_ini_custom(self, params):
        # Construit le .INI a partir d'un dict de parametres avances.
        plateforme = int(params.get("plateforme") or 1)
        maj = int(params.get("maj") or 2)
        cchf = 1 if params.get("cchf") else 0
        serveur = params.get("serveur") or "SERVEUR"
        machine = params.get("machine") or serveur
        port = int(params.get("port") or DEFAULT_HFSQL_PORT)
        repertoire = params.get("repertoire") or "C:\\"
        ini = (
            "[PILOTAGE]\r\n"
            f"Plateforme={plateforme}\r\n"
            f"MAJ={maj}\r\n"
            f"CCHF={cchf}\r\n"
            "\r\n"
            "[SERVEUR]\r\n"
            f"Serveur={serveur}\r\n"
            f"Port={port}\r\n"
            f"Repertoire={repertoire}\r\n"
            "\r\n"
            "[MACHINE]\r\n"
            f"Nom={machine}\r\n"
        )
        import time as _time
        path = Path(tempfile.gettempdir()) / f"hfsql_silent_adv_{maj}_{int(_time.time())}.ini"
        path.write_text(ini, encoding="ascii")
        return path

    def _build_ini(self, repertoire, port, maj):
        # Construit le .INI silent. Le user a dit "nom serveur par defaut"
        # donc on utilise le hostname courant (c'est le default attendu par
        # l'installeur HFSQL).
        try:
            hostname = socket.gethostname()
        except Exception:
            hostname = "SERVEUR"
        # Note langue : Langue=FR / LCID=1036 ne sont PAS documentes dans la
        # doc PCSoft (?8010003) - le langage est en realite porte par le binaire
        # de l'installeur (les portails FR et EN de download.windev.com servent
        # des builds differentes). On ajoute ces cles en pari low-cost : si
        # PCSoft les ignore, aucun impact. La VRAIE solution si l'installation
        # finit en anglais est de remplacer le binaire heberge sur
        # telechargement.quatuhore.fr par la build FR depuis le portail FR
        # PCSoft (https://download.windev.com/fr/).
        ini = (
            "[PILOTAGE]\r\n"
            "Plateforme=1\r\n"
            f"MAJ={maj}\r\n"
            "CCHF=1\r\n"
            "Langue=FR\r\n"
            "LCID=1036\r\n"
            "\r\n"
            "[SERVEUR]\r\n"
            f"Serveur={hostname}\r\n"
            f"Port={port}\r\n"
            f"Repertoire={repertoire}\r\n"
            "\r\n"
            "[MACHINE]\r\n"
            f"Nom={hostname}\r\n"
            "\r\n"
            "[LANGUE]\r\n"
            "Defaut=FR\r\n"
        )
        path = Path(tempfile.gettempdir()) / f"hfsql_silent_{maj}_{int(time.time())}.ini"
        path.write_text(ini, encoding="ascii")
        return path

    def _run_installer(self, installer_path, ini_path, log_path):
        # Lance l'installeur en mode silent, attend la fin, retourne rc.
        # /Silent=<ini> /LOG=<log>
        # Pre-flight langue : le binaire WX*PACKHFSQLCS.exe est multilingue
        # mais en silent il default a l'anglais. PCSoft NE documente AUCUNE
        # cle INI / flag CLI pour la langue. Les deux mecanismes probables
        # (best-effort, non documentes) :
        #   1. Forcer la langue UI du thread parent a fr-FR (1036) - les
        #      sous-processus heritent du thread UI language a la creation.
        #   2. Pre-amorcer HKLM/HKCU\Software\PC SOFT\WDSetup avec une cle
        #      "Langue=FR" - WDSetup consulte cette branche au boot.
        # Si PCSoft ignore tout, aucun impact.
        self._force_french_for_installer()
        args = [
            str(installer_path),
            f"/Silent={ini_path}",
            f"/LOG={log_path}",
        ]
        proc = subprocess.run(
            args,
            creationflags=CREATE_NO_WINDOW,
            timeout=900,  # 15 min max
        )
        return proc.returncode

    def _force_french_for_installer(self):
        # (a) Thread UI language -> fr-FR (LCID 1036 = 0x040C)
        try:
            ctypes.windll.kernel32.SetThreadUILanguage(0x040C)
        except Exception:
            pass
        # (b) Seed HKLM/HKCU\Software\PC SOFT\* avec candidats
        candidates_str = (
            ("Langue", "FR"),
            ("LangueDefaut", "FR"),
            ("Language", "French"),
            ("Lang", "fr"),
        )
        candidates_dword = (
            ("IDLangue", 1036),
            ("LCID", 1036),
        )
        paths = (
            r"Software\PC SOFT\WDSetup",
            r"Software\PC SOFT",
        )
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for path in paths:
                try:
                    with winreg.CreateKey(hive, path) as key:
                        for name, val in candidates_str:
                            try:
                                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, val)
                            except OSError:
                                pass
                        for name, val in candidates_dword:
                            try:
                                winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, val)
                            except OSError:
                                pass
                except OSError:
                    # HKLM peut refuser si pas admin - on continue avec HKCU
                    pass

    def _stop_service(self, name):
        script = (
            f"$ErrorActionPreference='SilentlyContinue'; "
            f"Stop-Service -Name '{name.replace(chr(39), chr(39)*2)}' -Force"
        )
        powershell_output(script)

    def _purge_residuals(self, install_path=None, service_name=None):
        # Supprime : service (si encore la), cle registre Uninstall, dossier
        # d'installation, et tous les MantaManager64.exe residuels dans les
        # paths typiques PC SOFT.
        purged = {
            "service_deleted": False,
            "registry_deleted": False,
            "install_dir_deleted": False,
            "extra_dirs_deleted": [],
        }

        # 1. Suppression service residuel
        if service_name:
            try:
                subprocess.run(
                    ["sc.exe", "delete", service_name],
                    creationflags=CREATE_NO_WINDOW, timeout=30,
                )
                purged["service_deleted"] = True
            except Exception as exc:
                self.logger.warn(f"sc.exe delete {service_name} echoue : {exc}")

        # 2. Cles registre
        for hive_path in (HFSQL_UNINSTALL_KEY, HFSQL_UNINSTALL_KEY_WOW):
            try:
                winreg.DeleteKey(winreg.HKEY_LOCAL_MACHINE, hive_path)
                purged["registry_deleted"] = True
            except FileNotFoundError:
                pass
            except OSError as exc:
                self.logger.warn(f"Suppression cle registre {hive_path} echoue : {exc}")

        # 3. Dossier d'install (depuis le registre si on l'avait)
        if install_path:
            p = Path(install_path)
            if p.is_dir():
                try:
                    self._rmtree_force(p)
                    purged["install_dir_deleted"] = True
                except Exception as exc:
                    self.logger.warn(f"Suppression {p} echoue : {exc}")

        # 4. Dossiers PC SOFT residuels (sous-dossiers HFSQL uniquement,
        # on ne touche pas au reste de PC SOFT s'il y a WinDev installe).
        for hint in RESIDUAL_PATH_HINTS:
            base = Path(hint)
            if not base.is_dir():
                continue
            try:
                for sub in base.iterdir():
                    if sub.is_dir() and ("hfsql" in sub.name.lower() or "hyper file" in sub.name.lower()):
                        try:
                            self._rmtree_force(sub)
                            purged["extra_dirs_deleted"].append(str(sub))
                        except Exception as exc:
                            self.logger.warn(f"Suppression {sub} echoue : {exc}")
            except (OSError, PermissionError):
                continue

        return purged

    def clean_vega_root_after_uninstall(self, vega_root):
        # Nettoyage du dossier VEGAHF / VEGACS APRES desinstallation HFSQL.
        # Regles :
        #   - A la racine : tout doit etre supprime SAUF le dossier BDD
        #     (les bases Vega).
        #   - Dans BDD : ne toucher QU'AUX elements caches (attribut HIDDEN)
        #     ET dont le nom commence par '__' (style HFSQL : __JNL,
        #     __JNLBackup, __System, __TRS).
        #
        # Robustesse : on tente 3 passes avec kill_processes + sleep entre
        # chaque, car les DLL HFSQL (wd310*.dll, wdhfsrv64.dll, wdsqlsrv64.dll,
        # manta64.exe) peuvent etre encore mappees en memoire quand on appelle
        # juste apres l'uninstall. On verifie ensuite ce qui reste.
        result = {
            "root_items_deleted": [],
            "bdd_items_deleted": [],
            "root_items_remaining": [],
            "errors": [],
            "passes": 0,
        }
        root = Path(vega_root)
        if not root.is_dir():
            return result

        for attempt in range(3):
            result["passes"] = attempt + 1
            # Kill prophylactique avant chaque passe pour liberer DLL/exe
            if attempt > 0:
                self._kill_hfsql_processes()
                time.sleep(2)

            # --- Racine : tout sauf BDD ---
            still_locked = False
            try:
                for entry in list(root.iterdir()):
                    if entry.name.upper() == "BDD":
                        continue
                    ok, err = self._delete_robust(entry)
                    if ok:
                        if entry.name not in result["root_items_deleted"]:
                            result["root_items_deleted"].append(entry.name)
                    else:
                        still_locked = True
                        if attempt == 2:
                            result["errors"].append(f"{entry.name} : {err}")
                            self.logger.warn(f"Suppression {entry} echoue : {err}")
            except (OSError, PermissionError) as exc:
                result["errors"].append(f"iter racine : {exc}")

            # --- BDD : uniquement les '__*' caches ---
            bdd = root / "BDD"
            if bdd.is_dir():
                try:
                    for entry in list(bdd.iterdir()):
                        if not entry.name.startswith("__"):
                            continue
                        if not self._is_hidden(entry):
                            # Garde-fou : pas cache = ne pas toucher
                            continue
                        ok, err = self._delete_robust(entry)
                        if ok:
                            if entry.name not in result["bdd_items_deleted"]:
                                result["bdd_items_deleted"].append(entry.name)
                        else:
                            still_locked = True
                            if attempt == 2:
                                result["errors"].append(f"BDD/{entry.name} : {err}")
                except (OSError, PermissionError) as exc:
                    result["errors"].append(f"iter BDD : {exc}")

            if not still_locked:
                break

        # Bilan : ce qui reste a la racine (a part BDD)
        try:
            for entry in root.iterdir():
                if entry.name.upper() == "BDD":
                    continue
                result["root_items_remaining"].append(entry.name)
        except (OSError, PermissionError):
            pass
        return result

    def _delete_robust(self, path):
        # Supprime un fichier ou dossier avec gestion explicite des erreurs.
        # Retourne (success_bool, error_message_or_None).
        # Strategie : Python d'abord (rapide, raise sur lock), puis fallback
        # PowerShell Remove-Item -Force (peut casser quelques cas Python rate).
        try:
            if path.is_dir():
                # rmtree manuel pour avoir des erreurs explicites
                import shutil
                shutil.rmtree(path, ignore_errors=False, onerror=_on_rmtree_err)
            else:
                # Tente de retirer le flag read-only puis unlink
                try:
                    import stat
                    path.chmod(path.stat().st_mode | stat.S_IWRITE)
                except Exception:
                    pass
                path.unlink(missing_ok=True)
            if not path.exists():
                return True, None
        except Exception as exc_py:
            # Fallback PowerShell
            try:
                self._rmtree_force(path)
                if not path.exists():
                    return True, None
                return False, str(exc_py)
            except Exception as exc_ps:
                return False, f"{exc_py} / PS: {exc_ps}"
        return False, "Existe encore apres suppression"

    @staticmethod
    def _is_hidden(path):
        # Test attribut Windows HIDDEN (0x2). On utilise GetFileAttributesW
        # directement plutot que stat().st_file_attributes pour rester
        # compatible meme si le path contient des liens.
        FILE_ATTRIBUTE_HIDDEN = 0x2
        INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
        try:
            attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
            if attrs == INVALID_FILE_ATTRIBUTES:
                return False
            return bool(attrs & FILE_ATTRIBUTE_HIDDEN)
        except Exception:
            return False

    def _rmtree_force(self, path):
        # PowerShell Remove-Item -Recurse -Force : plus tolerant que shutil.rmtree
        # sur les fichiers en lecture seule ou ouverts.
        path_str = str(path).replace("'", "''")
        script = (
            f"$ErrorActionPreference='SilentlyContinue'; "
            f"Remove-Item -Path '{path_str}' -Recurse -Force"
        )
        powershell_output(script)
