import ctypes
import datetime
import json
import shutil
from pathlib import Path

from ._common import (
    DLL_V5,
    EXE_V5,
    FIREWALL_TCP_PORT,
    OperationError,
    PKG_CONTENT_DIRS,
    PKG_SUFFIX,
    RETAILFORCE_CONFIG_PATH,
    STATE_DIR_NAME,
    SmtpTestError,
    ToolLogger,
    V6_TARGET_REL,
    copy_path,
    firewall_port_open,
    hide_windows_path,
    path_writable,
    powershell_output,
    remove_existing_path,
    windows_case_insensitive,
)

class MigrationAborted(Exception):
    # Levee quand l'utilisateur choisit d'annuler la migration face a un fichier
    # verrouille. Declenche un retour arriere complet dans run_migration.
    pass


class MigrationManager:
    HFSQL_CACHE_TTL_SECONDS = 5

    def __init__(self, logger):
        self.logger = logger
        self._firewall_cache = None
        self._firewall_cache_at = None
        self._hfsql_cache = None
        self._hfsql_cache_at = None
        # Gestion des anomalies pendant une migration (fichier verrouille OU
        # element attendu manquant, etc.) :
        #   _conflict_handler(kind, name, detail) -> "skip" | "skip_all" | "abort"
        #     kind : "locked" (fichier verrouille) | "missing" (element manquant)
        #   _ignore_problems : True apres un "skip_all" (ignore toutes les suivantes)
        self._conflict_handler = None
        self._ignore_problems = False

    def invalidate_hfsql_cache(self):
        self._hfsql_cache = None
        self._hfsql_cache_at = None

    def validate_root(self, root):
        root = Path(root).resolve()
        if not root.exists() or not root.is_dir():
            raise OperationError(f"Dossier Vega introuvable: {root}")

        v6 = windows_case_insensitive(root, V6_TARGET_REL)
        vega_dos = v6.parent
        if not vega_dos.exists() or not vega_dos.is_dir():
            raise OperationError(
                f"Dossier {vega_dos} introuvable. Ce n'est pas le bon moment pour lancer le transfert."
            )
        if not v6.exists() or not v6.is_dir():
            raise OperationError(f"Dossier {v6} introuvable. Une étape précédente est manquante.")
        return v6

    def firewall_rule_ok(self):
        # Petit cache pour éviter de ralentir l'interface à chaque contrôle.
        now = datetime.datetime.now()
        if self._firewall_cache_at and (now - self._firewall_cache_at).total_seconds() < 15:
            return bool(self._firewall_cache)
        try:
            firewall_ok = firewall_port_open(FIREWALL_TCP_PORT)
            self._firewall_cache = firewall_ok
            self._firewall_cache_at = now
            return firewall_ok
        except Exception:
            self._firewall_cache = False
            self._firewall_cache_at = now
            return False

    def hfsql_service_status(self, force_refresh=False):
        # Interroge l'etat des services dont le DisplayName commence par "Hyper File Server".
        # Le nom exact contient le hostname de la machine : on filtre par prefixe.
        # Cache court (5s) : appel PowerShell evite si la verif vient d'etre faite.
        # force_refresh=True (utilise apres un Stop/Start) court-circuite le cache.
        now = datetime.datetime.now()
        if (
            not force_refresh
            and self._hfsql_cache is not None
            and self._hfsql_cache_at is not None
            and (now - self._hfsql_cache_at).total_seconds() < self.HFSQL_CACHE_TTL_SECONDS
        ):
            return dict(self._hfsql_cache)

        script = (
            "$svc = Get-Service | Where-Object { $_.DisplayName -like 'Hyper File Server*' } | "
            "Select-Object -First 1;"
            "if ($svc) { '{0}|{1}|{2}' -f $svc.Name, $svc.DisplayName, $svc.Status } else { 'NONE' }"
        )
        try:
            result = powershell_output(script)
        except Exception as exc:
            return {"found": False, "running": False, "error": str(exc)}
        stdout = (result.stdout or "").strip()
        if not stdout or stdout.upper() == "NONE":
            status_dict = {"found": False, "running": False, "name": None, "display_name": None, "status": None}
        else:
            parts = stdout.splitlines()[0].split("|")
            name = parts[0] if len(parts) > 0 else None
            display_name = parts[1] if len(parts) > 1 else None
            status = parts[2].strip() if len(parts) > 2 else None
            running = status is not None and status.lower() == "running"
            status_dict = {
                "found": True,
                "running": running,
                "name": name,
                "display_name": display_name,
                "status": status,
            }
        self._hfsql_cache = dict(status_dict)
        self._hfsql_cache_at = now
        return status_dict

    def retailforce_json_status(self):
        # Etats possibles :
        #   - exists=False           : fichier absent
        #   - error                  : JSON illisible
        #   - reset=True             : FiscalClients=[] (etat post-installation)
        #   - configured=True        : FiscalClients contient de vraies donnees client
        #   - is_test=True           : FiscalClients contient les donnees de DEMO
        #                              livrees par RetailForce, identifiees par
        #                              l'IDENTITE de l'entreprise (TestCompany,
        #                              Teststrasse, DevBrand, TestClient=true)
        #                              -> il faut reinitialiser.
        #                              NB : une apiKey 'test_*' seule NE suffit PAS :
        #                              un vrai client en TSE de test l'a aussi.
        # Le bon etat operationnel = reset OU configured.
        path = RETAILFORCE_CONFIG_PATH
        info = {"exists": False, "reset": False, "configured": False,
                "is_test": False, "path": str(path), "error": None,
                "test_markers": []}
        if not path.exists():
            return info
        info["exists"] = True
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            info["error"] = str(exc)
            return info
        clients = data.get("FiscalClients") if isinstance(data, dict) else None
        if not isinstance(clients, list):
            return info
        if len(clients) == 0:
            info["reset"] = True
            return info
        # Detection donnees de test RetailForce
        markers = self._detect_retailforce_test_markers(clients)
        if markers:
            info["is_test"] = True
            info["test_markers"] = markers
        else:
            info["configured"] = True
        return info

    @staticmethod
    def _detect_retailforce_test_markers(clients):
        # Renvoie la liste des marqueurs qui identifient le fichier comme
        # contenant les donnees de DEMO RetailForce (a reinitialiser).
        # IMPORTANT : on ne se base QUE sur l'identite de l'entreprise de demo
        # (TestClient / TestCompany / Teststrasse / DevBrand). L'ancien marqueur
        # "apiKey commence par test_" a ete RETIRE car il donnait des faux
        # positifs : un vrai client dont la TSE est encore en environnement de
        # test possede une apiKey 'test_' tout en ayant des donnees reelles ->
        # il etait marque "A reinitialiser" a tort.
        markers = []
        for idx, client in enumerate(clients):
            if not isinstance(client, dict):
                continue
            # 1. Marqueur explicite
            if client.get("TestClient") is True:
                markers.append(f"client[{idx}].TestClient=true")
            # 2. CompanyName de test
            company = (client.get("CompanyName") or "").lower()
            for needle in ("testcompany", "testfiliale"):
                if needle in company:
                    markers.append(f"client[{idx}].CompanyName contient '{needle}'")
                    break
            # 3. Adresse de test (Teststrasse / Steyr)
            addr = client.get("CompanyAddress") or {}
            street = (addr.get("Street") or "").lower()
            if "teststrasse" in street:
                markers.append(f"client[{idx}].CompanyAddress.Street='Teststrasse'")
            # 4. Brand de demo (DevBrand)
            brand = ((client.get("CashRegister") or {}).get("Brand") or "").lower()
            if "devbrand" in brand:
                markers.append(f"client[{idx}].CashRegister.Brand contient 'DevBrand'")
        # Deduplication preservant l'ordre
        seen = set()
        unique = []
        for m in markers:
            if m not in seen:
                seen.add(m)
                unique.append(m)
        return unique

    def stop_hfsql_service(self):
        # Stop-Service force, puis re-check via hfsql_service_status pour confirmer.
        status = self.hfsql_service_status()
        if not status.get("found"):
            raise OperationError("Aucun service HFSQL detecte sur ce poste.")
        if not status.get("running"):
            return status
        service_name = status.get("name") or status.get("display_name")
        if not service_name:
            raise OperationError("Nom du service HFSQL introuvable.")
        self.logger.info(f"Arret du service HFSQL : {service_name}")
        script = f"Stop-Service -Name '{service_name}' -Force -ErrorAction Stop"
        try:
            powershell_output(script)
        except Exception as exc:
            raise OperationError(f"Arret du service HFSQL refuse : {exc}")
        self.invalidate_hfsql_cache()
        new_status = self.hfsql_service_status(force_refresh=True)
        if new_status.get("running"):
            raise OperationError("Le service HFSQL est toujours en cours apres l'arret.")
        self.logger.info("Service HFSQL arrete.")
        return new_status

    def start_hfsql_service(self):
        # Symetrique de stop_hfsql_service : Start-Service puis re-check.
        status = self.hfsql_service_status()
        if not status.get("found"):
            raise OperationError("Aucun service HFSQL detecte sur ce poste.")
        if status.get("running"):
            return status
        service_name = status.get("name") or status.get("display_name")
        if not service_name:
            raise OperationError("Nom du service HFSQL introuvable.")
        self.logger.info(f"Demarrage du service HFSQL : {service_name}")
        script = f"Start-Service -Name '{service_name}' -ErrorAction Stop"
        try:
            powershell_output(script)
        except Exception as exc:
            raise OperationError(f"Demarrage du service HFSQL refuse : {exc}")
        self.invalidate_hfsql_cache()
        new_status = self.hfsql_service_status(force_refresh=True)
        if not new_status.get("running"):
            raise OperationError("Le service HFSQL n'a pas demarre.")
        self.logger.info("Service HFSQL demarre.")
        return new_status

    def reset_retailforce_json(self):
        # Reinitialise FiscalClients a [] dans le fichier de config RetailForce.
        # 1) Backup horodate cote a cote du fichier pour pouvoir restaurer en cas de probleme.
        # 2) Ecriture atomique : ecriture dans .tmp puis rename pour eviter un fichier corrompu en cas d'interruption.
        # Format de sortie : tableau vide ecrit sur deux lignes ("[\n  ]") pour rester aligne sur le format
        # attendu par RetailForce (json.dumps standard produit "[]" inline, ce qui peut perturber l'outil).
        path = RETAILFORCE_CONFIG_PATH
        if not path.exists():
            raise OperationError(f"Fichier introuvable : {path}")

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = path.with_name(f"{path.stem}.backup_{timestamp}{path.suffix}")
        try:
            shutil.copy2(path, backup_path)
        except Exception as exc:
            raise OperationError(f"Sauvegarde du fichier impossible : {exc}")
        self.logger.info(f"Sauvegarde creee : {backup_path.name}")

        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            raise OperationError(f"Lecture JSON impossible : {exc}")
        if not isinstance(data, dict):
            raise OperationError("Format JSON inattendu (objet attendu).")
        data["FiscalClients"] = []
        content = json.dumps(data, indent=2)
        # Force l'ecriture du tableau FiscalClients sur deux lignes meme vide.
        content = content.replace('"FiscalClients": []', '"FiscalClients": [\n  ]')
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        try:
            tmp_path.write_text(content, encoding="utf-8")
            tmp_path.replace(path)
        except Exception as exc:
            raise OperationError(f"Ecriture JSON refusee : {exc}")
        self.logger.info(f"FiscalClients reinitialise dans {path.name}.")
        status = self.retailforce_json_status()
        status["backup_path"] = str(backup_path)
        return status

    def detect_vega_roots(self):
        # Recherche les racines Vega sur tous les disques (locaux + reseau).
        # Patterns testes a la racine de chaque disque : VEGAHF, VEGACS, VEGA, VEGA*.
        # Pour chaque racine candidate : enumere les bases dans BDD/ (dossiers contenant vega.exe).
        # Retour : liste de dict {root: Path, on_network: bool, bases: [{name, path}]}.
        results = []
        try:
            drive_bits = ctypes.windll.kernel32.GetLogicalDrives()
        except Exception as exc:
            self.logger.warn(f"Enumeration disques impossible : {exc}")
            return results

        DRIVE_REMOTE = 4
        candidate_names = ("VEGAHF", "VEGACS", "VEGA")
        for index in range(26):
            if not (drive_bits & (1 << index)):
                continue
            letter = chr(ord("A") + index)
            drive_root = Path(f"{letter}:\\")
            try:
                drive_type = ctypes.windll.kernel32.GetDriveTypeW(str(drive_root))
            except Exception:
                drive_type = 0
            on_network = (drive_type == DRIVE_REMOTE)

            try:
                if not drive_root.exists():
                    continue
            except Exception:
                continue

            # Recherche en deux passes : noms exacts d'abord, sinon glob VEGA*.
            candidates = []
            for name in candidate_names:
                candidate = drive_root / name
                try:
                    if candidate.is_dir():
                        candidates.append(candidate)
                except Exception:
                    pass
            if not candidates:
                try:
                    for entry in drive_root.glob("VEGA*"):
                        if entry.is_dir() and entry not in candidates:
                            candidates.append(entry)
                except Exception:
                    pass

            for candidate in candidates:
                bdd_dir = candidate / "BDD"
                bases = []
                try:
                    if bdd_dir.is_dir():
                        for sub in bdd_dir.iterdir():
                            try:
                                # Une base Vega = sous-dossier de BDD/ qui n'est
                                # pas un dossier systeme HFSQL (__JNL, __System...)
                                # et qui contient au moins un fichier metier
                                # (.DAT, .fic, .wx, ou un .exe comme vega.exe,
                                # vega6.exe, Dialoweb.exe, etc.). Le critere
                                # "vega.exe" strict ratait les installs HFSQL
                                # avec uniquement des fichiers de donnees, ou
                                # les bases Vega6 ou l'exe s'appelle vega6.exe.
                                if not sub.is_dir():
                                    continue
                                if sub.name.startswith("__"):
                                    continue
                                has_data = False
                                for entry in sub.iterdir():
                                    if entry.is_file() and entry.suffix.lower() in (
                                        ".dat", ".fic", ".wx", ".exe", ".ndx", ".mmo"
                                    ):
                                        has_data = True
                                        break
                                if has_data:
                                    bases.append({"name": sub.name, "path": sub})
                            except Exception:
                                continue
                except Exception:
                    pass
                results.append({
                    "root": candidate,
                    "on_network": on_network,
                    "drive": letter,
                    "bases": bases,
                })

        if results:
            local_count = sum(1 for r in results if not r["on_network"])
            self.logger.info(f"Detection Vega : {len(results)} racine(s) ({local_count} local(es)).")
        else:
            self.logger.info("Detection Vega : aucune racine trouvee.")
        return results

    def rollback_preflight(self, root, operation_id):
        # Dry-run : verifie la faisabilite d'un rollback avant de toucher au disque.
        root = Path(root).resolve()
        backup_root = root / STATE_DIR_NAME / operation_id
        manifest_path = backup_root / "manifest.json"
        if not manifest_path.exists():
            return {"ok": False, "issues": [f"Manifest introuvable : {manifest_path}"], "actions": 0}

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return {"ok": False, "issues": [f"Manifest illisible : {exc}"], "actions": 0}

        if manifest.get("status") == "rolled_back":
            return {"ok": False, "issues": [f"Operation {operation_id} deja annulee."], "actions": 0}

        issues = []
        actions = manifest.get("actions", [])
        for action in actions:
            for key in ("src", "dest"):
                path_str = action.get(key)
                if not path_str:
                    continue
                target = Path(path_str)
                if target.exists() and not path_writable(target):
                    issues.append(f"Acces refuse : {target}")
            for snap_key in ("src_snapshot", "dest_snapshot"):
                snap = action.get(snap_key)
                if snap and not (backup_root / snap).exists():
                    issues.append(f"Snapshot absent : {snap}")
        return {"ok": not issues, "issues": issues, "actions": len(actions)}

    def ensure_subfolders(self, v6):
        exe_v5 = v6 / EXE_V5
        dll_v5 = v6 / DLL_V5
        exe_v5.mkdir(exist_ok=True)
        dll_v5.mkdir(exist_ok=True)
        self.logger.info(f"Dossiers cibles assurés : {exe_v5} ; {dll_v5}.")
        return exe_v5, dll_v5

    def run_migration(self, root, json_ok, hfsql_ok, allow_without_firewall=False,
                      conflict_handler=None):
        # conflict_handler(kind, name, detail) -> "skip" | "skip_all" | "abort"
        #   appele des qu'une anomalie survient : fichier verrouille (kind
        #   "locked") ou element attendu manquant (kind "missing"). "abort"
        #   declenche un retour arriere complet.
        if not json_ok:
            raise OperationError(
                "Vous devez obligatoirement réinitialiser le fichier JSON de Retail Force avant de continuer."
            )
        if not hfsql_ok:
            raise OperationError("Le service HFSQL doit être arrêté avant de continuer.")

        self._conflict_handler = conflict_handler
        self._ignore_problems = False

        root = Path(root).resolve()
        v6 = self.validate_root(root)
        firewall_ok = self.firewall_rule_ok()
        if not firewall_ok:
            self.logger.info(
                f"Avertissement : aucune règle pare-feu TCP {FIREWALL_TCP_PORT} active n'a été trouvée."
            )
            if not allow_without_firewall:
                raise OperationError(
                    f"Aucune règle pare-feu TCP {FIREWALL_TCP_PORT} active n'a été trouvée."
                )

        operation_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        state_dir = root / STATE_DIR_NAME
        backup_root = state_dir / operation_id
        backup_root.mkdir(parents=True, exist_ok=True)
        hide_windows_path(state_dir)

        # Le manifest pilote à la fois les journaux et le retour arrière.
        manifest = {
            "id": operation_id,
            "root": str(root),
            "created_at": datetime.datetime.now().isoformat(),
            "status": "running",
            "snapshot_index": 0,
            "actions": [],
            "log_file": f"vega_migration_{operation_id}.log",
        }
        self.save_manifest(backup_root, manifest)
        self.logger.info(f"Demarrage migration sur {root}")

        try:
            exe_v5, dll_v5 = self.ensure_subfolders(v6)
            self.move_root_binaries(root, exe_v5, dll_v5, backup_root, manifest)
            self.handle_nomserve_and_produits(root, v6, backup_root, manifest)

            pkg = self.find_pkg_folder(v6)
            if pkg:
                self.move_pkg_content_to_root(root, pkg, backup_root, manifest)

            manifest["status"] = "completed_with_errors" if self.logger.errors else "completed"
            if self.logger.errors:
                self.logger.info("Migration terminée avec erreurs.")
            else:
                self.logger.info("Migration terminée avec succès.")
        except MigrationAborted as exc:
            # Annulation utilisateur face a un fichier verrouille : on annule
            # TOUTES les actions deja effectuees (retour arriere complet).
            manifest["status"] = "aborted"
            self.save_manifest(backup_root, manifest)
            self.logger.info(
                "Annulation demandée : retour arrière des actions déjà effectuées..."
            )
            for action in reversed(manifest.get("actions", [])):
                try:
                    self.rollback_action(action, backup_root)
                except Exception as rexc:
                    self.logger.error(f"Erreur pendant le retour arrière d'annulation : {rexc}")
            manifest["status"] = "rolled_back"
            manifest["aborted"] = True
            manifest["rolled_back_at"] = datetime.datetime.now().isoformat()
            self.logger.info(
                "Retour arrière terminé : la migration a été entièrement annulée."
            )
            raise OperationError(
                "Migration annulée : un fichier était verrouillé et toutes les "
                "actions déjà effectuées ont été annulées (retour arrière)."
            ) from exc
        except Exception as exc:
            manifest["status"] = "failed"
            manifest["fatal_error"] = str(exc)
            self.logger.error(f"Migration interrompue: {exc}")
            raise
        finally:
            manifest.pop("snapshot_index", None)
            self.save_manifest(backup_root, manifest)
            log_path = root / manifest["log_file"]
            self.logger.save(log_path)

        return manifest

    def list_operations(self, root):
        root = Path(root).resolve()
        state_dir = root / STATE_DIR_NAME
        operations = []
        if not state_dir.exists():
            return operations

        for child in state_dir.iterdir():
            manifest_path = child / "manifest.json"
            if not manifest_path.exists():
                continue
            try:
                operations.append(json.loads(manifest_path.read_text(encoding="utf-8")))
            except Exception:
                continue

        operations.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return operations

    def rollback(self, root, operation_id):
        # Rejoue les actions enregistrées en ordre inverse.
        root = Path(root).resolve()
        backup_root = root / STATE_DIR_NAME / operation_id
        manifest_path = backup_root / "manifest.json"
        if not manifest_path.exists():
            raise OperationError(f"Sauvegarde introuvable pour l'opération {operation_id}.")

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("status") == "rolled_back":
            raise OperationError(f"L'opération {operation_id} a déjà été annulée.")

        self.logger.info(f"Retour arrière de l'opération {operation_id}")
        for action in reversed(manifest.get("actions", [])):
            self.rollback_action(action, backup_root)

        manifest["status"] = "rolled_back"
        manifest["rolled_back_at"] = datetime.datetime.now().isoformat()
        self.save_manifest(backup_root, manifest)

        rollback_log = root / f"vega_migration_retour_arriere_{operation_id}.log"
        self.logger.save(rollback_log)
        self.logger.info(f"Retour arrière terminé. Journal : {rollback_log}")
        return manifest

    def save_manifest(self, backup_root, manifest):
        payload = dict(manifest)
        payload.pop("snapshot_index", None)
        (backup_root / "manifest.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def snapshot_existing(self, path, backup_root, manifest, label):
        # Sauvegarde l'état précédent avant tout écrasement.
        if not path.exists():
            return None
        index = manifest["snapshot_index"]
        manifest["snapshot_index"] += 1
        target = backup_root / f"{index:04d}_{label}"
        copy_path(path, target)
        return target.name

    def restore_snapshot(self, backup_root, snapshot_name, target):
        if not snapshot_name:
            return
        source = backup_root / snapshot_name
        if not source.exists():
            return
        copy_path(source, target)

    def record_action(self, backup_root, manifest, action):
        manifest["actions"].append(action)
        self.save_manifest(backup_root, manifest)

    @staticmethod
    def _is_lock_error(exc):
        # Erreur "fichier utilise par un autre processus" :
        #   WinError 32 = ERROR_SHARING_VIOLATION, 33 = ERROR_LOCK_VIOLATION.
        #   PermissionError / EACCES / EBUSY couvrent les cas equivalents.
        import errno
        if getattr(exc, "winerror", None) in (32, 33):
            return True
        if isinstance(exc, PermissionError):
            return True
        if isinstance(exc, OSError) and exc.errno in (errno.EACCES, errno.EBUSY):
            return True
        return False

    def _prompt_and_decide(self, kind, name, detail):
        # Coeur commun de la gestion d'anomalie : demande a l'utilisateur (via
        # le handler GUI) et renvoie "skip" | "skip_all" | "abort", en tenant
        # compte du flag "ignorer toutes les suivantes".
        #   - Si l'utilisateur a deja coche "ignorer les suivantes" -> "skip".
        #   - Sans handler (appel programmatique / test) : on ignore les elements
        #     manquants (ancien comportement) mais on annule sur fichier verrouille.
        if self._ignore_problems:
            return "skip"
        handler = self._conflict_handler
        if handler is None:
            return "skip" if kind == "missing" else "abort"
        decision = handler(kind, name, detail) or "abort"
        if decision == "skip_all":
            self._ignore_problems = True
        return decision

    def _handle_locked_item(self, src, exc):
        # Fichier verrouille (impossible a deplacer). Retourne True si on ignore
        # et poursuit, sinon leve MigrationAborted (-> retour arriere complet).
        reason = getattr(exc, "strerror", None) or str(exc)
        decision = self._prompt_and_decide("locked", str(src), reason)
        if decision in ("skip", "skip_all"):
            self.logger.warn(f"Fichier verrouillé ignoré : {Path(src).name} ({reason})")
            return True
        self.logger.error(
            f"Migration annulée par l'utilisateur (fichier verrouillé : {Path(src).name})."
        )
        raise MigrationAborted(str(src))

    def _handle_missing(self, message, name=None):
        # Anomalie non-verrouillage : un element attendu est manquant (dossier
        # du paquet, nomserve.txt, GPRODUIT...). Meme logique que le verrouillage :
        # on demande a l'utilisateur de poursuivre (ignorer) ou tout annuler.
        decision = self._prompt_and_decide("missing", name, message)
        if decision in ("skip", "skip_all"):
            # On garde la trace de l'anomalie dans le journal (rouge) puis on continue.
            self.logger.error(message)
            self.logger.warn("→ Élément manquant ignoré, migration poursuivie.")
            return True
        self.logger.error(f"Migration annulée par l'utilisateur ({message}).")
        raise MigrationAborted(message)

    def move_item(self, src, dest, backup_root, manifest, log_label):
        if not src.exists():
            self._handle_missing(f"Introuvable : {src}", name=Path(src).name)
            return

        # Conserve les deux côtés du déplacement quand c'est nécessaire.
        # Sur un fichier verrouillé, shutil.move (os.rename même volume) échoue
        # de façon atomique : rien n'a bougé, on peut ignorer sans réparer.
        action = {
            "type": "move",
            "src": str(src),
            "dest": str(dest),
            "src_snapshot": self.snapshot_existing(src, backup_root, manifest, "src"),
            "dest_snapshot": self.snapshot_existing(dest, backup_root, manifest, "dest"),
        }
        try:
            self.merge_move(src, dest)
        except OSError as exc:
            if not self._is_lock_error(exc):
                raise
            self._handle_locked_item(src, exc)
            return
        self.record_action(backup_root, manifest, action)
        self.logger.info(f"{log_label}: {src.name} -> {dest}")

    def copy_item(self, src, dest, backup_root, manifest, log_label):
        if not src.exists():
            self._handle_missing(f"Introuvable : {src}", name=Path(src).name)
            return

        # Pour une copie, seule la destination doit être sauvegardée.
        action = {
            "type": "copy",
            "src": str(src),
            "dest": str(dest),
            "dest_snapshot": self.snapshot_existing(dest, backup_root, manifest, "dest"),
        }
        try:
            self.replace_copy(src, dest)
        except OSError as exc:
            if not self._is_lock_error(exc):
                raise
            self._handle_locked_item(src, exc)
            return
        self.record_action(backup_root, manifest, action)
        self.logger.info(f"{log_label}: {src.name} -> {dest}")

    def merge_move(self, src, dest):
        # Les dossiers sont fusionnés élément par élément pour éviter de perdre l'existant.
        dest.parent.mkdir(parents=True, exist_ok=True)

        if src.is_dir():
            if dest.exists() and dest.is_file():
                dest.unlink()
            if not dest.exists():
                shutil.move(str(src), str(dest))
                return
            for child in list(src.iterdir()):
                self.merge_move(child, dest / child.name)
            src.rmdir()
            return

        if dest.exists():
            remove_existing_path(dest)
        shutil.move(str(src), str(dest))

    def replace_copy(self, src, dest):
        # Le mode copie remplace les conflits sans toucher au reste.
        dest.parent.mkdir(parents=True, exist_ok=True)

        if src.is_dir():
            if dest.exists() and dest.is_file():
                dest.unlink()
            if not dest.exists():
                shutil.copytree(src, dest)
                return
            for child in src.iterdir():
                self.replace_copy(child, dest / child.name)
            return

        if dest.exists():
            remove_existing_path(dest)
        shutil.copy2(src, dest)

    def running_binary_name(self):
        import sys

        # L'exe compilé ne doit jamais tenter de se déplacer lui-même.
        if getattr(sys, "frozen", False):
            return Path(sys.executable).name.lower()
        return None

    def move_root_binaries(self, root, exe_v5, dll_v5, backup_root, manifest):
        current_binary = self.running_binary_name()

        for item in sorted(root.glob("*.exe")):
            if current_binary and item.name.lower() == current_binary:
                self.logger.info(f"Fichier ignoré car en cours d'exécution : {item.name}")
                continue
            self.move_item(item, exe_v5 / item.name, backup_root, manifest, "Déplacé")

        for item in sorted(root.glob("*.dll")):
            self.move_item(item, dll_v5 / item.name, backup_root, manifest, "Déplacé")

    def handle_nomserve_and_produits(self, root, v6, backup_root, manifest):
        nomserve = root / "nomserve.txt"
        if nomserve.exists():
            self.move_item(nomserve, v6 / "nomserve-v5", backup_root, manifest, "Déplacé")
        else:
            self._handle_missing("nomserve.txt introuvable à la racine.", name="nomserve.txt")

        for fname in ["GPRODUIT.DAT", "GPRODUIT.NDX"]:
            src = root / fname
            if not src.exists():
                self._handle_missing(f"{fname} introuvable à la racine.", name=fname)
                continue
            self.copy_item(src, v6 / src.name, backup_root, manifest, "Copie")

    def find_pkg_folder(self, v6):
        # Seul le suffixe est stable, le nom daté change avec le temps.
        candidates = []
        try:
            for directory in v6.iterdir():
                if directory.is_dir() and directory.name.endswith(PKG_SUFFIX):
                    candidates.append(directory)
        except Exception as exc:
            self.logger.error(f"Lecture de {v6} impossible : {exc}")
            return None

        if not candidates:
            self._handle_missing(
                "Aucun dossier se terminant par PkgV6ProdF n'a été trouvé dans V6.",
                name="PkgV6ProdF",
            )
            return None

        picked = sorted(candidates, key=lambda path: path.name)[-1]
        self.logger.info(f"Dossier paquet détecté : {picked.name}")

        nested = picked / picked.name
        if nested.exists() and nested.is_dir():
            self.logger.info(f"Dossier imbriqué détecté, utilisation de : {nested}")
            return nested
        return picked

    def move_pkg_content_to_root(self, root, pkg, backup_root, manifest):
        # N'applique que les parties du paquet prévues par la procédure.
        for name in PKG_CONTENT_DIRS:
            src_dir = pkg / name
            if not src_dir.exists() or not src_dir.is_dir():
                self._handle_missing(f"Dossier manquant dans le paquet : {name}", name=name)
                continue
            for item in list(src_dir.iterdir()):
                self.move_item(item, root / item.name, backup_root, manifest, f"Déplacé depuis {name}")

        seven_zip = pkg / "Exe_Divers" / "7zip"
        if seven_zip.exists() and seven_zip.is_dir():
            for item in list(seven_zip.iterdir()):
                self.move_item(
                    item,
                    root / item.name,
                    backup_root,
                    manifest,
                    "Déplacé depuis Exe_Divers\\7zip",
                )
        else:
            self._handle_missing(
                "Dossier Exe_Divers\\7zip manquant dans le paquet.", name="Exe_Divers\\7zip"
            )

        images = pkg / "Images"
        if images.exists() and images.is_dir():
            self.move_item(images, root / "Images", backup_root, manifest, "Dossier deplace")
        else:
            self._handle_missing("Dossier Images manquant dans le paquet.", name="Images")

        if (pkg / "RF").exists():
            self.logger.info("Dossier RF laissé inchangé conformément à la procédure.")

    def rollback_action(self, action, backup_root):
        src = Path(action["src"])
        dest = Path(action["dest"])

        if action["type"] == "move":
            if src.exists():
                remove_existing_path(src)
            if dest.exists():
                remove_existing_path(dest)
            self.restore_snapshot(backup_root, action.get("src_snapshot"), src)
            self.restore_snapshot(backup_root, action.get("dest_snapshot"), dest)
            self.logger.info(f"Retour arrière déplacement : {dest} -> {src}")
            return

        if action["type"] == "copy":
            if dest.exists():
                remove_existing_path(dest)
            self.restore_snapshot(backup_root, action.get("dest_snapshot"), dest)
            self.logger.info(f"Retour arrière copie : {dest}")


