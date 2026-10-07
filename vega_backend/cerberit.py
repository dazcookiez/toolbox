"""Installation et configuration silencieuse de CerberIT (Kiwi Backup en marque
blanche, editee par Logi Diffusion).

L'installeur telecharge est un paquet NSIS signe par kiwi-backup.com :
  - Install silencieuse : CERBERIT_x.y.z.exe /S  (option /D=dossier possible)
  - Il depose kiwi.exe + config.json (serveur/tenant deja predefinis) dans
    C:\\Program Files\\CERBERIT\\CERBERIT et cree le service Windows "CERBERIT".

L'agent kiwi.exe expose une CLI (elevation requise) :
  - Enregistrement : kiwi.exe install --name <nom> --key <cle_contrat> [--server ...] [--jobid backup1]
  - Sauvegarde     : kiwi.exe backup --jobid <job>

La configuration du jeu de sauvegarde (dossiers inclus, motifs d'exclusion,
planning) est stockee en YAML dans :
  C:\\ProgramData\\Kiwi-Backup\\backup\\kiwi.conf
On la modifie APRES l'enregistrement pour ne pas ecraser clientid/clientkey.

NOTE : kiwi.exe exige les droits administrateur. Vega Toolbox tourne deja
eleve (uac_admin), donc les appels CLI n'entrainent pas de nouvelle invite UAC.
"""
import os
import shutil
import socket
import subprocess
import winreg
from pathlib import Path
from urllib.request import Request, urlopen

import yaml

from ._common import (
    CREATE_NO_WINDOW,
    DOWNLOAD_USER_AGENT,
    OperationError,
    command_output,
    is_admin,
)


# Source de l'installeur (heberge sur le serveur de telechargement interne).
CERBERIT_INSTALLER_URL = "https://telechargement.quatuhore.fr/Cerberit/V5/CERBERIT_5.0.31.exe"
CERBERIT_INSTALLER_NAME = "CERBERIT_5.0.31.exe"

# Emplacements poses par l'installeur.
CERBERIT_INSTALL_DIR = Path(r"C:\Program Files\CERBERIT\CERBERIT")
CERBERIT_EXE = CERBERIT_INSTALL_DIR / "kiwi.exe"
CERBERIT_UNINSTALLER = CERBERIT_INSTALL_DIR / "uninstall.exe"
CERBERIT_CONF = Path(r"C:\ProgramData\Kiwi-Backup\backup\kiwi.conf")
CERBERIT_LOG = Path(r"C:\ProgramData\Kiwi-Backup\backup\kiwi.log")
CERBERIT_DATA_DIR = Path(r"C:\ProgramData\Kiwi-Backup")
CERBERIT_SERVICE = "CERBERIT"
# Port TCP de transfert des donnees vers le serveur Kiwi (doc troubleshooting :
# un blocage du 4443 est une cause frequente d'echec de sauvegarde).
CERBERIT_PORT = 4443

# Parametres predefinis (le serveur/tenant sont deja dans config.json de
# l'installeur, on garde le serveur ici pour le passer a --server par securite).
CERBERIT_SERVER = "pool4.sauvegardes.org"
DEFAULT_JOBID = "backup1"
DEFAULT_EXCLUDE_PATTERNS = ["*.log", "*.log*", "*.bak", "*.old"]

_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


class CerberitManager:
    def __init__(self, logger):
        self.logger = logger

    # ------------------------------------------------------------------ detection

    def detect_install(self):
        # Etat de l'installation CerberIT : binaire present, service, enregistrement.
        info = {
            "installed": CERBERIT_EXE.is_file(),
            "exe_path": str(CERBERIT_EXE),
            "service": self._service_status(),
            "registered": False,
            "jobs": [],
            "conf_present": CERBERIT_CONF.is_file(),
        }
        conf = self._read_conf_safe()
        if conf:
            for jobid, job in conf.items():
                if not isinstance(job, dict) or jobid == "___":
                    continue
                params = job.get("params") or {}
                registered = bool(params.get("registered"))
                if registered:
                    info["registered"] = True
                info["jobs"].append({
                    "jobid": jobid,
                    "registered": registered,
                    "include": ((job.get("files") or {}).get("include") or []),
                })
        return info

    def _service_status(self):
        # Interroge le service Windows "CERBERIT". Retourne "running"/"stopped"/None.
        script = (
            f"$s = Get-Service -Name '{CERBERIT_SERVICE}' -ErrorAction SilentlyContinue;"
            "if ($s) { \"$($s.Status)\" } else { 'NONE' }"
        )
        try:
            result = command_output([
                "powershell", "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-Command", script,
            ])
        except Exception:
            return None
        out = (result.stdout or "").strip().lower()
        if not out or out == "none":
            return None
        return "running" if out == "running" else "stopped"

    # ------------------------------------------------------------------ install

    def download_installer(self, on_progress=None):
        notify = on_progress or (lambda _s, _m: None)
        target = Path(os.environ.get("TEMP", ".")) / CERBERIT_INSTALLER_NAME
        # Cache : reutilise si deja telecharge et de taille plausible (> 1 Mo).
        if target.exists() and target.stat().st_size > 1_000_000:
            notify("cache", f"Installeur deja present ({target.stat().st_size // (1024*1024)} Mo).")
            return target
        notify("download", "Telechargement de l'installeur CerberIT...")
        req = Request(CERBERIT_INSTALLER_URL, headers={"User-Agent": DOWNLOAD_USER_AGENT})
        try:
            with urlopen(req, timeout=300) as resp, open(target, "wb") as out:
                shutil.copyfileobj(resp, out, length=1024 * 1024)
        except Exception as exc:
            raise OperationError(f"Telechargement de l'installeur CerberIT echoue : {exc}") from exc
        if not target.exists() or target.stat().st_size < 1_000_000:
            raise OperationError(f"Installeur CerberIT invalide (taille suspecte) : {target}")
        notify("download", f"Installeur telecharge : {target.name}")
        return target

    def install_silent(self, on_progress=None):
        # Telecharge puis lance l'installeur NSIS en silencieux (/S). Verifie
        # ensuite la presence de kiwi.exe et du service.
        if not is_admin():
            raise OperationError("L'installation CerberIT requiert les droits administrateur.")
        notify = on_progress or (lambda _s, _m: None)
        installer = self.download_installer(on_progress=on_progress)
        notify("install", "Installation silencieuse de CerberIT (/S)...")
        self.logger.info(f"Lancement installeur CerberIT : {installer} /S")
        try:
            result = subprocess.run(
                [str(installer), "/S"],
                capture_output=True, text=True, timeout=600,
                creationflags=CREATE_NO_WINDOW,
            )
        except subprocess.TimeoutExpired as exc:
            raise OperationError("Installation CerberIT : timeout (10 min).") from exc
        except Exception as exc:
            raise OperationError(f"Installation CerberIT impossible : {exc}") from exc
        # NSIS silencieux : le process de tete rend la main immediatement mais
        # continue en arriere-plan. On attend l'apparition de kiwi.exe.
        if not self._wait_for(lambda: CERBERIT_EXE.is_file(), timeout=120):
            raise OperationError(
                f"kiwi.exe introuvable apres installation (code {result.returncode}). "
                "L'installeur n'a peut-etre pas termine."
            )
        notify("install", "CerberIT installe (kiwi.exe present).")
        return {"installed": True, "return_code": result.returncode}

    @staticmethod
    def _wait_for(predicate, timeout=60, interval=2):
        import time
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if predicate():
                    return True
            except Exception:
                pass
            time.sleep(interval)
        try:
            return bool(predicate())
        except Exception:
            return False

    def find_registry_uninstaller(self):
        # Cherche la commande de desinstallation declaree par CerberIT / Kiwi
        # dans la base de registre (HKLM et HKCU, vues 64 et 32 bits).
        # Sert de repli quand uninstall.exe a disparu du dossier d'installation.
        bases = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        ]
        for hive, path in bases:
            try:
                root = winreg.OpenKey(hive, path)
            except OSError:
                continue
            try:
                index = 0
                while True:
                    try:
                        sub_name = winreg.EnumKey(root, index)
                    except OSError:
                        break
                    index += 1
                    try:
                        with winreg.OpenKey(root, sub_name) as sub:
                            values = {}
                            i = 0
                            while True:
                                try:
                                    n, v, _k = winreg.EnumValue(sub, i)
                                except OSError:
                                    break
                                i += 1
                                if isinstance(v, str):
                                    values[n] = v
                    except OSError:
                        continue
                    display = (values.get("DisplayName") or "").lower()
                    if "cerberit" in display or "kiwi" in display:
                        return {
                            "display_name": values.get("DisplayName") or "CerberIT",
                            "uninstall_string": values.get("UninstallString") or "",
                            "quiet_uninstall_string": values.get("QuietUninstallString") or "",
                            "install_location": values.get("InstallLocation") or "",
                            "registry_key": (hive, path + "\\" + sub_name),
                        }
            finally:
                winreg.CloseKey(root)
        return None

    def _stop_service(self):
        # Arret best-effort du service, pour liberer les binaires avant suppression.
        try:
            command_output([
                "powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-Command",
                f"Stop-Service -Name '{CERBERIT_SERVICE}' -Force -ErrorAction SilentlyContinue",
            ])
        except Exception:
            pass

    def _manual_purge(self, notify):
        # Dernier recours : aucun desinstalleur exploitable. On demonte
        # l'installation a la main, dans l'ordre qui evite les verrous.
        report = {"service_deleted": False, "processes_killed": False,
                  "install_dir_deleted": False, "registry_deleted": False}

        notify("uninstall", "Arrêt des processus CerberIT...")
        try:
            command_output([
                "powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-Command",
                "Get-Process -Name 'kiwi','CERBERIT' -ErrorAction SilentlyContinue | "
                "Stop-Process -Force -ErrorAction SilentlyContinue",
            ])
            report["processes_killed"] = True
        except Exception:
            pass

        notify("uninstall", "Suppression du service Windows...")
        try:
            result = command_output(["sc", "delete", CERBERIT_SERVICE])
            # 0 = supprime ; 1060 = service inexistant (deja propre)
            report["service_deleted"] = result.returncode in (0, 1060)
        except Exception:
            pass

        notify("uninstall", "Suppression des fichiers d'installation...")
        target = CERBERIT_INSTALL_DIR.parent if CERBERIT_INSTALL_DIR.parent.name.upper() == "CERBERIT" \
            else CERBERIT_INSTALL_DIR
        if target.exists():
            self._wait_for(lambda: True, timeout=2, interval=2)  # laisse le service se liberer
            try:
                shutil.rmtree(target, ignore_errors=True)
            except Exception as exc:
                self.logger.warn(f"Suppression de {target} partielle : {exc}")
        report["install_dir_deleted"] = not target.exists()

        entry = self.find_registry_uninstaller()
        if entry:
            hive, key_path = entry["registry_key"]
            try:
                winreg.DeleteKey(hive, key_path)
                report["registry_deleted"] = True
            except OSError as exc:
                self.logger.warn(f"Entrée de registre non supprimée : {exc}")
        return report

    def uninstall(self, on_progress=None, remove_data=False):
        # Desinstallation en CASCADE, pour ne jamais rester bloque :
        #   1. uninstall.exe /S            (voie normale, NSIS)
        #   2. commande declaree en base de registre (si le fichier a disparu)
        #   3. purge manuelle : processus, service, fichiers, registre
        # remove_data=True supprime aussi C:\ProgramData\Kiwi-Backup.
        if not is_admin():
            raise OperationError("La désinstallation CerberIT requiert les droits administrateur.")
        notify = on_progress or (lambda _s, _m: None)

        # Rien a faire : ni binaire, ni desinstalleur, ni service.
        if not CERBERIT_EXE.is_file() and not CERBERIT_UNINSTALLER.is_file() \
                and self._service_status() is None:
            notify("uninstall", "CerberIT n'est pas installé.")
            return {"uninstalled": True, "already_absent": True}

        self._stop_service()
        method = None
        command = None

        if CERBERIT_UNINSTALLER.is_file():
            method = "uninstall.exe"
            command = [str(CERBERIT_UNINSTALLER), "/S"]
        else:
            entry = self.find_registry_uninstaller()
            raw = (entry or {}).get("quiet_uninstall_string") or (entry or {}).get("uninstall_string") or ""
            if raw:
                method = "registre"
                # NSIS : on force le mode silencieux si absent de la commande.
                command = raw if "/S" in raw.upper() else raw + " /S"
                self.logger.info(
                    f"uninstall.exe absent — repli sur la commande déclarée en base de registre "
                    f"({(entry or {}).get('display_name')})."
                )

        if command is not None:
            notify("uninstall", f"Désinstallation silencieuse de CerberIT (via {method})...")
            try:
                subprocess.run(
                    command, capture_output=True, text=True, timeout=300,
                    creationflags=CREATE_NO_WINDOW, shell=isinstance(command, str),
                )
            except subprocess.TimeoutExpired as exc:
                raise OperationError("Désinstallation CerberIT : timeout (5 min).") from exc
            except Exception as exc:
                self.logger.warn(f"Désinstalleur inutilisable ({exc}) — passage en purge manuelle.")
                command = None

        purge = None
        if command is None or not self._wait_for(lambda: not CERBERIT_EXE.is_file(), timeout=120):
            # Aucun desinstalleur, ou il n'a pas fait le travail : on purge.
            if command is not None:
                self.logger.warn(
                    "CerberIT toujours présent après le désinstalleur — purge manuelle."
                )
            else:
                self.logger.warn(
                    "Aucun désinstalleur exploitable (uninstall.exe absent et rien en base "
                    "de registre) — purge manuelle."
                )
            purge = self._manual_purge(notify)
            if CERBERIT_EXE.is_file():
                raise OperationError(
                    "CerberIT n'a pas pu être supprimé : des fichiers restent verrouillés. "
                    "Redémarrez le poste puis relancez la désinstallation."
                )

        self.logger.info("CerberIT désinstallé.")

        data_removed = False
        if remove_data and CERBERIT_DATA_DIR.exists():
            notify("uninstall", "Suppression de la configuration et des journaux...")
            try:
                shutil.rmtree(CERBERIT_DATA_DIR, ignore_errors=True)
                data_removed = not CERBERIT_DATA_DIR.exists()
            except Exception as exc:
                self.logger.warn(f"Suppression de {CERBERIT_DATA_DIR} partielle : {exc}")
        return {
            "uninstalled": True,
            "method": method or "purge manuelle",
            "manual_purge": purge,
            "data_removed": data_removed,
        }

    # ------------------------------------------------------------------ register

    def register(self, machine_name, contract_key, jobid=DEFAULT_JOBID, server=CERBERIT_SERVER):
        # Enregistre la machine : kiwi.exe install --name --key [--server] [--jobid].
        if not is_admin():
            raise OperationError("L'enregistrement CerberIT requiert les droits administrateur.")
        if not CERBERIT_EXE.is_file():
            raise OperationError("kiwi.exe introuvable : installez d'abord CerberIT.")
        if not (contract_key or "").strip():
            raise OperationError("La cle de contrat est obligatoire pour l'enregistrement.")
        if not (machine_name or "").strip():
            raise OperationError("Le nom de la machine est obligatoire.")

        cmd = [str(CERBERIT_EXE), "install",
               "--name", machine_name.strip(),
               "--key", contract_key.strip(),
               "--jobid", (jobid or DEFAULT_JOBID)]
        if server:
            cmd += ["--server", server]
        # On ne journalise PAS la cle de contrat (donnee sensible).
        self.logger.info(f"Enregistrement CerberIT : kiwi.exe install --name {machine_name} --jobid {jobid} (cle masquee)")
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=180,
                creationflags=CREATE_NO_WINDOW,
                cwd=str(CERBERIT_INSTALL_DIR),
            )
        except subprocess.TimeoutExpired as exc:
            raise OperationError("Enregistrement CerberIT : timeout (3 min).") from exc
        except Exception as exc:
            raise OperationError(f"Enregistrement CerberIT impossible : {exc}") from exc
        if result.returncode != 0:
            raise OperationError(
                f"Enregistrement refuse (code {result.returncode}) : "
                f"{(result.stderr or result.stdout or '').strip()[:300]}"
            )
        # Verifie que kiwi.conf marque bien le job comme registered.
        conf = self._read_conf_safe() or {}
        job = conf.get(jobid or DEFAULT_JOBID) or {}
        registered = bool((job.get("params") or {}).get("registered"))
        return {"registered": registered, "jobid": jobid, "output": (result.stdout or "").strip()[:500]}

    # ------------------------------------------------------------------ config kiwi.conf

    def apply_backup_config(self, jobid=DEFAULT_JOBID, include_paths=None,
                            exclude_patterns=None, hour=None, minute=0,
                            days=None, advanced=None):
        # Modifie le job dans kiwi.conf SANS ecraser l'enregistrement :
        #   - files.include        = dossiers/fichiers a sauvegarder
        #   - files.excludepattern = motifs a exclure (*.log ...)
        #   - scheduler.hour/min + jours de la semaine
        #   - advanced : dict {chemin.pointe: valeur} pour tous les autres champs
        conf = self._read_conf_safe()
        if conf is None:
            conf = {}
        job = conf.get(jobid)
        if not isinstance(job, dict):
            job = self._default_job(jobid)
            conf[jobid] = job

        files = job.setdefault("files", {})
        if include_paths is not None:
            files["include"] = list(include_paths)
        if exclude_patterns is not None:
            files["excludepattern"] = list(exclude_patterns)

        sched = job.setdefault("scheduler", {})
        if hour is not None:
            sched["hour"] = int(hour)
            sched["min"] = int(minute or 0)
        if days is not None:
            selected = set(days)
            for day in _DAYS:
                sched[day] = day in selected

        if advanced:
            for dotted, value in advanced.items():
                self._set_dotted(job, dotted, value)

        self._write_conf(conf)
        self.logger.info(
            f"Configuration CerberIT '{jobid}' mise a jour : "
            f"{len(files.get('include') or [])} element(s) inclus, "
            f"exclusions {files.get('excludepattern')}."
        )
        return {"jobid": jobid, "include": files.get("include"), "scheduler": sched}

    def run_backup_now(self, jobid=DEFAULT_JOBID):
        if not CERBERIT_EXE.is_file():
            raise OperationError("kiwi.exe introuvable : installez d'abord CerberIT.")
        self.logger.info(f"Lancement d'une sauvegarde CerberIT (job {jobid})...")
        try:
            result = subprocess.run(
                [str(CERBERIT_EXE), "backup", "--jobid", jobid],
                capture_output=True, text=True, timeout=3600,
                creationflags=CREATE_NO_WINDOW, cwd=str(CERBERIT_INSTALL_DIR),
            )
        except subprocess.TimeoutExpired as exc:
            raise OperationError("Sauvegarde CerberIT : timeout (1 h).") from exc
        except Exception as exc:
            raise OperationError(f"Sauvegarde CerberIT impossible : {exc}") from exc
        # En cas d'echec, on remonte aussi les dernieres erreurs du journal :
        # kiwi.exe ne documente pas de codes numeriques, le kiwi.log est la
        # vraie source d'explication.
        errors = []
        if result.returncode != 0:
            errors = [f"[{lvl}] {msg}" for lvl, _ts, msg in self.read_log(max_lines=60)
                      if lvl in ("ERROR", "WARNING")][-8:]
        return {
            "return_code": result.returncode,
            "output": (result.stdout or "").strip()[:1000],
            "stderr": (result.stderr or "").strip()[:1000],
            "log_errors": errors,
        }

    # ------------------------------------------------------------------ diagnostic

    def read_log(self, max_lines=60):
        # Lit la fin du kiwi.log et parse les lignes "[LEVEL] YYYY-... - message".
        # Retourne une liste [(level, timestamp, message)] (ordre chronologique).
        if not CERBERIT_LOG.is_file():
            return []
        try:
            lines = CERBERIT_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            return []
        parsed = []
        for line in lines[-max_lines:]:
            line = line.strip()
            if not line:
                continue
            level, ts, msg = "INFO", "", line
            if line.startswith("[") and "]" in line:
                level = line[1:line.index("]")].upper()
                rest = line[line.index("]") + 1:].strip()
                # "2026-07-30 16:19:54 - message"
                if " - " in rest:
                    ts, msg = rest.split(" - ", 1)
                else:
                    msg = rest
            parsed.append((level, ts, msg))
        return parsed

    def diagnose(self):
        # Batterie de controles pour comprendre pourquoi une sauvegarde ne se
        # fait pas. Retourne une liste de dicts {label, status, detail} avec
        # status dans {"ok", "warn", "fail"}. S'inspire des causes listees dans
        # la doc troubleshooting Kiwi (port 4443, connexion, conf, planning...).
        checks = []

        def add(label, status, detail=""):
            checks.append({"label": label, "status": status, "detail": detail})

        # 1. Binaire installe
        if CERBERIT_EXE.is_file():
            add("Agent installé", "ok", str(CERBERIT_EXE))
        else:
            add("Agent installé", "fail", "kiwi.exe introuvable — CerberIT n'est pas installé.")
            return checks  # inutile d'aller plus loin

        # 2. Service Windows
        svc = self._service_status()
        if svc == "running":
            add("Service Windows CERBERIT", "ok", "en cours d'exécution")
        elif svc == "stopped":
            add("Service Windows CERBERIT", "fail", "le service est arrêté (à démarrer)")
        else:
            add("Service Windows CERBERIT", "fail", "service absent — réinstaller peut aider")

        # 3. Fichier de config
        conf = self._read_conf_safe()
        if conf is None:
            add("Fichier kiwi.conf", "fail",
                "absent ou illisible (corruption ?) : réenregistrer la machine")
            return checks
        add("Fichier kiwi.conf", "ok", str(CERBERIT_CONF))

        # 4. Enregistrement + contenu du job
        any_registered = False
        any_include = False
        any_schedule = False
        for jobid, job in conf.items():
            if not isinstance(job, dict) or jobid == "___":
                continue
            params = job.get("params") or {}
            if params.get("registered"):
                any_registered = True
            files = job.get("files") or {}
            if files.get("include") or files.get("includepattern"):
                any_include = True
            sched = job.get("scheduler") or {}
            if sched.get("hour") is not None and any(sched.get(d) for d in _DAYS):
                any_schedule = True
        add("Machine enregistrée", "ok" if any_registered else "fail",
            "" if any_registered else "aucun job enregistré — vérifiez la clé de contrat")
        add("Éléments à sauvegarder", "ok" if any_include else "fail",
            "" if any_include else "aucun dossier/fichier sélectionné")
        add("Planning défini", "ok" if any_schedule else "warn",
            "" if any_schedule else "aucune heure/jour planifié (sauvegarde uniquement manuelle)")

        # 5. Connexion au serveur (port 4443)
        add(f"Connexion serveur ({CERBERIT_SERVER}:{CERBERIT_PORT})",
            *self._check_port(CERBERIT_SERVER, CERBERIT_PORT))

        # 6. Dernieres erreurs du journal
        errs = [(lvl, msg) for lvl, _ts, msg in self.read_log(max_lines=80)
                if lvl in ("ERROR", "WARNING")]
        if errs:
            last = "; ".join(m for _l, m in errs[-3:])
            add("Journal (dernières alertes)", "warn", last[:300])
        else:
            add("Journal", "ok", "aucune erreur récente")

        return checks

    @staticmethod
    def _check_port(host, port, timeout=5):
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return "ok", "connexion établie"
        except Exception as exc:
            return "fail", (
                f"injoignable ({exc}). Pare-feu/proxy bloquant le port {port} "
                "ou pas d'accès Internet."
            )

    # ------------------------------------------------------------------ helpers conf

    def _read_conf_safe(self):
        if not CERBERIT_CONF.is_file():
            return None
        try:
            data = yaml.safe_load(CERBERIT_CONF.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except Exception as exc:
            self.logger.warn(f"Lecture kiwi.conf impossible : {exc}")
            return None

    def _write_conf(self, conf):
        try:
            CERBERIT_CONF.parent.mkdir(parents=True, exist_ok=True)
            text = yaml.safe_dump(conf, default_flow_style=False, sort_keys=False, allow_unicode=True)
            CERBERIT_CONF.write_text(text, encoding="utf-8")
        except Exception as exc:
            raise OperationError(f"Ecriture kiwi.conf refusee : {exc}") from exc

    @staticmethod
    def _set_dotted(root, dotted, value):
        # Ecrit root["a"]["b"] = value pour dotted = "a.b" (cree les dicts au besoin).
        parts = dotted.split(".")
        node = root
        for part in parts[:-1]:
            nxt = node.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                node[part] = nxt
            node = nxt
        node[parts[-1]] = value

    @staticmethod
    def _default_job(jobid):
        # Squelette d'un job conforme a la structure kiwi.conf observee.
        return {
            "key": jobid,
            "files": {"exclude": [], "excludepattern": [], "include": [],
                      "includepattern": [], "expression": ""},
            "label": "Backup",
            "params": {"clientid": "", "clientkey": "", "registered": False,
                       "server": "", "total_size": 0, "total": 0, "active": 0},
            "scheduler": {"hour": None, "min": None, "monday": False, "tuesday": False,
                          "wednesday": False, "thursday": False, "friday": False,
                          "saturday": False, "sunday": False,
                          "backup_if_planning_missed": False, "repetition_min": None},
            "security": {"max_recv_speed": 0, "max_send_speed": 0},
            "shutdown_after_backup": False,
            "disable_vss": False,
            "skip_start_synchro_if_synced_within_seconds": 0,
            "full_scan_interval_seconds": 0,
            "remote_disk": [],
            "script": {"before_backup": "", "after_backup": ""},
            "hypervisor": [],
        }
