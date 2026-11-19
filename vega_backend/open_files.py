"""Fichiers ouverts dans une base Vega : liste et fermeture forcee.

Deux sources :
  - fichiers ouverts a distance par les postes via le partage reseau (SMB).
    Win8 / Server 2012+ : Get-SmbOpenFile / Close-SmbOpenFile.
    Win7 / 2008 R2 (pas de cmdlets SMB) : openfiles /query + /disconnect.
  - processus de CE poste lances depuis la base (vega.exe, vega6.exe...).

Le service HFSQL n'est pas concerne : il s'arrete depuis l'onglet Migration.
"""
import csv
import io
import json
import os
from pathlib import Path

from ._common import OperationError, command_output, is_admin, powershell_output


def _norm(path):
    return str(path).replace("/", "\\").rstrip("\\").lower()


def _under(path, base_norm):
    p = _norm(path)
    return p == base_norm or p.startswith(base_norm + "\\")


def _json_list(text):
    text = (text or "").strip()
    if not text:
        return []
    data = json.loads(text)
    return data if isinstance(data, list) else [data]


class OpenFilesManager:
    def __init__(self, logger):
        self.logger = logger

    # ---------- Lecture ----------

    def _smb_files_cmdlet(self):
        # None si les cmdlets SMB n'existent pas (Win7) -> repli openfiles.
        script = (
            "if (-not (Get-Command Get-SmbOpenFile -ErrorAction SilentlyContinue)) { 'NOCMDLET'; exit }\n"
            "@(Get-SmbOpenFile -ErrorAction Stop | Select-Object "
            "@{n='id';e={[string]$_.FileId}},@{n='path';e={$_.Path}},"
            "@{n='client';e={$_.ClientComputerName}},@{n='user';e={$_.ClientUserName}}) "
            "| ConvertTo-Json -Compress"
        )
        result = powershell_output(script)
        out = (result.stdout or "").strip()
        if out == "NOCMDLET":
            return None
        if result.returncode != 0 and not out:
            raise OperationError(result.stderr.strip() or "Lecture des fichiers partagés impossible.")
        return _json_list(out)

    def _smb_files_openfiles(self):
        # openfiles.exe (Win7+) : colonnes ID, Accede par, Type, Fichier ouvert.
        result = command_output(["openfiles", "/query", "/fo", "csv", "/nh"])
        files = []
        for row in csv.reader(io.StringIO(result.stdout or "")):
            if len(row) < 4 or not row[0].strip():
                continue
            files.append({"id": row[0].strip(), "path": row[3].strip(), "user": row[1].strip(), "client": ""})
        return files

    def _local_processes(self, base_norm):
        script = (
            "@(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path } | "
            "Select-Object @{n='pid';e={$_.Id}},@{n='name';e={$_.ProcessName}},@{n='path';e={$_.Path}}) "
            "| ConvertTo-Json -Compress"
        )
        result = powershell_output(script)
        try:
            procs = _json_list(result.stdout)
        except ValueError:
            return []
        own = os.getpid()
        return [
            p for p in procs
            if p.get("path") and _under(p["path"], base_norm) and int(p.get("pid") or 0) != own
        ]

    def list_open(self, base):
        base = Path(base)
        if not base.is_dir():
            raise OperationError(f"Base Vega introuvable : {base}")
        if not is_admin():
            raise OperationError("La liste des fichiers ouverts demande un lancement en administrateur.")
        base_norm = _norm(base)

        method = "smb"
        files = self._smb_files_cmdlet()
        if files is None:
            method = "openfiles"
            files = self._smb_files_openfiles()
        files = [f for f in files if f.get("path") and _under(f["path"], base_norm)]
        processes = self._local_processes(base_norm)
        self.logger.info(
            f"Fichiers ouverts dans {base} : {len(files)} fichier(s) réseau, {len(processes)} processus local(aux)."
        )
        return {"base": str(base), "method": method, "files": files, "processes": processes}

    # ---------- Fermeture ----------

    def close_all(self, state):
        # state = resultat de list_open (on ferme exactement ce qui a ete montre).
        if not is_admin():
            raise OperationError("La fermeture des fichiers ouverts demande un lancement en administrateur.")
        closed, failed = 0, []

        for proc in state.get("processes") or []:
            result = powershell_output(f"Stop-Process -Id {int(proc['pid'])} -Force -ErrorAction Stop")
            if result.returncode == 0:
                closed += 1
                self.logger.info(f"Processus arrêté : {proc.get('name')} (PID {proc['pid']})")
            else:
                failed.append(f"{proc.get('name')} (PID {proc['pid']})")

        files = state.get("files") or []
        if files and state.get("method") == "smb":
            ids = ",".join(str(int(f["id"])) for f in files if str(f.get("id", "")).isdigit())
            if ids:
                script = (
                    "$ok=0; $ko=@(); foreach ($id in @(" + ids + ")) { "
                    "try { Close-SmbOpenFile -FileId $id -Force -ErrorAction Stop; $ok++ } "
                    "catch { $ko += $id } }; @{ok=$ok; ko=$ko} | ConvertTo-Json -Compress"
                )
                result = powershell_output(script)
                try:
                    data = json.loads((result.stdout or "").strip() or "{}")
                except ValueError:
                    data = {}
                closed += int(data.get("ok") or 0)
                ko = data.get("ko") or []
                if not isinstance(ko, list):
                    ko = [ko]
                failed.extend(f"fichier réseau #{i}" for i in ko)
        else:
            for f in files:
                result = command_output(["openfiles", "/disconnect", "/id", str(f["id"])])
                if result.returncode == 0:
                    closed += 1
                else:
                    failed.append(f["path"])

        self.logger.info(f"Fermeture forcée : {closed} élément(s) fermé(s), {len(failed)} échec(s).")
        for item in failed:
            self.logger.warn(f"Non fermé : {item}")
        return {"closed": closed, "failed": failed}

