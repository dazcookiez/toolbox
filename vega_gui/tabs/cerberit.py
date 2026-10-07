"""Onglet CerberIT : installation silencieuse + enregistrement + configuration
de la sauvegarde Kiwi Backup (marque blanche CerberIT).

Structure :
  - En-tete "Compte CerberIT" : cle de contrat (a saisir) + nom machine (auto).
  - Etat de l'installation (+ bouton Verifier).
  - Configuration : arbre disques/dossiers/fichiers a cocher, motifs
    d'exclusion (defaut *.log *.log* *.bak *.old), planning (heure + jours),
    boutons Installer & configurer / Sauvegarder / Diagnostiquer / Desinstaller.
"""
import os
import socket
import string
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from vega_backend import is_admin
from vega_backend.cerberit import (
    CERBERIT_SERVER,
    DEFAULT_EXCLUDE_PATTERNS,
    DEFAULT_JOBID,
)

from ..theme import (
    ACCENT_BLUE,
    APP_TITLE,
    ERROR_COLOR,
    MUTED_TEXT,
    OK_COLOR,
    WARN_COLOR,
    WINDOW_BG,
)
from ..widgets import tool_button, value_cell

_DAY_LABELS = [
    ("monday", "Lun"), ("tuesday", "Mar"), ("wednesday", "Mer"),
    ("thursday", "Jeu"), ("friday", "Ven"), ("saturday", "Sam"), ("sunday", "Dim"),
]


class CerberitTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=8)
        self.app = app
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        # Etat arbre : item_id -> chemin absolu ; item_id -> coche (bool).
        self._item_path = {}
        self._checked = {}
        self._install_info = None

        try:
            default_host = socket.gethostname()
        except Exception:
            default_host = "PC"

        self._key_var = tk.StringVar(value="")
        self._name_var = tk.StringVar(value=default_host)
        self._exclude_var = tk.StringVar(value=" ".join(DEFAULT_EXCLUDE_PATTERNS))
        self._hour_var = tk.StringVar(value="22")
        self._min_var = tk.StringVar(value="00")
        self._day_vars = {key: tk.BooleanVar(value=True) for key, _lbl in _DAY_LABELS}
        self._status_var = tk.StringVar(value="Vérification...")
        self._sel_count_var = tk.StringVar(value="0 élément(s) sélectionné(s)")

        self._build_account_panel()
        self._build_status_panel()
        self._build_config_panel()

        self.after(200, self.refresh_status)

    # ------------------------------------------------------------------ panneaux

    def _build_account_panel(self):
        panel = tk.LabelFrame(self, text="Compte CerberIT", padx=8, pady=6, bg=WINDOW_BG)
        panel.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        panel.columnconfigure(1, weight=1)
        panel.columnconfigure(3, weight=1)

        tk.Label(panel, text="Clé de contrat :", bg=WINDOW_BG, font=("Tahoma", 9),
                 ).grid(row=0, column=0, sticky="w", padx=(0, 8), pady=2)
        tk.Entry(panel, textvariable=self._key_var, font=("Tahoma", 9),
                 ).grid(row=0, column=1, sticky="ew", pady=2)
        tk.Label(panel, text="Nom de la machine :", bg=WINDOW_BG, font=("Tahoma", 9),
                 ).grid(row=0, column=2, sticky="w", padx=(12, 8), pady=2)
        tk.Entry(panel, textvariable=self._name_var, font=("Tahoma", 9),
                 ).grid(row=0, column=3, sticky="ew", pady=2)

    def _build_status_panel(self):
        panel = tk.LabelFrame(self, text="État de l'installation", padx=8, pady=6, bg=WINDOW_BG)
        panel.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        panel.columnconfigure(1, weight=1)
        tk.Label(panel, text="CerberIT :", bg=WINDOW_BG, font=("Tahoma", 9),
                 ).grid(row=0, column=0, sticky="w", padx=(0, 12))
        self._status_badge = value_cell(panel, textvariable=self._status_var, width=60, anchor="w")
        self._status_badge.grid(row=0, column=1, sticky="ew")
        tool_button(panel, text="Vérifier", command=self.refresh_status,
                    image=self.app.icons.get("verifier"), width=12,
                    ).grid(row=0, column=2, sticky="e", padx=(8, 0))

    def _build_config_panel(self):
        panel = tk.Frame(self, bg=WINDOW_BG)
        panel.grid(row=3, column=0, sticky="nsew", pady=(6, 0))
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(1, weight=1)

        # --- Zone selection disques/fichiers ---
        sel_frame = tk.LabelFrame(
            panel, text="Quoi sauvegarder ? (cochez les disques, dossiers ou fichiers)",
            padx=6, pady=6, bg=WINDOW_BG,
        )
        sel_frame.grid(row=1, column=0, sticky="nsew")
        sel_frame.rowconfigure(0, weight=1)
        sel_frame.columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(sel_frame, show="tree", height=10, selectmode="none")
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(sel_frame, orient="vertical", command=self.tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind("<<TreeviewOpen>>", self._on_tree_open)
        self.tree.bind("<Button-1>", self._on_tree_click)
        tk.Label(sel_frame, textvariable=self._sel_count_var, bg=WINDOW_BG,
                 fg=MUTED_TEXT, font=("Tahoma", 8)).grid(row=1, column=0, sticky="w", pady=(4, 0))

        # --- Exclusions + planning ---
        opt = tk.Frame(panel, bg=WINDOW_BG)
        opt.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        opt.columnconfigure(1, weight=1)

        tk.Label(opt, text="Exclure (motifs, séparés par espace) :", bg=WINDOW_BG,
                 font=("Tahoma", 9)).grid(row=0, column=0, sticky="w", padx=(0, 8))
        tk.Entry(opt, textvariable=self._exclude_var, font=("Tahoma", 9),
                 ).grid(row=0, column=1, sticky="ew")

        sched = tk.Frame(opt, bg=WINDOW_BG)
        sched.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        tk.Label(sched, text="Sauvegarde à :", bg=WINDOW_BG, font=("Tahoma", 9),
                 ).pack(side="left", padx=(0, 6))
        tk.Spinbox(sched, from_=0, to=23, width=3, textvariable=self._hour_var,
                   format="%02.0f", font=("Tahoma", 9)).pack(side="left")
        tk.Label(sched, text="h", bg=WINDOW_BG, font=("Tahoma", 9)).pack(side="left", padx=2)
        tk.Spinbox(sched, from_=0, to=59, width=3, textvariable=self._min_var,
                   format="%02.0f", font=("Tahoma", 9)).pack(side="left")
        tk.Label(sched, text="   Jours :", bg=WINDOW_BG, font=("Tahoma", 9),
                 ).pack(side="left", padx=(10, 4))
        for key, lbl in _DAY_LABELS:
            tk.Checkbutton(sched, text=lbl, variable=self._day_vars[key], bg=WINDOW_BG,
                           activebackground=WINDOW_BG, font=("Tahoma", 8)).pack(side="left")

        # --- Boutons ---
        actions = tk.Frame(panel, bg=WINDOW_BG)
        actions.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        self._install_btn = tool_button(
            actions, text="Installer & configurer", command=self.on_install_configure,
            image=self.app.icons.get("lancer"), anchor="center",
        )
        self._install_btn.pack(side="left", padx=(0, 6))
        self._backup_btn = tool_button(
            actions, text="Sauvegarder maintenant", command=self.on_backup_now,
            image=self.app.icons.get("actualiser"), anchor="center", state="disabled",
        )
        self._backup_btn.pack(side="left", padx=(0, 6))
        self._diag_btn = tool_button(
            actions, text="Diagnostiquer", command=self.on_diagnose,
            image=self.app.icons.get("journal"), anchor="center", state="disabled",
        )
        self._diag_btn.pack(side="left", padx=(0, 6))
        self._uninstall_btn = tool_button(
            actions, text="Désinstaller", command=self.on_uninstall,
            image=self.app.icons.get("bin"), anchor="center", state="disabled",
        )
        self._uninstall_btn.pack(side="left", padx=(0, 6))

        self._populate_drives()

    # ------------------------------------------------------------------ arbre fichiers

    def _populate_drives(self):
        # Liste les disques presents (lettres A-Z existantes).
        for letter in string.ascii_uppercase:
            drive = f"{letter}:\\"
            if not os.path.exists(drive):
                continue
            item = self.tree.insert("", "end", text=f"☐ {drive}", open=False)
            self._item_path[item] = drive
            self._checked[item] = False
            # Enfant fictif pour afficher la fleche d'expansion (chargement paresseux).
            self.tree.insert(item, "end", text="…")

    def _on_tree_open(self, _event):
        item = self.tree.focus()
        if not item:
            return
        children = self.tree.get_children(item)
        # Si le seul enfant est le placeholder "…", on charge le contenu reel.
        if len(children) == 1 and self.tree.item(children[0], "text") == "…":
            self.tree.delete(children[0])
            self._populate_children(item)

    def _populate_children(self, parent_item):
        base = self._item_path.get(parent_item)
        if not base:
            return
        try:
            entries = sorted(os.scandir(base), key=lambda e: (not e.is_dir(), e.name.lower()))
        except Exception:
            return
        parent_checked = self._checked.get(parent_item, False)
        for entry in entries:
            try:
                is_dir = entry.is_dir()
            except OSError:
                continue
            mark = "☑" if parent_checked else "☐"
            item = self.tree.insert(parent_item, "end", text=f"{mark} {entry.name}", open=False)
            self._item_path[item] = entry.path
            self._checked[item] = parent_checked
            if is_dir:
                # placeholder pour l'expansion paresseuse
                try:
                    self.tree.insert(item, "end", text="…")
                except Exception:
                    pass

    def _on_tree_click(self, event):
        # Clic sur la fleche d'expansion -> comportement natif (ne pas cocher).
        element = self.tree.identify_element(event.x, event.y)
        if "indicator" in (element or ""):
            return
        item = self.tree.identify_row(event.y)
        if not item or item not in self._checked:
            return
        self._set_checked(item, not self._checked[item], recurse=True)
        self._update_sel_count()
        return "break"

    def _set_checked(self, item, value, recurse=False):
        self._checked[item] = value
        text = self.tree.item(item, "text")
        # Remplace le prefixe ☐/☑
        if len(text) >= 2 and text[0] in ("☐", "☑"):
            text = text[2:]
        self.tree.item(item, text=f"{'☑' if value else '☐'} {text}")
        if recurse:
            for child in self.tree.get_children(item):
                if self.tree.item(child, "text") == "…":
                    continue
                if child in self._checked:
                    self._set_checked(child, value, recurse=True)

    def _update_sel_count(self):
        n = sum(1 for v in self._checked.values() if v)
        self._sel_count_var.set(f"{n} élément(s) sélectionné(s)")

    def selected_paths(self):
        # Chemins coches. On retire les descendants dont un ancetre est aussi
        # coche (kiwi inclut recursivement un dossier), pour un include propre.
        checked = [self._item_path[i] for i, v in self._checked.items()
                   if v and i in self._item_path]
        checked_sorted = sorted(checked, key=len)
        result = []
        for path in checked_sorted:
            if any(path != p and path.lower().startswith(p.lower().rstrip("\\") + "\\")
                   for p in result):
                continue
            result.append(path)
        return result

    # ------------------------------------------------------------------ etat

    def refresh_status(self):
        self._status_var.set("Vérification...")
        self._status_badge.configure(fg=MUTED_TEXT)

        def task():
            return self.app.cerberit.detect_install()

        def on_success(info):
            self._install_info = info
            self._apply_status(info)

        def on_error(exc):
            self._status_var.set(f"Erreur de vérification : {exc}")
            self._status_badge.configure(fg=ERROR_COLOR)

        self.app.run_task("Vérification CerberIT", task,
                          on_success=on_success, on_error=on_error, key="cerberit_detect")

    def _apply_status(self, info):
        installed = info.get("installed")
        svc = info.get("service")
        registered = info.get("registered")
        if not installed:
            self._status_var.set("✕ CerberIT n'est pas installé sur ce poste")
            self._status_badge.configure(fg=ERROR_COLOR)
            self._backup_btn.configure(state="disabled")
            self._uninstall_btn.configure(state="disabled")
            self._diag_btn.configure(state="disabled")
            return
        svc_txt = {"running": "service actif", "stopped": "service arrêté"}.get(svc, "service absent")
        reg_txt = "enregistré" if registered else "NON enregistré"
        color = OK_COLOR if (registered and svc == "running") else WARN_COLOR
        self._status_var.set(f"✓ CerberIT installé — {svc_txt}, {reg_txt}")
        self._status_badge.configure(fg=color)
        self._backup_btn.configure(state="normal" if registered else "disabled")
        self._uninstall_btn.configure(state="normal")
        self._diag_btn.configure(state="normal")

    # ------------------------------------------------------------------ actions

    def on_install_configure(self):
        key = self._key_var.get().strip()
        name = self._name_var.get().strip()
        if not key:
            self.app.set_status("Renseignez la clé de contrat CerberIT.", ERROR_COLOR)
            return
        if not name:
            self.app.set_status("Renseignez le nom de la machine.", ERROR_COLOR)
            return
        if not is_admin():
            self.app.confirm_action(
                APP_TITLE,
                "L'installation CerberIT nécessite les droits administrateur.\n"
                "Relancez Vega Toolbox en administrateur.",
            )
            return

        includes = self.selected_paths()
        excludes = [p for p in self._exclude_var.get().split() if p]
        try:
            hour = int(self._hour_var.get()); minute = int(self._min_var.get())
        except ValueError:
            self.app.set_status("Heure de planning invalide.", ERROR_COLOR)
            return
        days = {k for k, var in self._day_vars.items() if var.get()}

        if not self.app.confirm_action(
            APP_TITLE,
            "Installer et configurer CerberIT ?\n\n"
            f"  • Machine : {name}\n"
            f"  • Serveur : {CERBERIT_SERVER}\n"
            f"  • Éléments à sauvegarder : {len(includes)}\n"
            f"  • Exclusions : {' '.join(excludes) or '(aucune)'}\n"
            f"  • Planning : {hour:02d}h{minute:02d}, {len(days)} jour(s)\n\n"
            "Le téléchargement + installation peut prendre quelques minutes.\nContinuer ?",
        ):
            return

        self._install_btn.configure(state="disabled")

        def on_progress(_step, message):
            self.after(0, lambda m=message: self.app.logger.info(m))

        def task():
            info = self.app.cerberit.detect_install()
            if not info.get("installed"):
                self.app.cerberit.install_silent(on_progress=on_progress)
            on_progress("register", "Enregistrement de la machine...")
            reg = self.app.cerberit.register(name, key, jobid=DEFAULT_JOBID, server=CERBERIT_SERVER)
            on_progress("config", "Application de la configuration de sauvegarde...")
            self.app.cerberit.apply_backup_config(
                jobid=DEFAULT_JOBID, include_paths=includes, exclude_patterns=excludes,
                hour=hour, minute=minute, days=days,
            )
            return reg

        def on_success(reg):
            self._install_btn.configure(state="normal")
            if reg.get("registered"):
                self.app.logger.info("CerberIT installé, enregistré et configuré.")
            else:
                self.app.logger.warn(
                    "CerberIT installé et configuré, mais l'enregistrement n'a pas été confirmé "
                    "(vérifiez la clé de contrat)."
                )
            self.refresh_status()
            self.app.audit.log_action(
                "cerberit_install_configure",
                status="success" if reg.get("registered") else "warning",
                details={"machine": name, "includes": includes, "excludes": excludes,
                         "hour": hour, "minute": minute, "days": sorted(days)},
            )

        def on_error(exc):
            self._install_btn.configure(state="normal")
            self.app.logger.error(f"CerberIT : échec — {exc}")
            self.app.audit.log_action("cerberit_install_configure", status="failure", error=str(exc))

        self.app.run_task("Installation CerberIT", task,
                          on_success=on_success, on_error=on_error, key="cerberit_install")

    def on_uninstall(self):
        if not is_admin():
            self.app.confirm_action(
                APP_TITLE,
                "La désinstallation CerberIT nécessite les droits administrateur.\n"
                "Relancez Vega Toolbox en administrateur.",
            )
            return
        if not self.app.confirm_action(
            APP_TITLE,
            "Désinstaller CerberIT de ce poste ?\n\n"
            "  • Le service et les binaires seront supprimés.\n"
            "  • La machine ne sera plus sauvegardée.\n\n"
            "Continuer ?",
        ):
            return
        # 2e question : supprimer aussi la config + journaux (kiwi.conf, logs).
        remove_data = self.app.confirm_action(
            APP_TITLE,
            "Supprimer aussi la configuration et les journaux "
            "(C:\\ProgramData\\Kiwi-Backup) ?\n\n"
            "  • Oui : désinstallation complète (aucune trace).\n"
            "  • Non : la configuration est conservée (réinstallation plus simple).",
        )

        self._uninstall_btn.configure(state="disabled")
        self.app.logger.info("=== DÉSINSTALLATION CERBERIT ===")

        def on_progress(_step, message):
            self.after(0, lambda m=message: self.app.logger.info(m))

        def task():
            return self.app.cerberit.uninstall(on_progress=on_progress, remove_data=remove_data)

        def on_success(res):
            if res.get("already_absent"):
                self.app.logger.info("CerberIT n'était pas installé.")
            else:
                methode = res.get("method") or "?"
                self.app.logger.info(f"CerberIT désinstallé avec succès (méthode : {methode}).")
                purge = res.get("manual_purge")
                if purge:
                    # Le desinstalleur etait absent ou inoperant : on detaille
                    # ce que la purge manuelle a reellement pu supprimer.
                    self.app.logger.info(
                        f"  Service supprimé : {purge.get('service_deleted')} · "
                        f"Fichiers supprimés : {purge.get('install_dir_deleted')} · "
                        f"Registre nettoyé : {purge.get('registry_deleted')}"
                    )
                if res.get("data_removed"):
                    self.app.logger.info("Configuration et journaux supprimés.")
            self.refresh_status()
            self.app.audit.log_action(
                "cerberit_uninstall", status="success",
                details={"data_removed": res.get("data_removed", False)},
            )

        def on_error(exc):
            self._uninstall_btn.configure(state="normal")
            self.app.logger.error(f"CerberIT : échec désinstallation — {exc}")
            self.app.audit.log_action("cerberit_uninstall", status="failure", error=str(exc))

        self.app.run_task("Désinstallation CerberIT", task,
                          on_success=on_success, on_error=on_error, key="cerberit_install")

    def on_backup_now(self):
        if not self.app.confirm_action(APP_TITLE, "Lancer une sauvegarde CerberIT maintenant ?"):
            return

        def task():
            return self.app.cerberit.run_backup_now(jobid=DEFAULT_JOBID)

        def on_success(res):
            rc = res.get("return_code")
            if rc == 0:
                self.app.logger.info("Sauvegarde CerberIT lancée avec succès (code 0).")
            else:
                self.app.logger.warn(f"Sauvegarde CerberIT en échec (code {rc}).")
                for line in (res.get("log_errors") or []):
                    self.app.logger.error(f"  {line}")
                if res.get("stderr"):
                    self.app.logger.error(f"  Sortie : {res['stderr']}")
                self.app.logger.info("Cliquez « Diagnostiquer » pour identifier la cause.")

        self.app.run_task("Sauvegarde CerberIT", task, on_success=on_success, key="cerberit_backup")

    def on_diagnose(self):
        # Lance la batterie de controles et affiche le resultat dans une popup +
        # le journal, pour comprendre pourquoi une sauvegarde ne se fait pas.
        def task():
            return self.app.cerberit.diagnose()

        def on_success(checks):
            self.app.logger.info("=== DIAGNOSTIC CERBERIT ===")
            for c in checks:
                icon = {"ok": "✓", "warn": "⚠", "fail": "✕"}.get(c["status"], "•")
                line = f"{icon} {c['label']}"
                if c.get("detail"):
                    line += f" — {c['detail']}"
                if c["status"] == "fail":
                    self.app.logger.error(line)
                elif c["status"] == "warn":
                    self.app.logger.warn(line)
                else:
                    self.app.logger.info(line)
            self._show_diag_popup(checks)

        self.app.run_task("Diagnostic CerberIT", task, on_success=on_success, key="cerberit_diag")

    def _show_diag_popup(self, checks):
        dialog = tk.Toplevel(self)
        try:
            from ..resources import set_window_icon
            set_window_icon(dialog)
        except Exception:
            pass
        dialog.title("Diagnostic CerberIT")
        dialog.configure(bg=WINDOW_BG)
        dialog.resizable(False, False)
        dialog.transient(self.winfo_toplevel())

        host = tk.Frame(dialog, bg=WINDOW_BG, padx=16, pady=12)
        host.pack(fill="both", expand=True)
        tk.Label(host, text="Résultat du diagnostic", bg=WINDOW_BG,
                 font=("Tahoma", 10, "bold"), fg=ACCENT_BLUE, anchor="w",
                 ).pack(anchor="w", pady=(0, 8))
        colors = {"ok": OK_COLOR, "warn": WARN_COLOR, "fail": ERROR_COLOR}
        icons = {"ok": "✓", "warn": "⚠", "fail": "✕"}
        for c in checks:
            row = tk.Frame(host, bg=WINDOW_BG)
            row.pack(fill="x", anchor="w", pady=1)
            tk.Label(row, text=icons.get(c["status"], "•"), bg=WINDOW_BG,
                     fg=colors.get(c["status"], MUTED_TEXT), font=("Tahoma", 10, "bold"),
                     width=2).pack(side="left")
            txt = c["label"]
            if c.get("detail"):
                txt += f" — {c['detail']}"
            tk.Label(row, text=txt, bg=WINDOW_BG, font=("Tahoma", 9),
                     justify="left", wraplength=520, anchor="w").pack(side="left")
        tool_button(host, text="Fermer", command=dialog.destroy, width=12,
                    anchor="center").pack(anchor="e", pady=(12, 0))

        dialog.update_idletasks()
        parent = self.winfo_toplevel()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        dw, dh = dialog.winfo_width(), dialog.winfo_height()
        dialog.geometry(f"+{px + max((pw - dw) // 2, 0)}+{py + max((ph - dh) // 2, 0)}")
        dialog.grab_set()
        dialog.focus_force()
