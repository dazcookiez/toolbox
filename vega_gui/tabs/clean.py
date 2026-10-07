import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from ..theme import ACCENT_BLUE, APP_TITLE, ERROR_COLOR, MUTED_TEXT, OK_COLOR, WARN_COLOR, WINDOW_BG
from ..widgets import info_panel, path_entry, tool_button, value_cell


class CleanTabV2(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=8)
        self.app = app
        self.root_var = tk.StringVar(value=str(Path.cwd()))
        self.plan_rows = {}
        self.latest_analysis = None
        self.state_var = tk.StringVar(value="À analyser")
        self.total_var = tk.StringVar(value="0")
        self.count_vars = {
            "dir_move": tk.StringVar(value="0"),
            "old_exe": tk.StringVar(value="0"),
            "old_dll": tk.StringVar(value="0"),
            "pdf": tk.StringVar(value="0"),
            "docs": tk.StringVar(value="0"),
            "archives": tk.StringVar(value="0"),
            "copies": tk.StringVar(value="0"),
            "images": tk.StringVar(value="0"),
            "fdj": tk.StringVar(value="0"),
            "delete": tk.StringVar(value="0"),
        }
        self.build_ui()
        self.root_var.trace_add("write", self.on_root_changed)

    def build_ui(self):
        # L'analyse présente uniquement les actions prévues à la racine Vega.
        self.columnconfigure(0, weight=3)
        self.columnconfigure(1, weight=2)
        self.rowconfigure(2, weight=1)

        target_frame = tk.LabelFrame(self, text="Dossier Vega cible", padx=8, pady=6, bg=WINDOW_BG)
        target_frame.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self.target_frame = target_frame
        target_frame.columnconfigure(1, weight=1)

        tk.Label(target_frame, text="Racine Vega", bg=WINDOW_BG).grid(row=0, column=0, sticky="w")
        path_entry(target_frame, textvariable=self.root_var).grid(
            row=0,
            column=1,
            sticky="ew",
            padx=8,
        )
        # Bouton "Bases" : menu deroulant des bases Vega detectees sur le poste
        # (comme dans Migration). Choisir une base met a jour root_var.
        self.bases_button = tool_button(
            target_frame,
            text="Bases ▾",
            command=self.show_bases_menu,
            image=self.app.icons.get("database"),
            width=10,
        )
        self.bases_button.grid(row=0, column=2, padx=(0, 6))
        tool_button(
            target_frame,
            text="Parcourir...",
            command=self.browse_root,
            image=self.app.icons.get("parcourir"),
            width=13,
        ).grid(row=0, column=3, padx=(0, 6))
        tool_button(
            target_frame,
            text="Analyser",
            command=self.refresh_analysis,
            image=self.app.icons.get("verifier"),
            width=11,
        ).grid(row=0, column=4)

        left_panel = tk.Frame(self, bg=WINDOW_BG)
        left_panel.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        left_panel.columnconfigure(0, weight=1)

        action_frame = tk.LabelFrame(left_panel, text="Actions nettoyage", padx=8, pady=6, bg=WINDOW_BG)
        action_frame.grid(row=0, column=0, sticky="ew")
        action_frame.columnconfigure(0, weight=1)

        self.run_button = tool_button(
            action_frame,
            text="Lancer le nettoyage",
            command=self.run_cleanup,
            image=self.app.icons.get("reparer"),
            state="disabled",
            width=24,
        )
        self.run_button.grid(row=0, column=0, sticky="ew")

        info_panel(
            action_frame,
            "Le nettoyage agit uniquement sur la racine Vega. "
            "Les éléments détectés sont rangés dans vega.dos, sans parcourir les sous-dossiers métier.",
            wraplength=420,
        ).grid(row=1, column=0, sticky="ew", pady=(6, 0))

        summary_frame = tk.LabelFrame(self, text="Résumé de l'analyse", padx=8, pady=6, bg=WINDOW_BG)
        summary_frame.grid(row=1, column=1, sticky="nsew")
        summary_frame.columnconfigure(1, weight=1)

        summary_rows = [
            ("État", self.state_var),
            ("Total d'actions", self.total_var),
            ("Dossiers", self.count_vars["dir_move"]),
            ("Anciens exe", self.count_vars["old_exe"]),
            ("Anciennes dll", self.count_vars["old_dll"]),
            ("PDF", self.count_vars["pdf"]),
            ("Docs", self.count_vars["docs"]),
            ("Archives", self.count_vars["archives"]),
            ("Copies", self.count_vars["copies"]),
            ("Images", self.count_vars["images"]),
            ("Résultats FDJ", self.count_vars["fdj"]),
            ("Suppressions", self.count_vars["delete"]),
        ]
        for row_index, (label, var) in enumerate(summary_rows):
            tk.Label(
                summary_frame,
                text=label,
                bg=WINDOW_BG,
                font=("Tahoma", 8, "bold") if row_index < 2 else ("Tahoma", 9),
            ).grid(row=row_index, column=0, sticky="w", pady=(0, 6))
            value = value_cell(
                summary_frame,
                textvariable=var,
                width=18,
                anchor="w" if row_index == 0 else "center",
                font=("Tahoma", 10, "bold") if row_index == 0 else ("Tahoma", 9),
            )
            value.grid(row=row_index, column=1, sticky="ew", padx=(12, 0), pady=(0, 6))
            if row_index == 0:
                self.state_label = value

        plan_frame = tk.LabelFrame(self, text="Éléments détectés (cochez ce que vous voulez traiter)", padx=8, pady=6, bg=WINDOW_BG)
        plan_frame.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(6, 0))
        plan_frame.rowconfigure(1, weight=1)
        plan_frame.columnconfigure(0, weight=1)

        select_bar = tk.Frame(plan_frame, bg=WINDOW_BG)
        select_bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        tool_button(select_bar, text="Tout cocher", command=self.check_all, width=13).pack(side="left")
        tool_button(select_bar, text="Tout décocher", command=self.uncheck_all, width=14).pack(side="left", padx=(6, 0))
        self.selection_var = tk.StringVar(value="0 / 0 sélectionné(s)")
        tk.Label(select_bar, textvariable=self.selection_var, bg=WINDOW_BG, fg=MUTED_TEXT).pack(side="right")

        columns = ("check", "category", "source", "destination")
        self.tree = ttk.Treeview(plan_frame, columns=columns, show="headings", height=11)
        self.tree.heading("check", text="✓")
        self.tree.heading("category", text="Type")
        self.tree.heading("source", text="Élément")
        self.tree.heading("destination", text="Destination")
        self.tree.column("check", width=36, anchor="center", stretch=False)
        self.tree.column("category", width=160, anchor="w")
        self.tree.column("source", width=320, anchor="w")
        self.tree.column("destination", width=280, anchor="w")
        self.tree.grid(row=1, column=0, sticky="nsew")
        self.tree.bind("<Button-1>", self.on_tree_click)
        self.tree.bind("<space>", self.on_tree_space)

        scrollbar = ttk.Scrollbar(plan_frame, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=1, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)

    def set_root(self, value):
        if value:
            self.root_var.set(value)

    def browse_root(self):
        initial_dir = self.root_var.get().strip() or str(Path.cwd())
        selected = filedialog.askdirectory(title="Sélectionnez la racine Vega", initialdir=initial_dir)
        if selected:
            self.root_var.set(selected)

    def show_bases_menu(self):
        # Scan les disques locaux pour les VEGAHF/VEGACS et liste toutes les
        # bases valides en menu deroulant. Click = root_var.set (declenche
        # on_root_changed -> reset analyse). Identique a l'onglet Migration.
        try:
            roots = self.app.migration.detect_vega_roots() or []
        except Exception as exc:
            self.app.set_status(f"Détection bases impossible : {exc}", ERROR_COLOR)
            return

        entries = []
        current = self.root_var.get().strip()
        for r in roots:
            root_path = str(r.get("root"))
            on_network = bool(r.get("on_network"))
            for base in (r.get("bases") or []):
                base_path = str(base["path"])
                label = f"{base['name']} ({root_path})"
                if on_network:
                    label += " [réseau]"
                entries.append({
                    "label": label,
                    "path": base_path,
                    "on_network": on_network,
                    "is_current": (base_path == current),
                })

        if not entries:
            self.app.set_status("Aucune base Vega détectée sur ce poste.", WARN_COLOR)
            return

        menu = tk.Menu(self, tearoff=0)
        for entry in entries:
            label = ("✓ " + entry["label"]) if entry["is_current"] else entry["label"]
            state = "disabled" if entry["on_network"] else "normal"
            menu.add_command(
                label=label,
                command=lambda p=entry["path"]: self.root_var.set(p),
                state=state,
            )
        try:
            x = self.bases_button.winfo_rootx()
            y = self.bases_button.winfo_rooty() + self.bases_button.winfo_height()
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def on_root_changed(self, *_args):
        self.latest_analysis = None
        self.run_button.configure(state="disabled")
        self.state_var.set("À analyser")
        self.state_label.configure(fg=ACCENT_BLUE)
        self.total_var.set("0")
        for var in self.count_vars.values():
            var.set("0")
        self.tree.delete(*self.tree.get_children())
        self.plan_rows.clear()
        self.selection_var.set("0 / 0 sélectionné(s)")

    def current_root(self):
        value = self.root_var.get().strip()
        if not value:
            self.app.set_status("Sélectionnez un dossier Vega.", ERROR_COLOR)
            return None
        return Path(value)

    def refresh_analysis(self):
        root = self.current_root()
        if not root:
            return

        self.app.run_task(
            "Analyse Clean Vega",
            lambda: self.app.cleaner.analyze(root),
            on_success=self.apply_analysis,
            clear_display=False,
        )

    def apply_analysis(self, analysis):
        self.latest_analysis = analysis
        counts = analysis.get("counts", {})
        total = int(analysis.get("total_actions", 0))

        self.total_var.set(str(total))
        for key, var in self.count_vars.items():
            var.set(str(int(counts.get(key, 0))))

        self.tree.delete(*self.tree.get_children())
        self.plan_rows.clear()

        actions = analysis.get("actions", [])
        if not actions:
            row_id = self.tree.insert("", "end", values=("", "Aucune action", "La racine est déjà propre", "-"))
            self.plan_rows[row_id] = None
            self.state_var.set("Aucun nettoyage nécessaire")
            self.state_label.configure(fg=OK_COLOR)
            self.run_button.configure(state="disabled")
            self.selection_var.set("0 / 0 sélectionné(s)")
            self.app.set_status("Analyse Clean Vega terminée : aucun nettoyage nécessaire.", OK_COLOR)
            return

        for action in actions:
            destination = action.get("destination_label", "Suppression")
            row_id = self.tree.insert(
                "",
                "end",
                values=("[X]", action["label"], action["source_name"], destination),
            )
            self.plan_rows[row_id] = {"action": action, "checked": True}

        self.state_var.set("Prêt à nettoyer")
        self.state_label.configure(fg=WARN_COLOR)
        self.run_button.configure(state="normal")
        self.update_selection_count()
        self.app.set_status(f"Analyse Clean Vega terminée : {total} action(s) détectée(s).", WARN_COLOR)

    def update_selection_count(self):
        rows = [entry for entry in self.plan_rows.values() if entry]
        total = len(rows)
        checked = sum(1 for entry in rows if entry.get("checked"))
        self.selection_var.set(f"{checked} / {total} sélectionné(s)")
        if total > 0 and checked == 0:
            self.run_button.configure(state="disabled")
        elif total > 0:
            self.run_button.configure(state="normal")

    def set_row_checked(self, row_id, checked):
        entry = self.plan_rows.get(row_id)
        if not entry:
            return
        entry["checked"] = bool(checked)
        values = list(self.tree.item(row_id, "values"))
        if values:
            values[0] = "[X]" if checked else "[ ]"
            self.tree.item(row_id, values=values)

    def toggle_row(self, row_id):
        entry = self.plan_rows.get(row_id)
        if not entry:
            return
        self.set_row_checked(row_id, not entry.get("checked"))
        self.update_selection_count()

    def on_tree_click(self, event):
        row_id = self.tree.identify_row(event.y)
        if not row_id:
            return
        column = self.tree.identify_column(event.x)
        # Clic sur la premiere colonne = toggle. Ailleurs = selection seulement.
        if column == "#1":
            self.toggle_row(row_id)

    def on_tree_space(self, _event):
        for row_id in self.tree.selection():
            self.toggle_row(row_id)
        return "break"

    def check_all(self):
        for row_id, entry in self.plan_rows.items():
            if entry:
                self.set_row_checked(row_id, True)
        self.update_selection_count()

    def uncheck_all(self):
        for row_id, entry in self.plan_rows.items():
            if entry:
                self.set_row_checked(row_id, False)
        self.update_selection_count()

    def selected_actions(self):
        return [entry["action"] for entry in self.plan_rows.values() if entry and entry.get("checked")]

    def run_cleanup(self):
        root = self.current_root()
        if not root:
            return
        if not self.latest_analysis:
            self.app.set_status("Lancez d'abord une analyse du dossier Vega.", ERROR_COLOR)
            return

        actions = self.selected_actions()
        if not actions:
            self.app.set_status("Cochez au moins un élément à nettoyer.", WARN_COLOR)
            return

        if not self.app.confirm_action(
            APP_TITLE,
            f"Le nettoyage va déplacer ou supprimer {len(actions)} élément(s) sélectionné(s) à la racine Vega.\n\nContinuer ?",
        ):
            return

        self.app.run_task(
            "Nettoyage Vega",
            lambda: self.app.cleaner.execute(root, actions),
            on_success=self.after_cleanup,
            clear_display=True,
        )

    def after_cleanup(self, result):
        self.apply_analysis(self.app.cleaner.analyze(Path(result["root"])))
        log_name = Path(result["log_path"]).name
        self.app.set_status(f"Nettoyage Vega terminé. Journal : {log_name}", OK_COLOR)

        # Detail des actions pour l'audit : on liste chaque fichier deplace
        # ou supprime avec sa categorie + destination. Format lisible plus
        # tard dans le HTML d'audit via le bloc 'Voir tout'.
        actions = result.get("actions") or []
        counts = result.get("counts") or {}
        action_details = []
        for a in actions:
            kind = a.get("kind", "?")
            src = a.get("source") or a.get("path") or ""
            dst = a.get("destination") or ""
            label = a.get("label") or a.get("category") or kind
            if kind == "delete":
                action_details.append(f"[Supprime] {label} : {src}")
            elif dst:
                action_details.append(f"[{label}] {src} -> {dst}")
            else:
                action_details.append(f"[{label}] {src}")

        self.app.audit.log_action(
            "vega_cleanup",
            status="success",
            details={
                "root": str(result.get("root")),
                "log_path": str(result.get("log_path")),
                "total_actions": result.get("total_actions") or len(actions),
                "par_categorie": counts,
                "actions_detaillees": action_details,
            },
        )
