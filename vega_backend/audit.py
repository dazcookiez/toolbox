"""Journal d'audit structure pour Vega Toolbox.

Genere deux fichiers dans le repertoire de l'EXE (la ou le user a lance
vega_toolbox.exe), pour que le superviseur trouve toujours le journal au
meme endroit, peu importe la racine Vega selectionnee :
  - vega_toolbox_audit.json : source de verite, append-only, liste d'entrees
  - vega_toolbox_audit.html : rendu lisible pour superviseur (table CSS embeded)

Chaque entree contient : timestamp ISO, utilisateur Windows, machine, action
(snake_case), status (success/warning/failure), details (dict), error (str|null).
La regeneration HTML se fait a chaque append - le supervisor peut ouvrir le HTML
directement dans son navigateur a tout moment.

Thread-safe : un verrou protege la lecture/append/rerender du couple JSON+HTML.
"""
import getpass
import html
import json
import os
import socket
import sys
import threading
from datetime import datetime
from pathlib import Path


_LOCK = threading.Lock()

JSON_NAME = "vega_toolbox_audit.json"
HTML_NAME = "vega_toolbox_audit.html"


# Mappings snake_case -> libelles humains francais + emoji.
# Si une action n'est pas dans la map, on utilise le snake_case tel quel.
ACTION_LABELS = {
    "app_start":                         ("▶ Demarrage de l'application", "Demarrage"),
    "navigate":                          ("➤ Changement d'onglet",       "Navigation"),
    "log":                               ("ℹ Message du journal",        "Journal"),
    "task_start":                        ("◯ Tache demarree",            "Tache"),
    "task_done":                         ("✔ Tache terminee",            "Tache"),
    "migration_run":                     ("⚡ Migration Vega",            "Migration"),
    "migration_rollback":                ("↺ Retour arriere migration",  "Migration"),
    "hfsql_start":                       ("▶ Demarrage du service HFSQL",  "HFSQL"),
    "hfsql_stop":                        ("■ Arret du service HFSQL",       "HFSQL"),
    "retailforce_json_reset":            ("✎ Reinitialisation JSON RetailForce", "RetailForce"),
    "firewall_rules_create_or_repair":   ("⛨ Creation ou reparation des regles pare-feu", "Pare-feu"),
    "vega_cleanup":                      ("✨ Nettoyage du dossier Vega",   "Nettoyage"),
    "network_set_static_ip":             ("⦵ Passage en IP fixe",          "Reseau"),
    "network_set_dhcp":                  ("↻ Retour en DHCP",              "Reseau"),
    "impressions_vega_create_or_repair": ("⧉ Creation ou reparation Impressions Vega", "Impressions"),
    "smtp_test_send":                    ("✉ Test d'envoi de mail",        "Mail"),
    "notepad_jsontools_install":         ("⤓ Installation Notepad++ + JsonTools", "Outils"),
    "retailforce_install":               ("⤓ Installation RetailForce",    "RetailForce"),
    "downloads_completed":               ("⤓ Telechargement termine",      "Telechargement"),
    "downloads_failed":                  ("✕ Telechargement echoue",       "Telechargement"),
    "downloads_cancelled":               ("⊘ Telechargement annule",       "Telechargement"),
}

STATUS_LABELS = {
    "success": "Succes",
    "warning": "Avertissement",
    "failure": "Echec",
    "info":    "Information",
}

# Mappings de cles techniques -> libelles francais pour le panneau Details.
DETAIL_KEYS = {
    "root":               "Racine Vega",
    "operation_id":       "Identifiant operation",
    "status":             "Statut",
    "title":              "Tache",
    "key":                "Cle",
    "from":               "Depuis",
    "to":                 "Vers",
    "level":              "Niveau",
    "message":            "Message",
    "version":            "Version",
    "exe":                "Chemin executable",
    "count":              "Nombre",
    "files":              "Fichiers",
    "target_dir":         "Dossier cible",
    "vega6_extracted":    "Paquet VEGA6 extrait",
    "vega_root":          "Racine Vega",
    "adapter":            "Carte reseau",
    "ipv4":               "Adresse IPv4",
    "subnet":             "Masque de sous-reseau",
    "gateway":            "Passerelle",
    "host":               "Serveur SMTP",
    "port":               "Port",
    "auth_method":        "Methode d'authentification",
    "cancelled":          "Annule",
    "npp_version":        "Version Notepad++",
    "jsontools_version":  "Version JsonTools",
    "previous_version":   "Version precedente",
    "new_version":        "Nouvelle version",
    "backup_path":        "Sauvegarde",
    "json_reset_confirmed": "JSON reinitialise (confirme)",
    "hfsql_stopped_confirmed": "HFSQL arrete (confirme)",
    "firewall_ok":        "Pare-feu conforme",
    "moved_files":        "Fichiers deplaces",
    "deleted_files":      "Fichiers supprimes",
    "total_actions":      "Nombre d'actions",
    "par_categorie":      "Repartition par categorie",
    "actions_detaillees": "Detail des actions",
    "log_path":           "Journal",
    "folder_ok":          "Dossier conforme",
    "share_ok":           "Partage conforme",
    "retailforce_ok":     "RetailForce conforme",
    "hfsql_ok":           "HFSQL conforme",
    "result_keys":        "Donnees produites",
    "result_count":       "Nombre de resultats",
}


def _human_action(action):
    info = ACTION_LABELS.get(action)
    if info:
        return info[0], info[1]
    # Fallback : on rend la snake_case lisible (mots separes par espace, capitalise).
    label = action.replace("_", " ").strip()
    if label:
        label = label[0].upper() + label[1:]
    return label or action, "Autre"


def _human_status(status):
    return STATUS_LABELS.get(status, status or "?")


def _default_audit_dir():
    # Repertoire de l'EXE en mode frozen (PyInstaller), sinon CWD en dev.
    # sys.executable pointe vers le launcher PyInstaller en mode frozen.
    if getattr(sys, "frozen", False):
        try:
            return Path(sys.executable).resolve().parent
        except Exception:
            pass
    return Path.cwd()


class AuditLogger:
    def __init__(self, logger, tool_version=""):
        self.logger = logger
        self.tool_version = tool_version
        # Le dossier d'audit est FIGE au lancement de l'app, pas lie a la
        # racine Vega selectionnee : un superviseur sait toujours ou aller chercher.
        self._audit_dir = _default_audit_dir()
        # Cache des metadonnees user/machine pour eviter de re-resoudre a chaque entry.
        try:
            self._user = getpass.getuser()
        except Exception:
            self._user = os.environ.get("USERNAME") or "?"
        try:
            self._machine = socket.gethostname()
        except Exception:
            self._machine = os.environ.get("COMPUTERNAME") or "?"

    def set_vega_root(self, root):
        # No-op pour compatibilite : l'audit n'est plus lie a la racine Vega.
        # Conserve pour eviter de casser les appels existants depuis app.py.
        pass

    @property
    def audit_dir(self):
        return self._audit_dir

    def log_action(self, action, status="success", details=None, error=None):
        # Append une entree au journal d'audit + regenere le HTML.
        # action : nom snake_case (ex: "migration_run", "clean_remove_files").
        # status : success | warning | failure | info.
        # details : dict serialisable (parametres, comptages, paths).
        # error : str ou None.
        root = self._audit_dir
        try:
            with _LOCK:
                entries = self._load_entries(root)
                entry = {
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "user": self._user,
                    "machine": self._machine,
                    "tool_version": self.tool_version,
                    "action": action,
                    "status": status,
                    "details": details or {},
                    "error": str(error) if error else None,
                }
                entries.append(entry)
                self._save_entries(root, entries)
                self._render_html(root, entries)
        except Exception as exc:
            # Audit non bloquant : si ca echoue, on log dans le ToolLogger
            # mais on ne casse pas l'action en cours.
            try:
                self.logger.warn(f"Audit log a echoue pour '{action}' : {exc}")
            except Exception:
                pass

    # -------------------------------------------------------------------
    # I/O internes
    # -------------------------------------------------------------------

    def _json_path(self, root):
        return Path(root) / JSON_NAME

    def _html_path(self, root):
        return Path(root) / HTML_NAME

    def _load_entries(self, root):
        path = self._json_path(root)
        if not path.exists():
            return []
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "entries" in data:
                return data["entries"]
            if isinstance(data, list):
                return data
            return []
        except Exception:
            return []

    def _save_entries(self, root, entries):
        path = self._json_path(root)
        payload = {
            "tool": "Vega Toolbox",
            "tool_version": self.tool_version,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "machine": self._machine,
            "entry_count": len(entries),
            "entries": entries,
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            # default=str : fallback pour les types non-JSON (Path, datetime,
            # set, etc.) qu'on pourrait passer par erreur dans details=. Sans
            # ca toute l'ecriture du JSON echoue et on perd l'audit complet.
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
        os.replace(tmp, path)

    def _render_html(self, root, entries):
        # Render HTML totalement en francais, lisible pour un superviseur non-tech.
        # Une seule colonne 'Action' qui affiche un libelle humain (emoji + texte)
        # + chip categorie ; les colonnes Machine/Utilisateur ont ete retirees
        # (info dispo dans le tooltip / panneau details si vraiment utile).
        path = self._html_path(root)

        # Statistiques pour le bandeau d'en-tete : compte par statut sur les
        # entrees "metier" uniquement (on exclut log/navigate/task_start/done
        # du compteur pour eviter de noyer le superviseur dans les milliers
        # de lignes de journal).
        metier_status = []
        chrono_first = None
        chrono_last = None
        for e in entries:
            ts = e.get("timestamp")
            if ts:
                if chrono_first is None or ts < chrono_first:
                    chrono_first = ts
                if chrono_last is None or ts > chrono_last:
                    chrono_last = ts
            action = e.get("action", "")
            if action not in ("log", "navigate", "task_start", "task_done"):
                metier_status.append(e.get("status", ""))
        nb_ok = sum(1 for s in metier_status if s == "success")
        nb_warn = sum(1 for s in metier_status if s == "warning")
        nb_err = sum(1 for s in metier_status if s == "failure")
        nb_metier = len(metier_status)

        rows = []
        # Tri descendant : plus recent en haut.
        for e in sorted(entries, key=lambda x: x.get("timestamp", ""), reverse=True):
            status = e.get("status", "")
            status_class = {
                "success": "ok", "warning": "warn",
                "failure": "err", "info": "info",
            }.get(status, "")
            action_raw = e.get("action", "")
            action_label, category = _human_action(action_raw)
            status_label = _human_status(status)
            details_html = self._fmt_details(e.get("details") or {})
            error_html = ""
            if e.get("error"):
                error_html = (
                    f'<div class="error"><strong>Message d\'erreur :</strong> '
                    f'{html.escape(str(e["error"]))}</div>'
                )
            ts_full = e.get("timestamp", "")
            # Affichage date plus lisible : "2026-05-27 10:41:33" -> "27/05/2026 10:41:33"
            ts_display = ts_full
            try:
                d, t = ts_full.split("T") if "T" in ts_full else ts_full.split(" ")
                y, mo, da = d.split("-")
                ts_display = f"{da}/{mo}/{y}<br><span class='hour'>{t}</span>"
            except Exception:
                pass
            # is_log = entree "journal" → on la rend plus discrete
            row_class = status_class
            if action_raw in ("log", "navigate", "task_start", "task_done"):
                row_class += " minor"
            rows.append(
                f'<tr class="{row_class}" data-cat="{html.escape(category)}" data-status="{status}">'
                f'<td class="ts">{ts_display}</td>'
                f'<td class="action"><div class="action-label">{html.escape(action_label)}</div>'
                f'<div class="action-cat">{html.escape(category)}</div></td>'
                f'<td><span class="badge {status_class}">{html.escape(status_label)}</span></td>'
                f'<td>{details_html}{error_html}</td>'
                f'</tr>'
            )
        rows_html = "\n".join(rows) or (
            '<tr><td colspan="4" class="empty">Aucune action n\'a encore ete enregistree.</td></tr>'
        )

        # Bandeau plage temporelle
        plage_html = ""
        if chrono_first and chrono_last:
            try:
                f_d = chrono_first.split("T")[0] if "T" in chrono_first else chrono_first.split(" ")[0]
                l_d = chrono_last.split("T")[0] if "T" in chrono_last else chrono_last.split(" ")[0]
                fy, fm, fd = f_d.split("-")
                ly, lm, ld_ = l_d.split("-")
                plage_html = f"Du <strong>{fd}/{fm}/{fy}</strong> au <strong>{ld_}/{lm}/{ly}</strong>"
            except Exception:
                pass

        content = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<title>Journal d'activite Vega Toolbox - {html.escape(self._machine)}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{
    font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif;
    margin: 0; padding: 24px;
    background: #eef1f5; color: #1f2937;
  }}
  .container {{ max-width: 1400px; margin: 0 auto; }}
  header.hero {{
    background: linear-gradient(135deg, #0a246a 0%, #1e3a8a 100%);
    color: white; padding: 24px 28px; border-radius: 8px 8px 0 0;
    box-shadow: 0 2px 6px rgba(0,0,0,0.08);
  }}
  header.hero h1 {{ margin: 0 0 6px 0; font-size: 22px; font-weight: 600; }}
  header.hero p {{ margin: 0; opacity: 0.85; font-size: 13px; }}
  .helpbox {{
    background: #fff8e1; border-left: 4px solid #f0ad4e;
    padding: 12px 16px; margin: 0; font-size: 13px; color: #4a3c0a;
  }}
  .helpbox strong {{ color: #1f2937; }}
  .stats {{
    display: grid; grid-template-columns: repeat(4, 1fr); gap: 1px;
    background: #d8dde6; padding: 1px; margin: 0;
  }}
  .stat {{ background: white; padding: 16px 20px; text-align: center; }}
  .stat .num {{ font-size: 28px; font-weight: 700; line-height: 1; }}
  .stat .lbl {{ font-size: 12px; color: #6b7280; margin-top: 4px; text-transform: uppercase; letter-spacing: 0.5px; }}
  .stat.ok .num   {{ color: #16a34a; }}
  .stat.warn .num {{ color: #d97706; }}
  .stat.err .num  {{ color: #dc2626; }}
  .stat.total .num{{ color: #0a246a; }}
  .controls {{
    background: white; padding: 14px 20px;
    display: flex; gap: 12px; align-items: center; flex-wrap: wrap;
    border-bottom: 1px solid #e5e7eb;
  }}
  .controls input.search {{
    flex: 1; min-width: 220px; padding: 9px 12px; font-size: 14px;
    border: 1px solid #cbd5e1; border-radius: 6px;
  }}
  .controls input.search:focus {{ outline: 2px solid #0a246a; outline-offset: -1px; }}
  .controls label {{ font-size: 13px; color: #6b7280; cursor: pointer; user-select: none; }}
  .controls input[type=checkbox] {{ margin-right: 4px; vertical-align: middle; }}
  table {{
    border-collapse: collapse; width: 100%; background: white;
    box-shadow: 0 2px 6px rgba(0,0,0,0.08); border-radius: 0 0 8px 8px;
    overflow: hidden;
  }}
  th, td {{
    padding: 11px 14px; border-bottom: 1px solid #f0f2f5;
    text-align: left; font-size: 13px; vertical-align: top;
  }}
  th {{
    background: #f3f5f8; color: #374151; font-weight: 600;
    font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px;
    position: sticky; top: 0; z-index: 1;
  }}
  td.ts {{
    white-space: nowrap; font-family: 'Consolas', 'Courier New', monospace;
    color: #4b5563; font-size: 12px; line-height: 1.4;
  }}
  td.ts .hour {{ color: #9ca3af; font-size: 11px; }}
  td.action .action-label {{ font-weight: 600; color: #1f2937; }}
  td.action .action-cat {{
    display: inline-block; margin-top: 3px;
    font-size: 10px; color: #6b7280; background: #f3f5f8;
    padding: 1px 8px; border-radius: 10px; text-transform: uppercase; letter-spacing: 0.4px;
  }}
  tr.ok    {{ background: #f5fbf3; }}
  tr.warn  {{ background: #fffaeb; }}
  tr.err   {{ background: #fef0f0; }}
  tr.info  {{ background: #f6f8fc; }}
  tr.minor {{ opacity: 0.65; }}
  tr.minor td.action .action-label {{ font-weight: 400; color: #6b7280; }}
  .badge {{
    display: inline-block; padding: 3px 10px; border-radius: 12px;
    font-size: 11px; font-weight: 600; white-space: nowrap;
  }}
  .badge.ok   {{ background: #16a34a; color: white; }}
  .badge.warn {{ background: #d97706; color: white; }}
  .badge.err  {{ background: #dc2626; color: white; }}
  .badge.info {{ background: #64748b; color: white; }}
  ul.details {{ margin: 0; padding: 0; list-style: none; font-size: 12px; }}
  ul.details li {{ padding: 2px 0; }}
  ul.details strong {{ color: #475569; font-weight: 600; }}
  details.expand {{ display: inline-block; margin-top: 4px; }}
  details.expand > summary {{
    cursor: pointer; color: #0a246a; font-weight: 600; font-size: 11px;
    padding: 2px 8px; background: #eef1f5; border-radius: 3px; display: inline-block;
    user-select: none;
  }}
  details.expand[open] > summary {{ background: #d6dde9; }}
  details.expand > summary::marker {{ content: '+ '; }}
  details.expand[open] > summary::marker {{ content: '− '; }}
  ol.expand-list {{
    margin: 6px 0 4px 0; padding: 8px 14px 8px 30px; background: #fafbfc;
    border-radius: 4px; max-height: 380px; overflow-y: auto;
    font-family: 'Consolas', 'Courier New', monospace; font-size: 11px;
    color: #374151;
  }}
  ol.expand-list li {{ padding: 2px 0; }}
  table.expand-table {{
    margin: 6px 0 4px 0; background: #fafbfc; border-radius: 4px;
    border-collapse: collapse; font-size: 12px;
  }}
  table.expand-table th {{
    background: #f3f5f8; color: #4b5563; padding: 4px 10px; text-align: left;
    font-weight: 600; border-bottom: 1px solid #e5e7eb; font-size: 11px;
  }}
  table.expand-table td {{ padding: 4px 10px; border-bottom: 1px solid #e5e7eb; }}
  .error {{
    color: #b91c1c; background: #fee2e2; padding: 6px 10px;
    border-radius: 4px; font-size: 12px; margin-top: 6px;
  }}
  .empty {{ text-align: center; color: #9ca3af; padding: 50px 20px; font-style: italic; }}
  footer {{ text-align: center; color: #94a3b8; font-size: 11px; padding: 20px; }}
</style>
</head>
<body>
<div class="container">
<header class="hero">
  <h1>Journal d'activite Vega Toolbox</h1>
  <p>Poste <strong>{html.escape(self._machine)}</strong> &middot; {plage_html} &middot; Mis a jour le {html.escape(datetime.now().strftime('%d/%m/%Y a %H:%M:%S'))}</p>
</header>
<div class="helpbox">
  <strong>Comment utiliser ce journal ?</strong>
  Chaque ligne represente une action effectuee sur ce poste par Vega Toolbox.
  Les lignes en <span style="background:#f5fbf3;padding:1px 6px;">vert</span> sont des reussites,
  en <span style="background:#fffaeb;padding:1px 6px;">jaune</span> des avertissements,
  en <span style="background:#fef0f0;padding:1px 6px;">rouge</span> des echecs.
  Utilisez la barre de recherche pour filtrer (ex : "migration", "echec"...).
  Cochez "Masquer les details techniques" pour ne voir que les vraies operations.
</div>
<div class="stats">
  <div class="stat total"><div class="num">{nb_metier}</div><div class="lbl">Operations totales</div></div>
  <div class="stat ok"><div class="num">{nb_ok}</div><div class="lbl">Reussites</div></div>
  <div class="stat warn"><div class="num">{nb_warn}</div><div class="lbl">Avertissements</div></div>
  <div class="stat err"><div class="num">{nb_err}</div><div class="lbl">Echecs</div></div>
</div>
<div class="controls">
  <input type="text" class="search" id="filter" placeholder="Rechercher (ex : migration, echec, telechargement, 27/05...)" onkeyup="filterRows()">
  <label><input type="checkbox" id="hideMinor" onchange="toggleMinor()" checked> Masquer les details techniques (journal, navigation, taches)</label>
</div>
<table>
<thead>
<tr>
  <th style="width:120px">Date &amp; heure</th>
  <th style="width:280px">Action</th>
  <th style="width:130px">Resultat</th>
  <th>Details</th>
</tr>
</thead>
<tbody id="rows">
{rows_html}
</tbody>
</table>
<footer>Genere par Vega Toolbox version {html.escape(self.tool_version)}</footer>
</div>
<script>
function filterRows() {{
  var q = document.getElementById('filter').value.toLowerCase();
  var rows = document.querySelectorAll('#rows tr');
  rows.forEach(function(r) {{
    var matches = r.innerText.toLowerCase().indexOf(q) >= 0;
    r.dataset.matchSearch = matches ? '1' : '0';
  }});
  applyVisibility();
}}
function toggleMinor() {{
  applyVisibility();
}}
function applyVisibility() {{
  var hideMinor = document.getElementById('hideMinor').checked;
  var rows = document.querySelectorAll('#rows tr');
  rows.forEach(function(r) {{
    var matchSearch = r.dataset.matchSearch !== '0';
    var isMinor = r.classList.contains('minor');
    if (matchSearch && (!hideMinor || !isMinor)) {{
      r.style.display = '';
    }} else {{
      r.style.display = 'none';
    }}
  }});
}}
// Init : masque les details techniques par defaut
window.addEventListener('DOMContentLoaded', function() {{
  document.querySelectorAll('#rows tr').forEach(function(r) {{ r.dataset.matchSearch = '1'; }});
  applyVisibility();
}});
</script>
</body>
</html>
"""
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)

    def _fmt_details(self, details):
        # Render des details avec support des grosses listes / dicts via
        # <details><summary> HTML natif (toggle +/- sans JS). Une liste
        # > 3 elements ou un dict > 3 cles devient expandable. Le user
        # voit un resume + un bouton 'Voir tout' qui deroule le detail.
        if not details:
            return '<em style="color:#9ca3af">Aucun detail</em>'
        items = []
        for k, v in details.items():
            label = DETAIL_KEYS.get(k, k.replace("_", " ").capitalize())
            label_esc = html.escape(str(label))

            if isinstance(v, (list, tuple)):
                items.append(self._fmt_list_item(label_esc, v))
            elif isinstance(v, dict):
                items.append(self._fmt_dict_item(label_esc, v))
            elif isinstance(v, bool):
                items.append(f"<li><strong>{label_esc} :</strong> {('Oui' if v else 'Non')}</li>")
            elif v is None or v == "":
                items.append(f"<li><strong>{label_esc} :</strong> —</li>")
            else:
                items.append(f"<li><strong>{label_esc} :</strong> {html.escape(str(v))}</li>")
        return "<ul class='details'>" + "".join(items) + "</ul>"

    def _fmt_list_item(self, label_esc, lst):
        # Liste : 0 -> (vide), <=3 -> inline, plus -> <details> expandable
        if not lst:
            return f"<li><strong>{label_esc} :</strong> (vide)</li>"
        n = len(lst)
        if n <= 3:
            inline = ", ".join(html.escape(str(x)) for x in lst)
            return f"<li><strong>{label_esc} :</strong> {inline}</li>"
        # Long : preview 3 premiers + <details> pour tout voir
        preview = ", ".join(html.escape(str(x)) for x in lst[:3])
        all_lis = "".join(
            f"<li>{html.escape(str(x))}</li>" for x in lst
        )
        return (
            f"<li><strong>{label_esc} :</strong> {preview} … "
            f"<details class='expand'><summary>Voir tout ({n} éléments)</summary>"
            f"<ol class='expand-list'>{all_lis}</ol></details></li>"
        )

    def _fmt_dict_item(self, label_esc, d):
        # Dict : <=3 cles -> inline JSON, plus -> <details> table
        if not d:
            return f"<li><strong>{label_esc} :</strong> (vide)</li>"
        if len(d) <= 3:
            inline = ", ".join(
                f"{html.escape(str(k))}={html.escape(str(val))}"
                for k, val in d.items()
            )
            return f"<li><strong>{label_esc} :</strong> {inline}</li>"
        rows = "".join(
            f"<tr><th>{html.escape(str(k))}</th><td>{html.escape(str(v))}</td></tr>"
            for k, v in d.items()
        )
        return (
            f"<li><strong>{label_esc} :</strong> "
            f"<details class='expand'><summary>Voir tout ({len(d)} entrées)</summary>"
            f"<table class='expand-table'>{rows}</table></details></li>"
        )
