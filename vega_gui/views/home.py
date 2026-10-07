import tkinter as tk
from pathlib import Path

from vega_backend import RETAILFORCE_TARGET_VERSION, is_admin
from vega_backend._compat import os_architecture

from ..theme import (
    ACCENT_BLUE,
    APP_TITLE,
    APP_VERSION_LABEL,
    ERROR_COLOR,
    MUTED_TEXT,
    OK_COLOR,
    PANEL_BG,
    WARN_COLOR,
    WHITE_BG,
    WINDOW_BG,
)
from ..widgets import tool_button
from ._home_checks import HomeChecksMixin


STATUS_UNKNOWN = "Non vérifié"


class HomeView(HomeChecksMixin, tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=WINDOW_BG, padx=14, pady=12)
        self.app = app

        self.firewall_var = tk.StringVar(value=STATUS_UNKNOWN)
        self.impressions_var = tk.StringVar(value=STATUS_UNKNOWN)
        self.structure_var = tk.StringVar(value=STATUS_UNKNOWN)
        self.hfsql_var = tk.StringVar(value=STATUS_UNKNOWN)
        self.json_var = tk.StringVar(value=STATUS_UNKNOWN)
        self.vega_root_var = tk.StringVar(value=str(Path.cwd()))
        self.root_status_var = tk.StringVar(value="Cliquez sur « Tout vérifier » pour détecter.")

        self.firewall_label = None
        self.impressions_label = None
        self.structure_label = None
        self.hfsql_label = None
        self.json_label = None
        self.root_value_label = None
        self.root_status_label = None

        # Bouton "Bases" de l'accueil : grise tant que "Tout verifier" n'a pas
        # scanne le poste. Une fois le scan fait (detect_root), il se degrise et
        # permet de choisir la base a utiliser pour l'ensemble des verifications.
        self.home_bases_button = None
        self._detected_roots = []

        # Outils tiers (Notepad++ + JsonTools).
        self.notepad_status_var = tk.StringVar(value="")
        self.notepad_status_label = None
        self.notepad_button = None

        # Reinstallation RetailForce.
        self.retailforce_status_var = tk.StringVar(value="")
        self.retailforce_status_label = None
        self.retailforce_button = None
        self.retailforce_update_button = None

        self.columnconfigure(0, weight=1)
        self.build_banner()
        self.build_root_panel()
        self.build_status_panel()
        self.build_shortcuts_panel()
        self.build_tools_panel()

    def build_banner(self):
        banner = tk.Frame(self, bg=PANEL_BG, bd=1, relief="groove", padx=14, pady=10)
        banner.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        banner.columnconfigure(0, weight=1)
        # Reference exposee pour le mode Tutoriel.
        self.banner_frame = banner

        tk.Label(
            banner,
            text=f"Bienvenue dans {APP_TITLE}",
            bg=PANEL_BG,
            font=("Tahoma", 14, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w")

        tk.Label(
            banner,
            text="Outil interne de migration et support VEGA6. Tableau de bord en lecture seule — utilisez les modules à gauche pour les actions.",
            bg=PANEL_BG,
            fg=MUTED_TEXT,
            font=("Tahoma", 9),
            anchor="w",
            wraplength=760,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        tk.Label(
            banner,
            text=f"Version {APP_VERSION_LABEL}",
            bg=PANEL_BG,
            fg=MUTED_TEXT,
            font=("Tahoma", 8),
            anchor="e",
        ).grid(row=0, column=1, rowspan=2, sticky="e", padx=(10, 0))

    def build_root_panel(self):
        # Racine Vega : auto-detectee, affichee en lecture seule. Modification dans l'onglet Migration.
        panel = tk.LabelFrame(self, text="Racine Vega détectée", padx=12, pady=10, bg=WINDOW_BG)
        panel.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        panel.columnconfigure(0, weight=1)
        self.root_panel_frame = panel

        self.root_value_label = tk.Label(
            panel,
            textvariable=self.vega_root_var,
            bg=WINDOW_BG,
            fg=ACCENT_BLUE,
            font=("Tahoma", 9, "bold"),
            anchor="w",
            justify="left",
            wraplength=760,
        )
        self.root_value_label.grid(row=0, column=0, sticky="w")

        self.root_status_label = tk.Label(
            panel,
            textvariable=self.root_status_var,
            bg=WINDOW_BG,
            fg=MUTED_TEXT,
            font=("Tahoma", 8),
            anchor="w",
            justify="left",
            wraplength=760,
        )
        self.root_status_label.grid(row=1, column=0, sticky="w", pady=(2, 0))

    def build_status_panel(self):
        # Section etat du poste : 6 lignes en lecture seule + bouton "Tout verifier" en bas.
        # Aucun check automatique : l'utilisateur declenche la verification manuellement.
        panel = tk.LabelFrame(self, text="État du poste", padx=12, pady=10, bg=WINDOW_BG)
        panel.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        self.status_panel_frame = panel
        panel.columnconfigure(0, weight=0, minsize=170)
        panel.columnconfigure(1, weight=1, minsize=120)

        self._status_row(
            panel, 0, "Administrateur",
            "OUI" if is_admin() else "NON",
            OK_COLOR if is_admin() else ERROR_COLOR,
        )
        self.firewall_label = self._status_row(panel, 1, "Pare-feu TCP 7678", STATUS_UNKNOWN, MUTED_TEXT)
        self.impressions_label = self._status_row(panel, 2, "C:\\ImpressionsVega", STATUS_UNKNOWN, MUTED_TEXT)
        self.structure_label = self._status_row(panel, 3, "Structure vega.dos\\V6", STATUS_UNKNOWN, MUTED_TEXT)
        self.hfsql_label = self._status_row(panel, 4, "Service HFSQL", STATUS_UNKNOWN, MUTED_TEXT)
        self.json_label = self._status_row(panel, 5, "JSON RetailForce", STATUS_UNKNOWN, MUTED_TEXT)

        actions = tk.Frame(panel, bg=WINDOW_BG)
        actions.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        tool_button(
            actions,
            text="Tout vérifier",
            command=self.run_full_check,
            image=self.app.icons.get("verifier"),
            anchor="center",
        ).pack(side="right")
        # Bouton "Bases" : grise au depart (aucun scan encore fait). Il se
        # degrise apres "Tout verifier" et permet de basculer la base ciblee
        # pour toutes les verifications.
        self.home_bases_button = tool_button(
            actions,
            text="Bases ▾",
            command=self.show_bases_menu,
            image=self.app.icons.get("database"),
            anchor="center",
            state="disabled",
        )
        self.home_bases_button.pack(side="right", padx=(0, 6))

    def build_shortcuts_panel(self):
        panel = tk.LabelFrame(self, text="Démarrage rapide", padx=12, pady=10, bg=WINDOW_BG)
        panel.grid(row=3, column=0, sticky="ew")
        self.shortcuts_panel_frame = panel
        for col in range(4):
            panel.columnconfigure(col, weight=1, uniform="shortcut")

        shortcuts = [
            ("Migration Vega", "lancer", self.app.show_migration_tab),
            ("Téléchargement", "parcourir", self.app.show_download_tab),
            ("Nettoyage Vega", "reparer", self.app.show_clean_tab),
            ("IP fixe", "actualiser", self.app.show_ip_tab),
        ]
        for col, (text, icon_key, cmd) in enumerate(shortcuts):
            tool_button(
                panel,
                text=text,
                command=cmd,
                image=self.app.icons.get(icon_key),
                anchor="center",
                padx=10,
                pady=10,
            ).grid(row=0, column=col, sticky="ew", padx=4)

    def build_tools_panel(self):
        # Outils complementaires : 2 colonnes cote a cote, taille uniforme.
        # tk.Button ne s'etire pas tout seul a sa cellule grid : on wrap dans un Frame +
        # button.pack(fill='x', expand=True) pour forcer le bouton a prendre la largeur du Frame.
        panel = tk.LabelFrame(self, text="Outils complémentaires", padx=12, pady=8, bg=WINDOW_BG)
        panel.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        self.tools_panel_frame = panel
        panel.columnconfigure(0, weight=1, uniform="tools")
        panel.columnconfigure(1, weight=1, uniform="tools")

        npp_cell = tk.Frame(panel, bg=WINDOW_BG)
        npp_cell.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.notepad_button = tool_button(
            npp_cell,
            text="Installer Notepad++ + JsonTools",
            command=self.install_notepad,
            image=self.app.icons.get("notepad"),
            anchor="w",
            padx=10,
            pady=6,
        )
        self.notepad_button.pack(fill="x", expand=True)

        self.notepad_status_label = tk.Label(
            panel,
            textvariable=self.notepad_status_var,
            bg=WINDOW_BG,
            fg=MUTED_TEXT,
            font=("Tahoma", 8),
            anchor="w",
            justify="left",
            wraplength=360,
        )
        self.notepad_status_label.grid(row=1, column=0, sticky="ew", padx=(0, 4), pady=(2, 0))

        rf_cell = tk.Frame(panel, bg=WINDOW_BG)
        rf_cell.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        self.retailforce_button = tool_button(
            rf_cell,
            text=f"Installer RetailForce {RETAILFORCE_TARGET_VERSION}",
            command=self.install_retailforce,
            image=self.app.icons.get("money_euro"),
            anchor="w",
            padx=10,
            pady=6,
        )
        self.retailforce_button.pack(fill="x", expand=True)

        # Bouton dedie a la mise a jour RetailForce (distinct de la reinstall).
        # Placeholder : le script de MAJ n'est pas encore fourni, on affiche
        # juste une confirmation puis un message "a venir".
        self.retailforce_update_button = tool_button(
            rf_cell,
            text="Mettre à jour RetailForce",
            command=self.update_retailforce,
            image=self.app.icons.get("actualiser"),
            anchor="w",
            padx=10,
            pady=6,
        )
        self.retailforce_update_button.pack(fill="x", expand=True, pady=(4, 0))

        self.retailforce_status_label = tk.Label(
            panel,
            textvariable=self.retailforce_status_var,
            bg=WINDOW_BG,
            fg=MUTED_TEXT,
            font=("Tahoma", 8),
            anchor="w",
            justify="left",
            wraplength=360,
        )
        self.retailforce_status_label.grid(row=1, column=1, sticky="ew", padx=(4, 0), pady=(2, 0))

    def install_notepad(self):
        # Lance le pipeline complet en arriere-plan. Met a jour notepad_status_var en live
        # pour montrer la progression sans bloquer l'UI.
        if not self.app.confirm_action(
            APP_TITLE,
            "Installer / mettre à jour Notepad++ et le plugin JSON Tools ?\n\n"
            "L'installation se fait en silencieux et nécessite les droits administrateur.",
        ):
            return

        # Disable button during install.
        if self.notepad_button:
            self.notepad_button.configure(state="disabled")
        self._set_notepad_status("Préparation...", ACCENT_BLUE)

        def on_progress(step, message):
            # Appele depuis le worker thread : marshalling vers Tk.
            color = ACCENT_BLUE
            if step.endswith("install") and "installe" in message.lower():
                color = OK_COLOR
            self.after(0, lambda m=message, c=color: self._set_notepad_status(m, c))

        def on_success(result):
            if self.notepad_button:
                self.notepad_button.configure(state="normal")
            text = (
                f"✓ Notepad++ {result.get('npp_version', '?')} + "
                f"JsonTools {result.get('jsontools_version', '?')} installés"
            )
            self._set_notepad_status(text, OK_COLOR)
            self.app.set_status("Notepad++ et JsonTools installés.", OK_COLOR)
            self.app.audit.log_action(
                "notepad_jsontools_install",
                status="success",
                details={
                    "npp_version": result.get("npp_version"),
                    "jsontools_version": result.get("jsontools_version"),
                },
            )

        def on_error(error):
            if self.notepad_button:
                self.notepad_button.configure(state="normal")
            self._set_notepad_status(f"Échec : {error}", ERROR_COLOR)
            self.app.audit.log_action(
                "notepad_jsontools_install",
                status="failure",
                error=error,
            )

        self.app.run_task(
            "Installation Notepad++",
            lambda: self.app.notepad.install_npp_and_jsontools(on_progress=on_progress),
            on_success=on_success,
            on_error=on_error,
            key="notepad_install",
        )

    def _set_notepad_status(self, text, color):
        self.notepad_status_var.set(text)
        if self.notepad_status_label:
            self.notepad_status_label.configure(fg=color)

    def install_retailforce(self):
        # 1) Detection synchrone (registry read, ~10ms) pour informer le user dans la popup.
        # 2) Confirmation utilisateur avec version actuelle vs cible.
        # 3) Pipeline complet en arriere-plan : uninstall + cleanup folders + download MSI + install.
        try:
            current = self.app.retailforce.detect_installed()
        except Exception as exc:
            self._set_retailforce_status(f"Erreur détection : {exc}", ERROR_COLOR)
            return

        # Architecture choisie automatiquement d'apres celle du SYSTEME (et non
        # du processus : le build legacy est 32 bits mais tourne aussi sur des
        # Windows 64 bits). Aucune question posee au technicien.
        arch = os_architecture()
        arch_label = "64 bits" if arch == "x64" else "32 bits"

        if current:
            message = (
                f"RetailForce {current['version']} est actuellement installé "
                f"({current['display_name']}).\n\n"
                f"Il sera désinstallé en silencieux puis remplacé par la version "
                f"{RETAILFORCE_TARGET_VERSION} en {arch_label}.\n\n"
                f"Les dossiers suivants seront également supprimés :\n"
                f"  • C:\\Program Files\\RetailForce\n"
                f"  • C:\\ProgramData\\RetailForce\n\n"
                f"Continuer ?"
            )
        else:
            message = (
                f"RetailForce n'est pas détecté sur ce poste.\n\n"
                f"La version {RETAILFORCE_TARGET_VERSION} ({arch_label}) va être "
                f"installée en silencieux.\n\n"
                f"Continuer ?"
            )

        if not self.app.confirm_action(APP_TITLE, message):
            return

        # 2e warning : challenge de combinaison de 4 fleches aleatoires.
        # Une reinstallation RetailForce est une operation lourde (desinstall +
        # cleanup + install) ; on veut etre certain que l'utilisateur le fait
        # exprès et pas par clic accidentel.
        if not self._arrow_combo_challenge():
            self._set_retailforce_status("Réinstallation annulée.", MUTED_TEXT)
            return

        if self.retailforce_button:
            self.retailforce_button.configure(state="disabled")
        self._set_retailforce_status("Préparation...", ACCENT_BLUE)

        def on_progress(_step, message):
            self.after(0, lambda m=message: self._set_retailforce_status(m, ACCENT_BLUE))

        def on_success(result):
            if self.retailforce_button:
                self.retailforce_button.configure(state="normal")
            previous = result.get("previous_version") or "—"
            new_version = result.get("new_version", RETAILFORCE_TARGET_VERSION)
            text = f"✓ RetailForce {new_version} installé (précédent : {previous})"
            self._set_retailforce_status(text, OK_COLOR)
            self.app.set_status(f"RetailForce {new_version} installé.", OK_COLOR)
            self.app.audit.log_action(
                "retailforce_install",
                status="success",
                details={
                    "previous_version": result.get("previous_version"),
                    "new_version": new_version,
                    "architecture": arch,
                },
            )

        def on_error(error):
            if self.retailforce_button:
                self.retailforce_button.configure(state="normal")
            self._set_retailforce_status(f"Échec : {error}", ERROR_COLOR)
            self.app.audit.log_action(
                "retailforce_install",
                status="failure",
                error=error,
            )

        self.app.run_task(
            f"Réinstallation RetailForce ({arch_label})",
            lambda: self.app.retailforce.install_or_replace(
                on_progress=on_progress, architecture=arch,
            ),
            on_success=on_success,
            on_error=on_error,
            key="retailforce_install",
        )

    def _set_retailforce_status(self, text, color):
        self.retailforce_status_var.set(text)
        if self.retailforce_status_label:
            self.retailforce_status_label.configure(fg=color)

    def update_retailforce(self):
        # PLACEHOLDER : la mise a jour RetailForce n'est pas encore implementee
        # (script non fourni). On affiche une confirmation puis un message clair
        # indiquant que la fonctionnalite arrive. Quand le script sera dispo,
        # remplacer le corps par le vrai pipeline (cf. install_retailforce).
        try:
            current = self.app.retailforce.detect_installed()
        except Exception:
            current = None
        version_txt = f" (version actuelle : {current['version']})" if current else ""
        if not self.app.confirm_action(
            APP_TITLE,
            f"Voulez-vous vraiment mettre à jour RetailForce{version_txt} ?\n\n"
            "La mise à jour conserve la configuration existante et remplace "
            "uniquement les composants du service fiscal.\n\n"
            "Continuer ?",
        ):
            return
        # TODO : brancher ici le vrai script de mise a jour RetailForce.
        self._set_retailforce_status(
            "Mise à jour RetailForce : fonctionnalité à venir (script non fourni).",
            WARN_COLOR,
        )
        self.app.set_status(
            "Mise à jour RetailForce : non encore disponible.", WARN_COLOR,
        )
        self.app.logger.warn(
            "Mise à jour RetailForce demandée mais non implémentée (placeholder)."
        )

    def show_bases_menu(self):
        # Menu deroulant listant toutes les bases Vega detectees lors du dernier
        # "Tout verifier" (self._detected_roots, alimente par _on_root_detected).
        # Choisir une base met a jour vega_root_var, qui est partagee avec
        # l'onglet Migration : la selection se propage donc a tout l'outil.
        roots = self._detected_roots or []
        entries = []
        current = self.vega_root_var.get().strip()
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
            self.set_root_status(
                "Aucune base Vega détectée sur ce poste.", WARN_COLOR,
            )
            return

        menu = tk.Menu(self, tearoff=0)
        for entry in entries:
            label = ("✓ " + entry["label"]) if entry["is_current"] else entry["label"]
            state = "disabled" if entry["on_network"] else "normal"
            menu.add_command(
                label=label,
                command=lambda e=entry: self._select_home_base(e),
                state=state,
            )
        try:
            x = self.home_bases_button.winfo_rootx()
            y = self.home_bases_button.winfo_rooty() + self.home_bases_button.winfo_height()
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _select_home_base(self, entry):
        # Applique la base choisie a tout l'outil (vega_root_var partagee avec
        # Migration -> declenche la re-verification cote Migration via son trace).
        self.vega_root_var.set(entry["path"])
        self.set_root_status(f"Base sélectionnée : {entry['label']}", OK_COLOR)

    def _arrow_combo_challenge(self):
        # 2e confirmation : l'utilisateur doit reproduire au clavier une
        # combinaison aleatoire de 4 fleches. Empeche les clics accidentels
        # sur des operations destructives (reinstall RetailForce). Retourne
        # True si la combinaison est saisie correctement, False sinon.
        import random
        directions = [
            ("Up", "↑"),
            ("Down", "↓"),
            ("Left", "←"),
            ("Right", "→"),
        ]
        target = [random.choice(directions) for _ in range(4)]
        target_keys = [d[0] for d in target]
        target_symbols = [d[1] for d in target]

        result = {"value": False}
        progress = {"index": 0}

        dialog = tk.Toplevel(self)
        try:
            from ..resources import set_window_icon
            set_window_icon(dialog)
        except Exception:
            pass
        dialog.title("Confirmation finale")
        dialog.resizable(False, False)
        dialog.configure(bg=WINDOW_BG)
        dialog.transient(self.winfo_toplevel())

        host = tk.Frame(dialog, bg=WINDOW_BG, padx=20, pady=16)
        host.pack(fill="both", expand=True)

        tk.Label(
            host,
            text="Reproduisez la combinaison ci-dessous au clavier",
            bg=WINDOW_BG,
            font=("Tahoma", 10, "bold"),
            fg=ACCENT_BLUE,
        ).pack(anchor="w")
        tk.Label(
            host,
            text="(touches flèches du clavier — Échap pour annuler)",
            bg=WINDOW_BG,
            font=("Tahoma", 8),
            fg=MUTED_TEXT,
        ).pack(anchor="w", pady=(0, 12))

        # Affichage des 4 fleches cibles + indicateur de progression.
        arrows_frame = tk.Frame(host, bg=WINDOW_BG)
        arrows_frame.pack(pady=8)
        arrow_labels = []
        for symbol in target_symbols:
            lbl = tk.Label(
                arrows_frame,
                text=symbol,
                bg=WHITE_BG,
                fg="#000000",
                font=("Segoe UI Symbol", 28, "bold"),
                width=2,
                relief="ridge",
                bd=2,
                padx=8,
                pady=4,
            )
            lbl.pack(side="left", padx=4)
            arrow_labels.append(lbl)

        status_var = tk.StringVar(value="En attente...")
        status_label = tk.Label(
            host,
            textvariable=status_var,
            bg=WINDOW_BG,
            font=("Tahoma", 9),
            fg=MUTED_TEXT,
        )
        status_label.pack(pady=(14, 0))

        def reset_combo():
            progress["index"] = 0
            for lbl in arrow_labels:
                lbl.configure(bg=WHITE_BG, fg="#000000")
            status_var.set("Erreur. Recommencez.")
            status_label.configure(fg=ERROR_COLOR)

        def on_arrow(direction_name):
            if progress["index"] >= len(target_keys):
                return
            expected = target_keys[progress["index"]]
            if direction_name == expected:
                # Bonne touche : verdir et avancer.
                arrow_labels[progress["index"]].configure(bg=OK_COLOR, fg="white")
                progress["index"] += 1
                if progress["index"] == len(target_keys):
                    status_var.set("Combinaison validée.")
                    status_label.configure(fg=OK_COLOR)
                    result["value"] = True
                    dialog.after(500, dialog.destroy)
                else:
                    status_var.set(f"Bonne touche ({progress['index']}/{len(target_keys)})")
                    status_label.configure(fg=OK_COLOR)
            else:
                reset_combo()

        for direction_name, _symbol in directions:
            dialog.bind(f"<KeyPress-{direction_name}>",
                       lambda _e, d=direction_name: on_arrow(d))

        def cancel(_event=None):
            result["value"] = False
            dialog.destroy()

        dialog.protocol("WM_DELETE_WINDOW", cancel)
        dialog.bind("<Escape>", cancel)

        # Centre la fenetre sur l'app principale.
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

    def _status_row(self, parent, row, label, value, color):
        tk.Label(parent, text=label, bg=WINDOW_BG, font=("Tahoma", 9)).grid(
            row=row, column=0, sticky="w", pady=2
        )
        value_label = tk.Label(
            parent,
            text=value,
            bg=WINDOW_BG,
            fg=color,
            font=("Tahoma", 9, "bold"),
            anchor="w",
        )
        value_label.grid(row=row, column=1, sticky="w", padx=(10, 0), pady=2)
        return value_label

    def set_firewall_status(self, text, color):
        self.firewall_var.set(text)
        if self.firewall_label:
            self.firewall_label.configure(text=text, fg=color)

    def set_impressions_status(self, text, color):
        self.impressions_var.set(text)
        if self.impressions_label:
            self.impressions_label.configure(text=text, fg=color)

    def set_structure_status(self, text, color):
        self.structure_var.set(text)
        if self.structure_label:
            self.structure_label.configure(text=text, fg=color)

    def set_hfsql_status(self, text, color):
        self.hfsql_var.set(text)
        if self.hfsql_label:
            self.hfsql_label.configure(text=text, fg=color)

    def set_json_status(self, text, color):
        self.json_var.set(text)
        if self.json_label:
            self.json_label.configure(text=text, fg=color)

    def set_root_status(self, text, color):
        self.root_status_var.set(text)
        if self.root_status_label:
            self.root_status_label.configure(fg=color)

    # check_firewall / check_impressions / check_structure / check_hfsql / check_json
    # check_all / run_full_check / detect_root et leurs callbacks _on_*_checked
    # sont herites de HomeChecksMixin.

    def _propagate(self, module_key, method_name, data):
        # Forward le resultat d'un check a l'onglet correspondant pour synchroniser l'affichage.
        modules = getattr(self.app, "modules", None) or {}
        module = modules.get(module_key)
        if not module:
            return
        method = getattr(module, method_name, None)
        if callable(method):
            try:
                method(data)
            except Exception:
                pass

    def _set_migration_var(self, var_name, value):
        modules = getattr(self.app, "modules", None) or {}
        migration = modules.get("migration")
        if not migration:
            return
        var = getattr(migration, var_name, None)
        if var is not None:
            try:
                var.set(value)
            except Exception:
                pass

    def _prompt_choice(self, title, message, options):
        # Popup modal : liste de radio-boutons. Retourne la value choisie ou None si annule.
        if not options:
            return None
        result = {"value": None}
        dialog = tk.Toplevel(self)
        dialog.title(title)
        dialog.configure(bg=WINDOW_BG)
        dialog.transient(self.winfo_toplevel())
        dialog.resizable(False, False)
        try:
            from ..resources import set_window_icon
            set_window_icon(dialog)
        except Exception:
            pass

        host = tk.Frame(dialog, bg=WINDOW_BG, padx=16, pady=14)
        host.pack(fill="both", expand=True)
        host.columnconfigure(0, weight=1)

        tk.Label(
            host,
            text=message,
            bg=WINDOW_BG,
            justify="left",
            wraplength=420,
            font=("Tahoma", 9),
        ).grid(row=0, column=0, sticky="w")

        choice_var = tk.StringVar(value=options[0][1])
        choices_frame = tk.Frame(host, bg=WINDOW_BG)
        choices_frame.grid(row=1, column=0, sticky="w", pady=(10, 0))
        for label, value in options:
            tk.Radiobutton(
                choices_frame,
                text=label,
                variable=choice_var,
                value=value,
                bg=WINDOW_BG,
                activebackground=WINDOW_BG,
                anchor="w",
                font=("Tahoma", 9),
            ).pack(anchor="w")

        buttons = tk.Frame(host, bg=WINDOW_BG)
        buttons.grid(row=2, column=0, sticky="e", pady=(14, 0))

        def confirm():
            result["value"] = choice_var.get()
            dialog.destroy()

        def cancel():
            dialog.destroy()

        tool_button(buttons, text="Valider", command=confirm, width=12, anchor="center").pack(side="right", padx=(6, 0))
        tool_button(buttons, text="Annuler", command=cancel, width=12, anchor="center").pack(side="right")

        dialog.protocol("WM_DELETE_WINDOW", cancel)
        dialog.bind("<Escape>", lambda _e: cancel())
        dialog.bind("<Return>", lambda _e: confirm())

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

    def bind_root_var(self, shared_var):
        # Partage la StringVar de l'onglet Migration : edition synchronisee 2-way (lecture seule cote home).
        previous = self.vega_root_var.get()
        self.vega_root_var = shared_var
        if previous and not shared_var.get().strip():
            shared_var.set(previous)
        if self.root_value_label:
            self.root_value_label.configure(textvariable=shared_var)

    def refresh(self):
        # Pas de check automatique : l'utilisateur clique sur "Tout vérifier" pour declencher.
        pass
