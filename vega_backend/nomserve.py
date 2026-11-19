"""Lecture / creation du fichier nomserve.txt (format Vega V6).

Format V6 attendu dans la base (ex. C:\\VEGACS\\BDD\\MAIN\\nomserve.txt) :

    [BDD HFCS]
    SERVEUR=192.168.1.1:4900
    BASE=MAIN
    UTILISATEUR=Admin
    MDP=

    [REPIMPRESSION]
    rep=\\\\192.168.1.1\\ImpressionsVega$

La migration deplace l'ancien nomserve.txt (V5) dans vega.dos\\V6\\nomserve-v5 :
on le relit uniquement pour en deduire l'IP du serveur.

Ecriture : les cles connues sont mises a jour en place, tout le reste du
fichier (autres sections, autres cles, commentaires) est conserve. L'ancien
fichier est sauvegarde dans <base>\\.vega_tool_state\\nomserve\\ avant ecriture.
"""
import datetime
import re
import shutil
from pathlib import Path

from ._common import STATE_DIR_NAME, V6_TARGET_REL, OperationError, hide_windows_path

NOMSERVE_NAME = "nomserve.txt"
V5_COPY_NAME = "nomserve-v5"
DEFAULT_PORT = "4900"
DEFAULT_USER = "Admin"
PRINT_SHARE_NAME = "ImpressionsVega$"

SECTION_BDD = "BDD HFCS"
SECTION_PRINT = "REPIMPRESSION"

# (section, cle) -> nom du champ dans le dict "values".
_FIELDS = (
    (SECTION_BDD, "SERVEUR", "serveur"),
    (SECTION_BDD, "BASE", "base"),
    (SECTION_BDD, "UTILISATEUR", "utilisateur"),
    (SECTION_BDD, "MDP", "mdp"),
    (SECTION_PRINT, "rep", "rep"),
)

_IPV4_RE = re.compile(r"(?<![\d.])((?:\d{1,3}\.){3}\d{1,3})(?::(\d{2,5}))?(?![\d.])")
_SECTION_RE = re.compile(r"^\s*\[([^\]]*)\]\s*$")


def _read_text(path):
    # Fichier Windows edite a la main : souvent ANSI (cp1252), parfois UTF-8.
    data = Path(path).read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace"), "utf-8-sig"
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace"), "cp1252"


def parse_nomserve(text):
    # Retourne {(SECTION_UPPER, CLE_UPPER): valeur} (premiere occurrence).
    values = {}
    section = ""
    for line in (text or "").splitlines():
        match = _SECTION_RE.match(line)
        if match:
            section = match.group(1).strip().upper()
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith(";") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values.setdefault((section, key.strip().upper()), value.strip())
    return values


def values_from_text(text):
    parsed = parse_nomserve(text)
    out = {}
    for section, key, field in _FIELDS:
        value = parsed.get((section.upper(), key.upper()))
        if value is not None:
            out[field] = value
    return out


def split_server(serveur):
    # "192.168.1.1:4900" -> ("192.168.1.1", "4900"). Port optionnel.
    serveur = (serveur or "").strip()
    if ":" in serveur:
        host, port = serveur.rsplit(":", 1)
        return host.strip(), port.strip()
    return serveur, ""


def default_print_share(host):
    host = (host or "").strip()
    return f"\\\\{host}\\{PRINT_SHARE_NAME}" if host else ""


def build_nomserve(values):
    # Fichier V6 complet, a partir de zero.
    lines = [f"[{SECTION_BDD}]"]
    for section, key, field in _FIELDS:
        if section == SECTION_BDD:
            lines.append(f"{key}={values.get(field, '')}")
    lines.append("")
    lines.append(f"[{SECTION_PRINT}]")
    for section, key, field in _FIELDS:
        if section == SECTION_PRINT:
            lines.append(f"{key}={values.get(field, '')}")
    return "\r\n".join(lines) + "\r\n"


def merge_nomserve(existing_text, values):
    # Met a jour les cles connues dans un fichier existant, sans toucher au reste.
    # Fichier vide ou encore au format V5 (pas de [BDD HFCS]) : on repart
    # d'un fichier V6 propre. L'ancien contenu reste dans la sauvegarde.
    if (SECTION_BDD.upper(), "SERVEUR") not in parse_nomserve(existing_text or ""):
        return build_nomserve(values)

    wanted = {}
    for section, key, field in _FIELDS:
        wanted.setdefault(section.upper(), []).append((key, field))

    lines = existing_text.splitlines()
    out = []
    done = set()
    section = None

    def flush_missing(sec):
        # Ajoute en fin de section les cles connues absentes du fichier.
        if sec is None:
            return
        added = []
        for key, field in wanted.get(sec, []):
            if (sec, key.upper()) not in done:
                added.append(f"{key}={values.get(field, '')}")
                done.add((sec, key.upper()))
        if added:
            # Insere avant les lignes vides de fin de section.
            tail = []
            while out and not out[-1].strip():
                tail.insert(0, out.pop())
            out.extend(added)
            out.extend(tail)

    seen_sections = set()
    for line in lines:
        match = _SECTION_RE.match(line)
        if match:
            flush_missing(section)
            section = match.group(1).strip().upper()
            seen_sections.add(section)
            out.append(line)
            continue
        stripped = line.strip()
        if section in wanted and "=" in stripped and not stripped.startswith(";"):
            key = stripped.split("=", 1)[0].strip()
            for wanted_key, field in wanted[section]:
                if key.upper() == wanted_key.upper() and (section, key.upper()) not in done:
                    line = f"{key}={values.get(field, '')}"
                    done.add((section, key.upper()))
                    break
        out.append(line)
    flush_missing(section)

    for sec_name in (SECTION_BDD, SECTION_PRINT):
        if sec_name.upper() in seen_sections:
            continue
        if out and out[-1].strip():
            out.append("")
        out.append(f"[{sec_name}]")
        for key, field in wanted[sec_name.upper()]:
            out.append(f"{key}={values.get(field, '')}")
    return "\r\n".join(out).rstrip("\r\n") + "\r\n"


def find_ipv4(text):
    # Premiere IPv4 "utile" d'un texte (ignore 127.x et 0.x) -> (ip, port|None).
    for match in _IPV4_RE.finditer(text or ""):
        ip = match.group(1)
        parts = ip.split(".")
        if any(int(p) > 255 for p in parts):
            continue
        if parts[0] in ("127", "0"):
            continue
        return ip, match.group(2)
    return None, None


def _unc_host(path):
    text = str(path)
    if text.startswith("\\\\"):
        host = text[2:].split("\\", 1)[0]
        return host or None
    return None


class NomserveManager:
    def __init__(self, logger):
        self.logger = logger

    @staticmethod
    def nomserve_path(base):
        return Path(base) / NOMSERVE_NAME

    @staticmethod
    def v5_copy_path(base):
        # Copie V5 laissee par la migration (fichier sans extension).
        candidate = Path(base) / V6_TARGET_REL / V5_COPY_NAME
        if candidate.is_dir():
            inner = candidate / NOMSERVE_NAME
            return inner if inner.is_file() else None
        return candidate if candidate.is_file() else None

    def local_ipv4s(self):
        # IPv4 des cartes actives de ce poste (hors APIPA), carte "Up" en premier.
        try:
            from .fixed_ip import enumerate_adapters
            adapters = enumerate_adapters()
        except Exception as exc:
            self.logger.warn(f"Lecture des cartes réseau impossible : {exc}")
            return []
        ips = []
        ordered = sorted(adapters, key=lambda a: 0 if str(a.get("status", "")).lower() == "up" else 1)
        for adapter in ordered:
            ip = adapter.get("ipv4") or ""
            if not ip or ip.startswith("169.254.") or ip.startswith("127."):
                continue
            if ip not in ips:
                ips.append(ip)
        return ips

    def read(self, base):
        # Etat complet pour l'onglet : fichier actuel, copie V5, suggestions.
        base = Path(base)
        if not base.is_dir():
            raise OperationError(f"Base Vega introuvable : {base}")
        path = self.nomserve_path(base)
        result = {
            "base": str(base),
            "path": str(path),
            "exists": path.is_file(),
            "text": "",
            "encoding": None,
            "values": {},
            "is_v6": False,
            "v5_path": None,
            "v5_text": "",
            "local_ips": self.local_ipv4s(),
        }
        if result["exists"]:
            text, encoding = _read_text(path)
            result["text"] = text
            result["encoding"] = encoding
            result["values"] = values_from_text(text)
            result["is_v6"] = (SECTION_BDD.upper(), "SERVEUR") in parse_nomserve(text)

        v5 = self.v5_copy_path(base)
        if v5 is not None:
            try:
                result["v5_path"] = str(v5)
                result["v5_text"] = _read_text(v5)[0]
            except Exception as exc:
                self.logger.warn(f"Lecture de {v5} impossible : {exc}")

        result["suggested"] = self.suggest(base, result)
        return result

    def suggest(self, base, state):
        # Valeurs proposees par defaut. Ordre de priorite pour l'IP :
        #   1. nomserve.txt actuel s'il est deja au format V6
        #   2. IP trouvee dans l'ancien nomserve V5
        #   3. hote UNC si la base est sur un partage reseau
        #   4. IP de ce poste (cas courant : la base est sur le serveur)
        current = state.get("values") or {}
        host, port = split_server(current.get("serveur", ""))
        source = "nomserve actuel" if host else ""
        if not host:
            # Seule l'IP est reprise du V5 : le port V6 reste 4900 par defaut
            # (le moteur HFSQL V6 n'utilise pas forcement le port du V5).
            ip = find_ipv4(state.get("v5_text", ""))[0]
            if ip:
                host, source = ip, "ancien nomserve V5 (vega.dos\\V6\\nomserve-v5)"
            elif state.get("text") and not state.get("is_v6"):
                ip = find_ipv4(state.get("text", ""))[0]
                if ip:
                    host, source = ip, "nomserve actuel (format V5)"
        if not host:
            unc = _unc_host(base)
            if unc:
                host, source = unc, "chemin réseau de la base"
        if not host and state.get("local_ips"):
            host, source = state["local_ips"][0], "IP de ce poste"
        port = port or DEFAULT_PORT
        return {
            "host": host or "",
            "port": port,
            "host_source": source,
            "base": current.get("base") or Path(base).name,
            "utilisateur": current.get("utilisateur") or DEFAULT_USER,
            "mdp": current.get("mdp", ""),
            "rep": current.get("rep") or default_print_share(host),
        }

    def preview(self, base, values):
        path = self.nomserve_path(base)
        existing = ""
        if path.is_file():
            existing = _read_text(path)[0]
        return merge_nomserve(existing, values)

    def write(self, base, values):
        base = Path(base)
        if not base.is_dir():
            raise OperationError(f"Base Vega introuvable : {base}")
        path = self.nomserve_path(base)
        existing, encoding = "", "cp1252"
        backup = None
        if path.is_file():
            existing, encoding = _read_text(path)
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            state_dir = base / STATE_DIR_NAME
            backup_dir = state_dir / "nomserve"
            backup_dir.mkdir(parents=True, exist_ok=True)
            hide_windows_path(state_dir)
            # Jamais d'ecrasement : deux enregistrements dans la meme seconde
            # perdraient sinon la sauvegarde du fichier d'origine.
            backup = backup_dir / f"nomserve_{stamp}.txt"
            counter = 1
            while backup.exists():
                backup = backup_dir / f"nomserve_{stamp}_{counter}.txt"
                counter += 1
            shutil.copy2(str(path), str(backup))
            self.logger.info(f"Sauvegarde de l'ancien nomserve : {backup}")
        content = merge_nomserve(existing, values)
        try:
            data = content.encode(encoding if encoding != "utf-8-sig" else "utf-8-sig")
        except UnicodeEncodeError:
            data = content.encode("utf-8")
        path.write_bytes(data)
        self.logger.info(f"nomserve.txt écrit : {path}")
        return {"path": str(path), "backup": str(backup) if backup else None, "content": content}
