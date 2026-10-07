import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from vega_security import load_last_vega_root, save_last_vega_root

from ..theme import APP_TITLE, ERROR_COLOR, OK_COLOR, WARN_COLOR, WINDOW_BG
from ..widgets import info_panel, path_entry, tool_button, value_cell


class MigrationTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=8)
        self.app = app
        initial_root = load_last_vega_root() or str(Path.cwd())
        self.root_var = tk.StringVar(value=initial_root)
        self.json_var = tk.BooleanVar(value=False)
        self.hfsql_var = tk.BooleanVar(value=False)
        self.structure_var = tk.StringVar(value="À vérifier")
        self.firewall_var = tk.StringVar(value="À vérifier")
        self.firewall_ok = False
        self.verified_ready = False
        self.backup_rows = {}
        self._auto_verify_job = None
        self.build_ui()
        self.root_var.trace_add("write", self.on_root_changed)
        self.refresh_backups()

    def build_ui(self):
        self.columnconfigure(0, weight=3)
        self.columnconfigure(1, weight=2)
        self.rowconfigure(2, weight=1, minsize=220)

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
        # Bouton menu deroulant : liste les bases Vega detectees sur tous
        # les disques locaux (scan via migration.detect_vega_roots). Click
        # selectionne la base, root_var.set declenche on_root_changed -> refresh
        # backups + verif automatique.
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
            text="Vérifier",
            command=self.refresh_status,
            image=self.app.icons.get("verifier"),
            width=11,
        ).grid(row=0, column=4)

        left_panel = tk.Frame(self, bg=WINDOW_BG)
        left_panel.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        left_panel.columnconfigure(0, weight=1)

        status_frame = tk.LabelFrame(left_panel, text="État des vérifications", padx=8, pady=6, bg=WINDOW_BG)
        status_frame.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        status_frame.columnconfigure(1, weight=1)
        tk.Label(status_frame, text="Structure vega.dos\\V6", bg=WINDOW_BG).grid(row=0, column=0, sticky="w")
        self.structure_label = value_cell(status_frame, textvariable=self.structure_var, width=34, anchor="w")
        self.structure_label.grid(row=0, column=1, sticky="ew", padx=(12, 0))
        tk.Label(status_frame, text="Pare-feu TCP 7678", bg=WINDOW_BG).grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.firewall_label = value_cell(status_frame, textvariable=self.firewall_var, width=34, anchor="w")
        self.firewall_label.grid(row=1, column=1, sticky="ew", padx=(12, 0), pady=(8, 0))

        prereq_frame = tk.LabelFrame(left_panel, text="Prérequis utilisateur", padx=8, pady=6, bg=WINDOW_BG)
        prereq_frame.grid(row=1, column=0, sticky="ew")
        prereq_frame.columnconfigure(0, weight=1)

        json_row = tk.Frame(prereq_frame, bg=WINDOW_BG)
        json_row.grid(row=0, column=0, sticky="ew")
        tk.Checkbutton(
            json_row,
            text="Le fichier JSON de Retail Force a été réinitialisé",
            variable=self.json_var,
            bg=WINDOW_BG,
            activebackground=WINDOW_BG,
        ).pack(side="left")
        tool_button(
            json_row,
            text="Réinitialiser",
            command=self.reset_json,
            width=14,
            anchor="center",
        ).pack(side="right")

        hfsql_row = tk.Frame(prereq_frame, bg=WINDOW_BG)
        hfsql_row.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        tk.Checkbutton(
            hfsql_row,
            text="Le service HFSQL est arrêté",
            variable=self.hfsql_var,
            bg=WINDOW_BG,
            activebackground=WINDOW_BG,
        ).pack(side="left")
        # Le bouton est dynamique : "Arreter" / "Relancer" selon l'etat reel du service.
        # Texte initial = "Arreter" (sera ajuste par _refresh_hfsql_button au premier check).
        self.hfsql_button = tool_button(
            hfsql_row,
            text="Arrêter",
            command=self.toggle_hfsql,
            width=14,
            anchor="center",
        )
        self.hfsql_button.pack(side="right")
        # Etat HFSQL connu : "running" | "stopped" | "missing" | None (inconnu).
        # Pas de lecture automatique : etat ajuste apres une action ou via la propagation depuis l'accueil.
        self._hfsql_state = None

        right_panel = tk.Frame(self, bg=WINDOW_BG)
        right_panel.grid(row=1, column=1, sticky="nsew")
        right_panel.columnconfigure(0, weight=1)
        right_panel.rowconfigure(1, weight=1)

        action_frame = tk.LabelFrame(right_panel, text="Actions migration", padx=8, pady=6, bg=WINDOW_BG)
        action_frame.grid(row=0, column=0, sticky="ew")
        action_frame.columnconfigure(0, weight=1)

        self.run_button = tool_button(
            action_frame,
            text="Lancer la migration",
            command=self.run_migration,
            image=self.app.icons.get("lancer"),
            state="disabled",
            width=28,
        )
        self.run_button.grid(row=0, column=0, sticky="ew", pady=(0, 8))

        tool_button(
            action_frame,
            text="Actualiser les sauvegardes",
            command=self.refresh_backups,
            image=self.app.icons.get("actualiser"),
            width=28,
        ).grid(row=1, column=0, sticky="ew", pady=(0, 8))

        tool_button(
            action_frame,
            text="Retour arrière sélectionné",
            command=self.rollback,
            image=self.app.icons.get("retour"),
            width=28,
        ).grid(row=2, column=0, sticky="ew")

        info_panel(
            action_frame,
            "Le port TCP 7678 est fortement recommandé. La migration peut continuer après confirmation si le port n'est pas ouvert.",
            wraplength=280,
        ).grid(row=3, column=0, sticky="ew", pady=(12, 0))

        backup_frame = tk.LabelFrame(self, text="Sauvegardes disponibles", padx=8, pady=6, height=240, bg=WINDOW_BG)
        backup_frame.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(6, 0))
        backup_frame.grid_propagate(False)
        backup_frame.rowconfigure(0, weight=1)
        backup_frame.columnconfigure(0, weight=1)

        columns = ("label", "date", "status", "actions")
        self.tree = ttk.Treeview(backup_frame, columns=columns, show="headings", height=11)
        self.tree.heading("label", text="Migration")
        self.tree.heading("date", text="Effectuée le")
        self.tree.heading("status", text="Statut")
        self.tree.heading("actions", text="Actions")
        self.tree.column("label", width=260, anchor="w")
        self.tree.column("date", width=170, anchor="w")
        self.tree.column("status", width=160, anchor="center")
        self.tree.column("actions", width=70, anchor="center")
        self.tree.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(backup_frame, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)

    def browse_root(self):
        selected = filedialog.askdirectory(title="Sélectionnez la racine Vega")
        if selected:
            # Le trace sur root_var declenche refresh_backups + auto-verif debouncee.
            self.root_var.set(selected)
            self.refresh_backups()

    def show_bases_menu(self):
        # Scan les disques locaux pour les VEGAHF/VEGACS et liste toutes les
        # bases valides en menu deroulant. Click = root_var.set qui declenche
        # le pipeline standard (on_root_changed + refresh + verif).
        from ..theme import WARN_COLOR
        try:
            roots = self.app.migration.detect_vega_roots() or []
        except Exception as exc:
            self.app.set_status(f"Détection bases impossible : {exc}", ERROR_COLOR)
            return

        # Construit la liste des bases utilisables :
        #   [(label affiche, chemin)]
        # Filtre les disques reseau (on_network=True) qui ne sont pas
        # migrables - on les liste tout de meme mais grisees dans le menu.
        entries = []
        current = self.root_var.get().strip()
        for r in roots:
            root_path = str(r.get("root"))
            on_network = bool(r.get("on_network"))
            for base in (r.get("bases") or []):
                base_path = str(base["path"])
                base_name = base["name"]
                # Label : "VEGAR1 (C:\VEGAHF) [reseau]"
                label = f"{base_name} ({root_path})"
                if on_network:
                    label += " [réseau]"
                entries.append({
                    "label": label,
                    "path": base_path,
                    "on_network": on_network,
                    "is_current": (base_path == current),
                })

        if not entries:
            self.app.set_status(
                "Aucune base Vega détectée sur ce poste.", WARN_COLOR,
            )
            return

        # Construction du menu popup
        menu = tk.Menu(self, tearoff=0)
        for entry in entries:
            label = entry["label"]
            if entry["is_current"]:
                label = "✓ " + label
            state = "disabled" if entry["on_network"] else "normal"
            menu.add_command(
                label=label,
                command=lambda p=entry["path"]: self.root_var.set(p),
                state=state,
            )

        # Popup juste sous le bouton
        try:
            x = self.bases_button.winfo_rootx()
            y = self.bases_button.winfo_rooty() + self.bases_button.winfo_height()
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def on_root_changed(self, *_args):
        self.set_migration_enabled(False)
        self.firewall_ok = False
        self.structure_var.set("À vérifier")
        self.firewall_var.set("À vérifier")
        self.structure_label.configure(fg="black")
        self.firewall_label.configure(fg="black")
        download = self.app.modules.get("download") if getattr(self.app, "modules", None) else None
        if download:
            download.set_vega_root(self.root_var.get())
        clean = self.app.modules.get("clean") if getattr(self.app, "modules", None) else None
        if clean:
            clean.set_root(self.root_var.get())
        # Refresh la liste des sauvegardes : elles sont stockees dans
        # <base>/.vega_tool_state/<operation_id>/ donc strictement par-base.
        # Sans ce refresh, la Treeview garde les operations de la base
        # precedente et le rollback peut cibler une operation qui n'existe
        # pas (ou pire, une collision de timestamp dans la nouvelle base).
        self.refresh_backups()
        self._schedule_auto_verify()

    def _schedule_auto_verify(self):
        # Debounce : ne lance la verif que 400 ms apres la derniere modification du champ.
        if self._auto_verify_job is not None:
            try:
                self.after_cancel(self._auto_verify_job)
            except Exception:
                pass
        self._auto_verify_job = self.after(400, self._auto_verify)

    def _auto_verify(self):
        self._auto_verify_job = None
        value = self.root_var.get().strip()
        if not value:
            return
        if not Path(value).is_dir():
            return
        self.refresh_status()

    def current_root(self):
        value = self.root_var.get().strip()
        if not value:
            self.app.set_status("Sélectionnez un dossier Vega.", ERROR_COLOR)
            return None
        return Path(value)

    def refresh_status(self):
        # Rafraîchit à la fois les contrôles du chemin et les sauvegardes disponibles.
        root = self.current_root()
        if not root:
            return

        def task():
            result = {"structure_ok": False, "structure_message": "", "firewall_ok": False}
            try:
                self.app.migration.validate_root(root)
                result["structure_ok"] = True
                result["structure_message"] = "OK"
            except Exception as exc:
                result["structure_message"] = str(exc)
                return result
            result["firewall_ok"] = self.app.migration.firewall_rule_ok()
            return result

        self.app.run_task(
            "Vérification de la migration",
            task,
            on_success=self.apply_status_result,
            clear_display=False,
        )

    def apply_status_result(self, result):
        if not result["structure_ok"]:
            msg = result["structure_message"] or "Vérification impossible."
            self.structure_var.set(msg)
            self.structure_label.configure(fg=ERROR_COLOR)
            self.firewall_var.set("Non vérifié")
            self.firewall_label.configure(fg="black")
            self.firewall_ok = False
            self.set_migration_enabled(False)
            # Status bar : on remonte le vrai message (qui dit ce qui manque
            # vraiment : V6 non extrait, vega.dos absent, etc.) plutot que
            # 'dossier Vega invalide' qui est inutile pour diagnostiquer.
            low = msg.lower()
            if "v6" in low and "introuvable" in low:
                bar = "V6 non installé dans la base. Téléchargez et extrayez le paquet V6."
            elif "vega.dos" in low and "introuvable" in low:
                bar = "Dossier vega.dos absent — étape précédente manquante."
            elif "introuvable" in low:
                bar = "Racine Vega introuvable."
            else:
                bar = f"Vérification : {msg}"
            self.app.set_status(bar, ERROR_COLOR)
            return

        self.structure_var.set("OK")
        self.structure_label.configure(fg=OK_COLOR)
        self.firewall_ok = bool(result["firewall_ok"])

        save_last_vega_root(self.root_var.get().strip())
        self.set_migration_enabled(True)

        if self.firewall_ok:
            self.firewall_var.set("OK")
            self.firewall_label.configure(fg=OK_COLOR)
            self.app.set_status("Vérification de la migration OK.", OK_COLOR)
        else:
            self.firewall_var.set("Non ouvert - fortement recommandé")
            self.firewall_label.configure(fg=WARN_COLOR)
            self.app.set_status("Port TCP 7678 non détecté : migration possible après confirmation.", WARN_COLOR)

    def set_migration_enabled(self, enabled):
        self.verified_ready = enabled
        self.run_button.configure(state=("normal" if enabled else "disabled"))

    def refresh_backups(self):
        root = self.current_root()
        if not root:
            return

        self.tree.delete(*self.tree.get_children())
        self.backup_rows.clear()
        operations = self.app.migration.list_operations(root)
        self.tree.configure(height=max(6, min(12, len(operations) or 6)))

        if not operations:
            row_id = self.tree.insert("", "end", values=("Aucune sauvegarde", "-", "-", "-"))
            self.backup_rows[row_id] = None
            return

        base_name = root.name or str(root)
        status_fr = {
            "completed": "Terminée",
            "completed_with_errors": "Terminée (avertissements)",
            "running": "En cours",
            "failed": "Échec",
            "rolled_back": "Annulée (rollback)",
        }
        # Tri par date decroissante deja fait dans list_operations.
        # Index 1 = la plus recente, 2 = la precedente, etc. -> label "Migration N°1 du JJ/MM"
        for n, operation in enumerate(operations, start=1):
            operation_id = operation.get("id", "?")
            raw_status = operation.get("status", "")
            status_label = status_fr.get(raw_status, raw_status or "?")
            iso = operation.get("created_at", "")
            # Format DD/MM/YYYY a HH:MM
            try:
                from datetime import datetime as _dt
                dt = _dt.fromisoformat(iso) if iso else None
                date_label = dt.strftime("%d/%m/%Y à %H:%M") if dt else "?"
                short_date = dt.strftime("%d/%m") if dt else ""
            except Exception:
                date_label = iso.replace("T", " ")[:19] or "?"
                short_date = ""
            nb_actions = len(operation.get("actions") or [])
            # Label parlant : Migration N°X — Base VEGARxx (jj/mm)
            label = f"Migration N°{n} — Base {base_name}"
            if short_date:
                label += f" ({short_date})"
            row_id = self.tree.insert(
                "",
                "end",
                values=(label, date_label, status_label, str(nb_actions)),
            )
            self.backup_rows[row_id] = operation_id

    def run_migration(self):
        # Dernière barrière avant de modifier les fichiers sur disque.
        root = self.current_root()
        if not root:
            return
        if not self.verified_ready:
            self.app.set_status(
                "Effectuez d'abord une vérification valide avant de lancer la migration.",
                ERROR_COLOR,
            )
            return
        if not self.json_var.get() or not self.hfsql_var.get():
            self.app.set_status("Cochez tous les prérequis utilisateur avant de lancer la migration.", ERROR_COLOR)
            return
        allow_without_firewall = False
        if not self.firewall_ok:
            allow_without_firewall = self.app.confirm_action(
                APP_TITLE,
                "L'ouverture du port TCP 7678 reste fortement recommandée.\n\n"
                "Le port 7678 n'est pas ouvert. Souhaitez-vous vraiment faire la migration ?",
            )
            if not allow_without_firewall:
                self.app.set_status("Migration annulée : port TCP 7678 non ouvert.", WARN_COLOR)
                return
        if not self.app.confirm_action(APP_TITLE, "La migration va modifier les fichiers Vega. Continuer ?"):
            return

        def task():
            return self.app.migration.run_migration(
                root,
                self.json_var.get(),
                self.hfsql_var.get(),
                allow_without_firewall=allow_without_firewall,
                conflict_handler=self._migration_problem_prompt,
            )

        self.app.run_task("Migration", task, on_success=self.on_migration_done, clear_display=True)

    def _migration_problem_prompt(self, kind, name, detail):
        # Appelé depuis le worker thread de migration quand une anomalie survient
        # (kind "locked" = fichier verrouillé, kind "missing" = élément manquant).
        # On marshalle l'affichage de la popup vers le thread Tk et on bloque le
        # worker jusqu'à la réponse. Retourne "skip" | "skip_all" | "abort".
        import threading
        event = threading.Event()
        outcome = {"decision": "abort"}

        def show():
            try:
                outcome["decision"] = self._show_problem_dialog(kind, name, detail)
            finally:
                event.set()

        self.app.after(0, show)
        event.wait()
        return outcome["decision"]

    def _show_problem_dialog(self, kind, name, detail):
        # Popup modale (thread Tk) : informe qu'un problème est survenu pendant
        # la migration, propose Continuer (ignorer) / Annuler tout (rollback),
        # avec une case "ignorer aussi les prochains problèmes".
        result = {"value": "abort"}
        ignore_next = tk.BooleanVar(value=False)

        if kind == "locked":
            win_title = "Fichier verrouillé"
            header = "Un fichier ne peut pas être déplacé"
            body = (
                f"{('Fichier : ' + name) if name else 'Un fichier'}\n\n"
                f"Détail : {detail}\n"
                "Ce fichier est probablement utilisé par un autre programme."
            )
            check_text = "Ignorer aussi les prochains fichiers verrouillés"
        else:
            win_title = "Élément manquant"
            header = "Un élément attendu est manquant"
            body = (
                f"{('Élément : ' + name) if name else 'Un élément attendu'}\n\n"
                f"Détail : {detail}"
            )
            check_text = "Ignorer aussi les prochaines anomalies"

        dialog = tk.Toplevel(self)
        try:
            from ..resources import set_window_icon
            set_window_icon(dialog)
        except Exception:
            pass
        dialog.title(win_title)
        dialog.resizable(False, False)
        dialog.configure(bg=WINDOW_BG)
        dialog.transient(self.winfo_toplevel())

        host = tk.Frame(dialog, bg=WINDOW_BG, padx=16, pady=14)
        host.pack(fill="both", expand=True)
        host.columnconfigure(0, weight=1)

        tk.Label(
            host, text=header,
            bg=WINDOW_BG, fg=ERROR_COLOR, font=("Tahoma", 10, "bold"),
            anchor="w", justify="left",
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            host, text=body,
            bg=WINDOW_BG, font=("Tahoma", 9), anchor="w", justify="left", wraplength=440,
        ).grid(row=1, column=0, sticky="w", pady=(6, 0))
        tk.Label(
            host,
            text="Voulez-vous continuer la migration ?\n"
                 "• Oui : ceci est ignoré et la migration continue.\n"
                 "• Non : toute la migration est annulée (retour arrière complet).",
            bg=WINDOW_BG, font=("Tahoma", 9), anchor="w", justify="left", wraplength=440,
        ).grid(row=2, column=0, sticky="w", pady=(10, 0))
        tk.Checkbutton(
            host, text=check_text,
            variable=ignore_next, bg=WINDOW_BG, activebackground=WINDOW_BG,
            font=("Tahoma", 9),
        ).grid(row=3, column=0, sticky="w", pady=(10, 0))

        buttons = tk.Frame(host, bg=WINDOW_BG)
        buttons.grid(row=4, column=0, sticky="e", pady=(16, 0))

        def close_with(value):
            result["value"] = value
            dialog.destroy()

        tool_button(
            buttons, text="Oui, ignorer",
            command=lambda: close_with("skip_all" if ignore_next.get() else "skip"),
            width=14, anchor="center",
        ).grid(row=0, column=0, padx=(0, 8))
        tool_button(
            buttons, text="Non, tout annuler",
            command=lambda: close_with("abort"),
            width=16, anchor="center",
        ).grid(row=0, column=1)

        dialog.protocol("WM_DELETE_WINDOW", lambda: close_with("abort"))
        dialog.bind("<Escape>", lambda _e: close_with("abort"))

        dialog.update_idletasks()
        parent = self.winfo_toplevel()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        dw, dh = dialog.winfo_width(), dialog.winfo_height()
        dialog.geometry(f"+{px + max((pw - dw) // 2, 0)}+{py + max((ph - dh) // 2, 0)}")

        dialog.grab_set()
        dialog.focus_force()
        self.wait_window(dialog)
        return result["value"]

    def on_migration_done(self, manifest):
        self.refresh_backups()
        status = manifest.get("status") or "unknown"
        if status == "completed":
            self.app.set_status("Migration terminée avec succès.", OK_COLOR)
            audit_status = "success"
        else:
            self.app.set_status("Migration terminée avec avertissements. Consultez le journal.", WARN_COLOR)
            audit_status = "warning"
        self.app.audit.log_action(
            "migration_run",
            status=audit_status,
            details={
                "root": str(self.current_root()),
                "operation_id": manifest.get("operation_id"),
                "status": status,
                "json_reset_confirmed": self.json_var.get(),
                "hfsql_stopped_confirmed": self.hfsql_var.get(),
                "firewall_ok": self.firewall_ok,
            },
        )
        # Popup confirm pour relancer HFSQL apres migration (le user a demande
        # une confirmation explicite plutot qu'un restart silencieux).
        if self._hfsql_state == "stopped":
            if self.app.confirm_action(
                "Relancer HFSQL",
                "La migration est terminée.\n\n"
                "Souhaitez-vous relancer le service HFSQL maintenant ?",
            ):
                self.app.run_task(
                    "Démarrage du service HFSQL",
                    self.app.migration.start_hfsql_service,
                    on_success=self._on_hfsql_started,
                    success_message="Service HFSQL relancé après migration.",
                    key="migration_hfsql_toggle",
                )

    def rollback(self):
        # Annule l'opération sélectionnée dans la liste des sauvegardes.
        root = self.current_root()
        if not root:
            return
        selection = self.tree.selection()
        if not selection:
            self.app.set_status("Sélectionnez une sauvegarde à annuler.", ERROR_COLOR)
            return

        operation_id = self.backup_rows.get(selection[0])
        if not operation_id:
            self.app.set_status("Aucune sauvegarde exploitable n'est disponible pour ce dossier.", WARN_COLOR)
            return
        # Garde-fou multi-base : verifie que cette operation existe BIEN dans
        # la base courante. Si la liste affiche une operation d'une ancienne
        # base (refresh manque) ou si l'utilisateur a change de base entre
        # temps, on stoppe net plutot que de risquer un rollback errone.
        from pathlib import Path as _P
        manifest_path = _P(root) / ".vega_tool_state" / operation_id / "manifest.json"
        if not manifest_path.is_file():
            self.app.set_status(
                f"L'opération {operation_id} n'existe pas dans la base courante "
                f"({root.name}). Liste rafraîchie.",
                ERROR_COLOR,
            )
            self.refresh_backups()
            return
        if not self.app.confirm_action(
            APP_TITLE,
            f"Retour arrière complet de l'opération {operation_id} ?\n\n"
            f"Base ciblée : {root}",
        ):
            return

        def task():
            return self.app.migration.rollback(root, operation_id)

        self.app.run_task(
            "Retour arrière",
            task,
            on_success=lambda _result: self.after_rollback(operation_id),
            clear_display=True,
        )

    def after_rollback(self, operation_id):
        self.refresh_backups()
        self.app.set_status(f"Retour arrière terminé pour {operation_id}.", OK_COLOR)
        self.app.audit.log_action(
            "migration_rollback",
            status="success",
            details={"root": str(self.current_root()), "operation_id": operation_id},
        )

    def toggle_hfsql(self):
        # Bouton dynamique : arrete si en cours, relance si arrete.
        # Etat inconnu = on tente l'arret par defaut (cas le plus frequent en migration).
        if self._hfsql_state == "stopped":
            self._start_hfsql()
        else:
            self._stop_hfsql()

    def _stop_hfsql(self):
        if not self.app.confirm_action(
            "Arrêter HFSQL",
            "Arrêter le service HFSQL maintenant ? Toute connexion en cours sera coupée.",
        ):
            return
        self.app.run_task(
            "Arrêt du service HFSQL",
            self.app.migration.stop_hfsql_service,
            on_success=self._on_hfsql_stopped,
            success_message="Service HFSQL arrêté.",
            key="migration_hfsql_toggle",
        )

    def _start_hfsql(self):
        if not self.app.confirm_action(
            "Relancer HFSQL",
            "Redémarrer le service HFSQL maintenant ?",
        ):
            return
        self.app.run_task(
            "Démarrage du service HFSQL",
            self.app.migration.start_hfsql_service,
            on_success=self._on_hfsql_started,
            success_message="Service HFSQL démarré.",
            key="migration_hfsql_toggle",
        )

    def _on_hfsql_stopped(self, _data):
        self.hfsql_var.set(True)
        self._set_hfsql_state("stopped")
        self.app.audit.log_action("hfsql_stop", status="success")

    def _on_hfsql_started(self, _data):
        self.hfsql_var.set(False)
        self.app.audit.log_action("hfsql_start", status="success")
        self._set_hfsql_state("running")

    def _refresh_hfsql_state(self):
        # Lecture differee, sans busy : met a jour le bouton selon l'etat reel.
        self.app.run_task(
            "Lecture statut HFSQL",
            self.app.migration.hfsql_service_status,
            on_success=self._apply_hfsql_state,
            key="migration_hfsql_status",
        )

    def _apply_hfsql_state(self, data):
        data = data or {}
        if not data.get("found"):
            self._set_hfsql_state("missing")
            self.hfsql_var.set(True)
            return
        if data.get("running"):
            self._set_hfsql_state("running")
            self.hfsql_var.set(False)
        else:
            self._set_hfsql_state("stopped")
            self.hfsql_var.set(True)

    def _set_hfsql_state(self, state):
        self._hfsql_state = state
        if not hasattr(self, "hfsql_button"):
            return
        if state == "stopped":
            self.hfsql_button.configure(text="Relancer", state="normal")
        elif state == "missing":
            self.hfsql_button.configure(text="Indisponible", state="disabled")
        else:
            self.hfsql_button.configure(text="Arrêter", state="normal")

    def reset_json(self):
        # Action TRES sensible : double confirmation pour eviter le clic accidentel.
        if not self.app.confirm_action(
            "Réinitialiser le JSON RetailForce",
            "Cette action vide la liste FiscalClients du fichier de configuration RetailForce.\n\nContinuer ?",
        ):
            return
        if not self.app.confirm_action(
            "Confirmation finale",
            "Une sauvegarde horodatée du fichier sera créée à côté avant le reset.\n\nConfirmer définitivement la réinitialisation ?",
        ):
            return

        def _on_reset(data):
            self.json_var.set(True)
            backup = (data or {}).get("backup_path")
            if backup:
                from pathlib import Path
                self.app.set_status(f"JSON réinitialisé. Sauvegarde : {Path(backup).name}", None)
            self.app.audit.log_action(
                "retailforce_json_reset",
                status="success",
                details={"backup_path": backup},
            )

        self.app.run_task(
            "Réinitialisation JSON RetailForce",
            self.app.migration.reset_retailforce_json,
            on_success=_on_reset,
            success_message="Fichier JSON RetailForce réinitialisé.",
            key="migration_json_reset",
        )
