"""Onglet Nomserve : lecture et creation du nomserve.txt V6 de la base Vega.

La base cible est celle de l'Accueil / Migration (StringVar partagee). Le
formulaire est pre-rempli : BASE = nom du dossier de la base, IP serveur
deduite de l'ancien nomserve V5 (vega.dos\\V6\\nomserve-v5), du chemin reseau
ou de l'IP de ce poste. L'apercu montre le fichier exact qui sera ecrit.
"""
import os
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from vega_backend.nomserve import (
    DEFAULT_PORT,
    default_print_share,
    merge_nomserve,
    split_server,
)

from ..theme import ACCENT_BLUE, APP_TITLE, ERROR_COLOR, MUTED_TEXT, OK_COLOR, WARN_COLOR, WHITE_BG, WINDOW_BG
from ..widgets import tool_button, value_cell

_MONO = ("Courier New", 9)


class NomserveTab(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=WINDOW_BG, padx=8, pady=8)
        self.app = app
        self.base_var = tk.StringVar(value="")
        self.file_var = tk.StringVar(value="")
        self.file_status_var = tk.StringVar(value="Non lu")
        self.source_var = tk.StringVar(value="")

        self.host_var = tk.StringVar()
        self.port_var = tk.StringVar(value=DEFAULT_PORT)
        self.db_var = tk.StringVar()
        self.user_var = tk.StringVar()
        self.pwd_var = tk.StringVar()
        self.rep_var = tk.StringVar()
        self.rep_follow_var = tk.BooleanVar(value=True)

        self._state = None
        self._loaded_for = None
        self._shared_root_var = None

        self.build_ui()
        for var in (self.port_var, self.db_var, self.user_var, self.pwd_var, self.rep_var):
            var.trace_add("write", lambda *_: self._update_preview())
        self.host_var.trace_add("write", lambda *_: self._on_host_changed())
        self.rep_follow_var.trace_add("write", lambda *_: self._on_host_changed())
        self._bind_shared_root()

    # ---------- UI ----------

    def build_ui(self):
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)

        target = tk.LabelFrame(self, text="Base Vega ciblée", padx=8, pady=6, bg=WINDOW_BG)
        target.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        target.columnconfigure(1, weight=1)
        self.target_frame = target

        tk.Label(target, text="Base", bg=WINDOW_BG).grid(row=0, column=0, sticky="w")
        value_cell(target, textvariable=self.base_var, anchor="w", width=10).grid(
            row=0, column=1, sticky="ew", padx=(8, 0)
        )
        tk.Label(target, text="Fichier", bg=WINDOW_BG).grid(row=1, column=0, sticky="w", pady=(4, 0))
        value_cell(target, textvariable=self.file_var, anchor="w", width=10).grid(
            row=1, column=1, sticky="ew", padx=(8, 0), pady=(4, 0)
        )
        self.file_status_label = tk.Label(
            target, textvariable=self.file_status_var, bg=WINDOW_BG, fg=MUTED_TEXT,
            font=("Tahoma", 8, "bold"), anchor="w",
        )
        self.file_status_label.grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(2, 0))

        actions = tk.Frame(target, bg=WINDOW_BG)
        actions.grid(row=0, column=2, rowspan=3, sticky="ne", padx=(10, 0))
        tool_button(actions, text="Relire", command=self.refresh,
                    image=self.app.icons.get("actualiser")).pack(fill="x")
        tool_button(actions, text="Ouvrir le dossier", command=self.open_folder,
                    image=self.app.icons.get("parcourir")).pack(fill="x", pady=(4, 0))
        tool_button(actions, text="Changer de base...", command=self.change_base,
                    image=self.app.icons.get("database")).pack(fill="x", pady=(4, 0))

        # --- Formulaire V6 ---
        form = tk.LabelFrame(self, text="Configuration V6", padx=8, pady=6, bg=WINDOW_BG)
        form.grid(row=1, column=0, sticky="nsew", pady=(0, 6))
        form.columnconfigure(1, weight=1)
        self.form_frame = form

        row = 0
        tk.Label(form, text="[BDD HFCS]", bg=WINDOW_BG, fg=ACCENT_BLUE,
                 font=("Tahoma", 9, "bold")).grid(row=row, column=0, columnspan=4, sticky="w")
        row += 1
        tk.Label(form, text="IP du serveur", bg=WINDOW_BG).grid(row=row, column=0, sticky="w", pady=2)
        self.host_combo = ttk.Combobox(form, textvariable=self.host_var, width=18)
        self.host_combo.grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=2)
        tk.Label(form, text="Port", bg=WINDOW_BG).grid(row=row, column=2, sticky="w", padx=(8, 0))
        tk.Entry(form, textvariable=self.port_var, width=7, bd=2, relief="sunken").grid(
            row=row, column=3, sticky="w", padx=(4, 0)
        )
        row += 1
        tk.Label(form, textvariable=self.source_var, bg=WINDOW_BG, fg=MUTED_TEXT,
                 font=("Tahoma", 8), anchor="w").grid(row=row, column=1, columnspan=3, sticky="w", padx=(8, 0))
        row += 1
        for label, var in (("BASE", self.db_var), ("UTILISATEUR", self.user_var), ("MDP", self.pwd_var)):
            tk.Label(form, text=label, bg=WINDOW_BG).grid(row=row, column=0, sticky="w", pady=2)
            tk.Entry(form, textvariable=var, bd=2, relief="sunken").grid(
                row=row, column=1, columnspan=3, sticky="ew", padx=(8, 0), pady=2
            )
            row += 1

        tk.Label(form, text="[REPIMPRESSION]", bg=WINDOW_BG, fg=ACCENT_BLUE,
                 font=("Tahoma", 9, "bold")).grid(row=row, column=0, columnspan=4, sticky="w", pady=(8, 0))
        row += 1
        tk.Label(form, text="rep", bg=WINDOW_BG).grid(row=row, column=0, sticky="w", pady=2)
        self.rep_entry = tk.Entry(form, textvariable=self.rep_var, bd=2, relief="sunken")
        self.rep_entry.grid(row=row, column=1, columnspan=3, sticky="ew", padx=(8, 0), pady=2)
        row += 1
        tk.Checkbutton(
            form, text="Suivre l'IP du serveur (\\\\IP\\ImpressionsVega$)",
            variable=self.rep_follow_var, bg=WINDOW_BG, activebackground=WINDOW_BG,
        ).grid(row=row, column=1, columnspan=3, sticky="w", padx=(4, 0))
        row += 1

        buttons = tk.Frame(form, bg=WINDOW_BG)
        buttons.grid(row=row, column=0, columnspan=4, sticky="ew", pady=(10, 0))
        self.save_button = tool_button(
            buttons, text="Enregistrer nomserve.txt", command=self.save,
            image=self.app.icons.get("lancer"), state="disabled",
        )
        self.save_button.pack(side="right")
        tool_button(buttons, text="Valeurs proposées", command=self.reset_to_suggested,
                    image=self.app.icons.get("retour")).pack(side="right", padx=(0, 6))

        # --- Apercu ---
        preview = tk.LabelFrame(self, text="Aperçu du fichier qui sera écrit", padx=8, pady=6, bg=WINDOW_BG)
        preview.grid(row=1, column=1, sticky="nsew", padx=(8, 0), pady=(0, 6))
        preview.columnconfigure(0, weight=1)
        preview.rowconfigure(0, weight=1)
        self.preview_text = self._readonly_text(preview, height=12)
        self.preview_text.grid(row=0, column=0, sticky="nsew")

        # --- Fichiers existants ---
        current = tk.LabelFrame(self, text="nomserve.txt actuel", padx=8, pady=6, bg=WINDOW_BG)
        current.grid(row=2, column=0, sticky="nsew")
        current.columnconfigure(0, weight=1)
        self.current_text = self._readonly_text(current, height=9)
        self.current_text.grid(row=0, column=0, sticky="nsew")

        v5 = tk.LabelFrame(self, text="Ancien nomserve V5 (vega.dos\\V6\\nomserve-v5)", padx=8, pady=6, bg=WINDOW_BG)
        v5.grid(row=2, column=1, sticky="nsew", padx=(8, 0))
        v5.columnconfigure(0, weight=1)
        self.v5_text = self._readonly_text(v5, height=9)
        self.v5_text.grid(row=0, column=0, sticky="nsew")

    def _readonly_text(self, parent, height):
        text = tk.Text(parent, height=height, width=40, bg=WHITE_BG, font=_MONO,
                       relief="sunken", bd=1, wrap="none")
        text.configure(state="disabled")
        return text

    @staticmethod
    def _set_text(widget, content):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", content)
        widget.configure(state="disabled")

    # ---------- Base partagee ----------

    def _bind_shared_root(self):
        # Suit la base de l'Accueil / Migration. Rechargement a l'affichage de
        # l'onglet (ensure_loaded) ou immediatement s'il est visible.
        modules = getattr(self.app, "modules", None) or {}
        migration = modules.get("migration")
        if migration is None:
            return
        try:
            var = migration.root_var
        except Exception:
            return
        self._shared_root_var = var
        self.base_var.set(var.get().strip())
        var.trace_add("write", lambda *_: self._on_shared_root_changed())

    def _on_shared_root_changed(self):
        self.base_var.set(self._shared_root_var.get().strip())
        if getattr(self.app, "current_module_key", None) == "nomserve":
            self.refresh()

    def ensure_loaded(self):
        if self._loaded_for != self.base_var.get().strip():
            self.refresh()

    def change_base(self):
        home = (getattr(self.app, "modules", None) or {}).get("home")
        if home is not None:
            home.choose_root_manually()

    # ---------- Lecture ----------

    def refresh(self):
        base = self.base_var.get().strip()
        self._loaded_for = base
        self.save_button.configure(state="disabled")
        if not base or not Path(base).is_dir():
            self._state = None
            self.file_var.set("")
            self._set_file_status("Base Vega introuvable : choisissez-la sur l'Accueil.", ERROR_COLOR)
            self._set_text(self.current_text, "")
            self._set_text(self.v5_text, "")
            self._set_text(self.preview_text, "")
            return
        self._set_file_status("Lecture...", MUTED_TEXT)
        self.app.run_task(
            "Lecture nomserve",
            lambda: self.app.nomserve.read(base),
            on_success=self._apply_state,
            on_error=lambda exc: self._set_file_status(f"Lecture impossible : {exc}", ERROR_COLOR),
            key="nomserve_read",
        )

    def _apply_state(self, state):
        self._state = state
        self.file_var.set(state["path"])
        if not state["exists"]:
            self._set_file_status("Absent : il sera créé à l'enregistrement.", WARN_COLOR)
            self._set_text(self.current_text, "(fichier absent)")
        elif state["is_v6"]:
            self._set_file_status("Présent, format V6.", OK_COLOR)
            self._set_text(self.current_text, state["text"])
        else:
            self._set_file_status("Présent, mais pas au format V6 ([BDD HFCS] / SERVEUR absent).", WARN_COLOR)
            self._set_text(self.current_text, state["text"])
        self._set_text(self.v5_text, state["v5_text"] if state.get("v5_path") else "(aucune copie V5)")
        self.host_combo.configure(values=state.get("local_ips") or [])
        self.reset_to_suggested()
        self.save_button.configure(state="normal")

    def _set_file_status(self, text, color):
        self.file_status_var.set(text)
        self.file_status_label.configure(fg=color)

    # ---------- Formulaire ----------

    def reset_to_suggested(self):
        if not self._state:
            return
        sug = self._state["suggested"]
        self.port_var.set(sug["port"])
        self.db_var.set(sug["base"])
        self.user_var.set(sug["utilisateur"])
        self.pwd_var.set(sug["mdp"])
        # rep "suit l'IP" sauf si le fichier actuel contient un autre chemin.
        follow = sug["rep"] == default_print_share(sug["host"])
        self.rep_follow_var.set(follow)
        self.rep_var.set(sug["rep"])
        self.host_var.set(sug["host"])
        self.source_var.set(f"Proposée depuis : {sug['host_source']}" if sug["host_source"] else
                            "Aucune IP trouvée : saisissez celle du serveur.")
        self._update_preview()

    def _on_host_changed(self):
        follow = self.rep_follow_var.get()
        self.rep_entry.configure(state="disabled" if follow else "normal")
        if follow:
            self.rep_var.set(default_print_share(self.host_var.get()))
        self._update_preview()

    def current_values(self):
        host = self.host_var.get().strip()
        # Accepte "IP:port" colle directement dans le champ IP.
        pasted_host, pasted_port = split_server(host)
        if pasted_port:
            host = pasted_host
        port = (pasted_port or self.port_var.get().strip())
        serveur = f"{host}:{port}" if (host and port) else host
        return {
            "serveur": serveur,
            "base": self.db_var.get().strip(),
            "utilisateur": self.user_var.get().strip(),
            "mdp": self.pwd_var.get(),
            "rep": self.rep_var.get().strip(),
        }

    def _update_preview(self):
        if not self._state:
            return
        existing = self._state["text"] if self._state["exists"] else ""
        self._set_text(self.preview_text, merge_nomserve(existing, self.current_values()).replace("\r\n", "\n"))

    # ---------- Ecriture ----------

    def save(self):
        if not self._state:
            return
        values = self.current_values()
        missing = [name for name, key in (("IP du serveur", "serveur"), ("BASE", "base"),
                                          ("UTILISATEUR", "utilisateur")) if not values[key]]
        if missing:
            self.app.set_status("Champs obligatoires manquants : " + ", ".join(missing), ERROR_COLOR)
            return
        base = self._state["base"]
        action = "remplacer les valeurs de" if self._state["exists"] else "créer"
        backup_note = (
            "\n\nL'ancien fichier est sauvegardé dans .vega_tool_state\\nomserve."
            if self._state["exists"] else ""
        )
        if not self.app.confirm_action(
            APP_TITLE,
            f"Voulez-vous {action} :\n{self._state['path']}\n\n"
            f"SERVEUR={values['serveur']}\nBASE={values['base']}\n"
            f"UTILISATEUR={values['utilisateur']}\nrep={values['rep']}{backup_note}",
        ):
            return

        def on_success(result):
            msg = "nomserve.txt enregistré."
            if result.get("backup"):
                msg += " Ancien fichier sauvegardé."
            self.app.set_status(msg, OK_COLOR)
            try:
                self.app.audit.log_action(
                    "nomserve_write", status="success",
                    details={"path": result["path"], "serveur": values["serveur"], "base": values["base"]},
                )
            except Exception:
                pass
            self.refresh()

        self.app.run_task(
            "Écriture nomserve",
            lambda: self.app.nomserve.write(base, values),
            on_success=on_success,
            key="nomserve_write",
        )

    def open_folder(self):
        base = self.base_var.get().strip()
        if base and Path(base).is_dir():
            try:
                os.startfile(base)
            except Exception as exc:
                self.app.set_status(f"Ouverture impossible : {exc}", ERROR_COLOR)
