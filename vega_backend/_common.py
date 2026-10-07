import base64
import ctypes
import datetime
import hashlib
import ipaddress
import json
import os
import secrets
import shutil
import smtplib
import socket
import ssl
import subprocess
import threading
import time
import webbrowser
import winreg
import zipfile
from collections import Counter
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlparse, urlsplit
from urllib.request import Request, urlopen

FIREWALL_TCP_PORT = "7678"
FIREWALL_RULE_PROTOCOL = "TCP"
FIREWALL_RULE_DISPLAY_NAME = "Service Fiscal RetailForce"
FIREWALL_RULE_NAME = "ServiceFiscalRetailForce7678"
FIREWALL_RULE_DESCRIPTION = "Ouverture du port TCP 7678 pour Service Fiscal RetailForce"
HFSQL_SERVICE_DISPLAY_PREFIX = "Hyper File Server"
RETAILFORCE_CONFIG_PATH = Path(r"C:\Program Files\RetailForce\Fiscal Webservice\Config\fiscalService.config.json")
FIREWALL_HFSQL_PORT = "4900"
FIREWALL_HFSQL_PROTOCOL = "TCP"
FIREWALL_HFSQL_DISPLAY_NAME = "HFSQL Server"
FIREWALL_HFSQL_RULE_NAME = "HFSQLServer4900"
FIREWALL_HFSQL_RULE_DESCRIPTION = "Ouverture du port TCP 4900 pour HFSQL Server"
V6_TARGET_REL = Path(r"vega.dos\V6")
EXE_V5 = "EXE V5"
DLL_V5 = "DLL V5"
PKG_SUFFIX = "PkgV6ProdF"
PKG_CONTENT_DIRS = ["V6_Divers", "V6_DLL", "V6_Exe", "V6_Fichiers", "W28_DLL"]
STATE_DIR_NAME = ".vega_tool_state"
SHARE_DIR = Path(r"C:\ImpressionsVega")
SHARE_NAME = "ImpressionsVega$"
FILE_ATTRIBUTE_HIDDEN = 0x2
FILE_ATTRIBUTE_NORMAL = 0x80
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
VEGA6_INDEX_URL = "https://telechargement.quatuhore.fr/VEGA6/PROD/"
DOWNLOAD_BUFFER_SIZE = 1024 * 1024
DOWNLOAD_TIMEOUT = 60
DOWNLOAD_USER_AGENT = "VegaMigrationTool/2.10.1"
CLEAN_LOG_TEMPLATE = "vega_cleanup_%Y-%m-%d_%H-%M-%S.log"

CLEAN_ROOT_DIR_MOVES = {
    "logos vega": "LOGOS VEGA",
    "procedures vega": "PROCEDURES VEGA",
    "sons": "SONS",
    "wd.eu": "WD.EU",
}

CLEAN_CATEGORY_DIRS = {
    "old_exe": "ANCIENS EXE",
    "old_dll": "ANCIENNES DLL",
    "pdf": "TROUVES PDF",
    "docs": "TROUVES DOCS",
    "xls": "TROUVES XLS",
    "archives": "ARCHIVES ZIP",
    "copies": "TROUVES COPIES",
    "images": "TROUVES IMAGES",
    "fdj": "TROUVES RESULTATS FDJ",
}

CLEAN_CATEGORY_LABELS = {
    "dir_move": "Dossiers",
    "old_exe": "Anciens exe",
    "old_dll": "Anciennes dll",
    "pdf": "PDF",
    "docs": "Docs",
    "xls": "XLS",
    "archives": "Archives",
    "copies": "Copies",
    "images": "Images",
    "fdj": "Résultats FDJ",
    "delete": "Suppressions",
}

CLEAN_OLD_EXE_NAMES = {
    "addimat.exe",
    "adrlinkit.exe",
    "adrlinkit35.exe",
    "encocent.exe",
    "factdisb.exe",
    "factdsib.exe",
    "factpock.exe",
    "jre-7u2-windows-i586.exe",
    "netrecep.exe",
    "renumclient.exe",
    "servpock.exe",
}

# Prefixe wd23* retire : ces DLL WinDev 23 sont toujours utilisees par
# Vega6 (qui embarque WinDev 23). Ne pas deplacer.
CLEAN_OLD_DLL_PREFIXES = ("wd110", "wd150", "wd190")
CLEAN_DOC_EXTENSIONS = {".doc", ".docx", ".rtf", ".txt"}
CLEAN_DOC_EXCEPTIONS = {"nomserve.txt", "linkit-communicator.txt"}
CLEAN_PDF_EXCEPTIONS = {"manuel vega.pdf", "menu par colonnes.pdf"}
CLEAN_XLS_EXTENSIONS = {".xls", ".xlsx"}
CLEAN_ARCHIVE_EXTENSIONS = {".zip", ".7z", ".7zip"}
CLEAN_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".bmp", ".png", ".tif", ".tiff", ".xcf"}
CLEAN_IMAGE_PREFIX_EXCEPTIONS = ("factima",)
CLEAN_DELETE_EXTENSIONS = {".hlp", ".lnk", ".ink"}

# Exceptions globales : fichiers qui ne doivent JAMAIS etre touches par le
# nettoyage, quel que soit leur extension (toutes en lowercase pour le check).
# Ces fichiers sont utilises par Vega et ne doivent ni etre deplaces, ni supprimes.
CLEAN_FILE_EXCEPTIONS = {
    "dialborne.exe",          # remplace : reste utilise par certaines installs
    "dialborne6.exe",         # version 6, utilisee par Vega6
    "imgexportbi.png",        # asset Vega
    "maintenance.png",        # asset Vega
    "zuc_orizz_white_a-1.jpg",  # logo Zucchetti utilise
}

STATIC_DOWNLOAD_ITEMS = [
    {
        "id": "hfsql31",
        "name": "Client Serveur HFSQL 31",
        "group": "HFSQL",
        "url": "https://telechargement.quatuhore.fr/CS/WX310PACKHFSQLCS.exe",
    },
    {
        "id": "hfsql28",
        "name": "Client Serveur HFSQL 28",
        "group": "HFSQL",
        "url": "https://telechargement.quatuhore.fr/CS/WX280PACKHFSQLCS.exe",
    },
    {
        "id": "syspay_models",
        "name": "Modèles de mail Syspay",
        "group": "SysPay",
        "url": "https://telechargement.quatuhore.fr/SysPay/Mod%c3%a8les%20Mails%20Syspay.zip",
    },
    {
        "id": "vcredist_2010_x86",
        "name": "Microsoft Visual C++ 2010 x86",
        "group": "Visual C++",
        "url": "https://telechargement.quatuhore.fr/SysPay/vcredist_x86.exe",
    },
    {
        "id": "vcredist_v14_x86",
        "name": "Microsoft Visual C++ v14 x86",
        "group": "Visual C++",
        "url": "https://telechargement.quatuhore.fr/SysPay/VC_redist.x86.exe",
    },
    {
        "id": "vcredist_v14_x64",
        "name": "Microsoft Visual C++ v14 x64",
        "group": "Visual C++",
        "url": "https://telechargement.quatuhore.fr/SysPay/VC_redist.x64.exe",
    },
    {
        "id": "vega4_rev_25_90_29",
        "name": "VEGA4 REV25.90.29",
        "group": "VEGA",
        "url": "https://telechargement.quatuhore.fr/VEGA2018W23/PROD/VEGA5.25-REV90.29-complete.zip",
    },
    {
        "id": "vega4_rev_25_90_30",
        "name": "VEGA4 REV25.90.30",
        "group": "VEGA",
        "url": "https://telechargement.quatuhore.fr/VEGA2018W23/PROD/525R90v30-20250219.zip",
    },
    {
        "id": "vega5",
        "name": "VEGA 5",
        "group": "VEGA",
        "url": "https://telechargement.quatuhore.fr/VEGANORM/PROD/install_vega_5_25_95_17_Dialoweb_9.zip",
    },
]


class DirectoryIndexParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return
        attributes = dict(attrs)
        href = attributes.get("href", "").strip()
        if href:
            self.links.append(href)


class OperationError(RuntimeError):
    pass


class SmtpTestError(OperationError):
    # Erreur dediee au test SMTP : porte la liste structuree (level, message) en plus du diag formate.
    # Les UI peuvent lire .lines pour styler chaque ligne (ERROR rouge, WARN orange, INFO neutre).
    def __init__(self, lines, message=None):
        self.lines = [(level, msg) for level, msg in (lines or [])]
        self.diagnostic = "\n".join(f"[{lvl}] {msg}" for lvl, msg in self.lines)
        super().__init__(message or self.diagnostic or "Echec test SMTP.")


class ToolLogger:
    # Thread-safe : un Lock protege les operations list (append/clear/snapshot pour save).
    # Le sink est appele HORS du lock pour eviter un deadlock si le sink retro-appelle le logger.
    # audit_sink (optionnel) : appele pour CHAQUE info/warn/error pour alimenter
    # le journal d'audit en parallele du sink UI.
    def __init__(self, sink=None, audit_sink=None):
        self.sink = sink
        self.audit_sink = audit_sink
        self.lines = []
        self.errors = []
        self._lock = threading.Lock()

    def set_audit_sink(self, audit_sink):
        # Permet de brancher l'audit apres construction (AuditLogger n'existe pas
        # encore au moment ou ToolLogger est instancie dans app.__init__).
        self.audit_sink = audit_sink

    def clear(self):
        with self._lock:
            self.lines.clear()
            self.errors.clear()

    def info(self, message):
        self._write("info", message)

    def warn(self, message):
        self._write("warn", message)

    def error(self, message):
        self._write("error", message)

    def _write(self, level, message):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        prefix = ""
        if level == "error":
            prefix = "ERREUR: "
        elif level == "warn":
            prefix = "ATTENTION: "
        line = f"[{ts}] {prefix}{message}"
        with self._lock:
            self.lines.append(line)
            if level == "error":
                self.errors.append(line)
        # Sink appele hors lock : il marshalle vers le thread Tk via Queue (thread-safe par design).
        if self.sink:
            self.sink(line, level)
        # Audit sink : alimentation automatique du journal d'audit. Best-effort.
        if self.audit_sink:
            try:
                self.audit_sink(level, message)
            except Exception:
                pass

    def save(self, path):
        with self._lock:
            snapshot = list(self.lines)
        path.write_text("\n".join(snapshot), encoding="utf-8")


def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def windows_case_insensitive(base_root, relative_path):
    current = base_root
    for part in relative_path.parts:
        found = None
        try:
            for entry in current.iterdir():
                if entry.name.lower() == part.lower():
                    found = entry
                    break
        except Exception:
            pass
        current = found if found else (current / part)
    return current


def remove_existing_path(path):
    path = Path(path)
    if not path.exists():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, onerror=handle_remove_readonly)
    else:
        ensure_writable(path)
        path.unlink()


def ensure_writable(path):
    try:
        path = Path(path)
        if not path.exists():
            return
        ctypes.windll.kernel32.SetFileAttributesW(str(path), FILE_ATTRIBUTE_NORMAL)
    except Exception:
        pass

    try:
        if path.is_dir():
            os.chmod(path, 0o777)
        else:
            os.chmod(path, 0o666)
    except Exception:
        pass


def handle_remove_readonly(func, path, _exc_info):
    ensure_writable(path)
    func(path)


def copy_path(src, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dest)
    else:
        shutil.copy2(src, dest)


def path_writable(path):
    # Dry-run : teste si on pourrait supprimer / modifier le chemin sans acces refuse.
    path = Path(path)
    if not path.exists():
        return True
    if path.is_dir():
        marker = path / ".vega_preflight.tmp"
        try:
            marker.touch()
            marker.unlink()
            return True
        except Exception:
            return False
    try:
        with open(path, "ab"):
            return True
    except Exception:
        return False


def command_output(command):
    # Lance les commandes sans faire apparaître de fenêtre console.
    startupinfo = None
    if hasattr(subprocess, "STARTUPINFO"):
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0

    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        errors="ignore",
        check=False,
        startupinfo=startupinfo,
        creationflags=CREATE_NO_WINDOW,
    )


def powershell_output(script):
    # Point d'entrée unique pour tous les appels PowerShell du GUI.
    return command_output(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-Command",
            script,
        ]
    )


def hide_windows_path(path):
    # Masque les dossiers internes pour garder la racine Vega propre.
    try:
        path = Path(path)
        if not path.exists():
            return
        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
        if attrs == -1:
            return
        ctypes.windll.kernel32.SetFileAttributesW(str(path), attrs | FILE_ATTRIBUTE_HIDDEN)
    except Exception:
        pass


def default_download_dir():
    # Utilise le vrai dossier Téléchargements de la session Windows.
    try:
        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", ctypes.c_ulong),
                ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        downloads_guid = GUID(
            0x374DE290,
            0x123F,
            0x4565,
            (ctypes.c_ubyte * 8)(0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7B),
        )
        path_ptr = ctypes.c_wchar_p()
        result = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(downloads_guid),
            0,
            None,
            ctypes.byref(path_ptr),
        )
        if result == 0 and path_ptr.value:
            path = Path(path_ptr.value)
            ctypes.windll.ole32.CoTaskMemFree(path_ptr)
            return path
    except Exception:
        pass

    return Path.home() / "Downloads"


def ps_single_quote(value):
    return str(value).replace("'", "''")


def prefix_to_mask(prefix_length):
    try:
        prefix_length = int(prefix_length)
    except Exception:
        return ""

    if prefix_length < 0 or prefix_length > 32:
        return ""
    if prefix_length == 0:
        return "0.0.0.0"

    mask = (0xFFFFFFFF << (32 - prefix_length)) & 0xFFFFFFFF
    return ".".join(str((mask >> shift) & 0xFF) for shift in (24, 16, 8, 0))


def is_link_local_ipv4(address):
    try:
        return ipaddress.ip_address(address).is_link_local
    except Exception:
        return False


def gateway_matches_subnet(ipv4, prefix_length, gateway):
    try:
        network = ipaddress.ip_network(f"{ipv4}/{int(prefix_length)}", strict=False)
        return ipaddress.ip_address(gateway) in network
    except Exception:
        return False


# --- Lecture pare-feu directe via le registre Windows ---
# Get-NetFirewallRule (PowerShell) coute 5 a 10 secondes (cold start PS + WMI).
# Les regles sont aussi stockees au format "cle=valeur" pipe-separe sous
# HKLM\SYSTEM\CurrentControlSet\Services\SharedAccess\Parameters\FirewallPolicy\FirewallRules,
# lecture en quelques millisecondes et independante de la langue de l'OS.

_FIREWALL_RULES_REG_PATH = r"SYSTEM\CurrentControlSet\Services\SharedAccess\Parameters\FirewallPolicy\FirewallRules"
_PROTOCOL_TO_NUMBER = {"TCP": "6", "UDP": "17"}
_REQUIRED_FIREWALL_PROFILES = frozenset({"Domain", "Private", "Public"})


def _read_firewall_rules():
    # Retourne [(value_name, raw_string), ...] : value_name = nom interne de la regle.
    rules = []
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _FIREWALL_RULES_REG_PATH)
    except OSError:
        return rules
    try:
        index = 0
        while True:
            try:
                name, value, _kind = winreg.EnumValue(key, index)
            except OSError:
                break
            index += 1
            if isinstance(value, str):
                rules.append((name, value))
    finally:
        winreg.CloseKey(key)
    return rules


def _parse_firewall_rule(raw):
    # Format brut : "v2.10|Action=Allow|Active=TRUE|Dir=In|Protocol=6|LPort=7678|Name=...|".
    fields = {}
    for chunk in raw.split("|"):
        if "=" in chunk:
            key, value = chunk.split("=", 1)
            fields[key] = value
    return fields


def _rule_profiles(raw):
    # Profils couverts par la regle. Ensemble vide impossible :
    # absence du champ Profile/Profile2 = "Any" => Domain+Private+Public.
    # Profile peut etre une liste comma-separee ("Domain,Private,Public") ou une seule valeur ("Public").
    # Profile2 est un bitmask (1=Domain, 2=Private, 4=Public).
    profiles = set()
    has_field = False
    for chunk in raw.split("|"):
        if not chunk or "=" not in chunk:
            continue
        key, value = chunk.split("=", 1)
        if key == "Profile":
            has_field = True
            for piece in value.replace(";", ",").split(","):
                piece = piece.strip()
                if piece:
                    profiles.add(piece)
        elif key == "Profile2":
            has_field = True
            try:
                bits = int(value)
            except ValueError:
                continue
            if bits & 1:
                profiles.add("Domain")
            if bits & 2:
                profiles.add("Private")
            if bits & 4:
                profiles.add("Public")
    if not has_field:
        return set(_REQUIRED_FIREWALL_PROFILES)
    return profiles


def _rule_lport_matches(fields, port):
    # LPort accepte une liste separee par virgules ("4900,4901") ou des plages ("4900-4910").
    raw = fields.get("LPort", "")
    if not raw:
        return False
    target = str(port)
    for piece in raw.split(","):
        piece = piece.strip()
        if piece == target:
            return True
        if "-" in piece:
            try:
                start, end = piece.split("-", 1)
                if int(start) <= int(target) <= int(end):
                    return True
            except ValueError:
                continue
    return False


def firewall_port_open(port, protocol=FIREWALL_RULE_PROTOCOL):
    # Verifie l'existence d'une regle active inbound allow ouvrant le port donne.
    proto_num = _PROTOCOL_TO_NUMBER.get(protocol.upper(), protocol)
    try:
        for _name, raw in _read_firewall_rules():
            fields = _parse_firewall_rule(raw)
            if (fields.get("Active", "").upper() == "TRUE"
                    and fields.get("Dir", "") == "In"
                    and fields.get("Action", "") == "Allow"
                    and fields.get("Protocol", "") == proto_num
                    and _rule_lport_matches(fields, port)):
                return True
    except Exception:
        return False
    return False


