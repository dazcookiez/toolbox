"""Onglet HFSQL : installation / desinstallation / reinstallation du
moteur HFSQL Client/Serveur de PCSoft (WX{ver}PACKHFSQLCS) en silencieux.

Structure (3 zones empilees) :
  - Etat actuel + Dossier Vega : zone fixe en haut (toujours visible)
  - Notebook 2 sous-onglets : 'Installation standard' (guidee, defaults
    intelligents) / 'Installation avancee' (tous les parametres editables)
  - Log : zone scrollable fixe en bas (toujours visible)

Sous-onglet Standard : port + boutons install/reinstall + choix version.
Sous-onglet Avance : Plateforme / Serveur (nom) / Port / Repertoire (libre,
parcourir) / Machine (nom) / CCHF on/off / MAJ (install/update/uninstall)
+ bouton 'Lancer installation avancee'.
"""
import socket
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from vega_backend import is_admin
from vega_backend.hfsql_installer import (
    DEFAULT_HFSQL_PORT,
    DEFAULT_HFSQL_VERSION,
    HFSQL_INSTALLERS,
)

from ..theme import (
    ACCENT_BLUE,
    APP_TITLE,
    ERROR_COLOR,
    MUTED_TEXT,
    OK_COLOR,
    PANEL_BG,
    WARN_COLOR,
    WHITE_BG,
    WINDOW_BG,
)
from ..widgets import info_panel, tool_button, value_cell


class HfsqlTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=8)
        self.app = app
        self.columnconfigure(0, weight=1)

        # Etat detection
        self._candidates = []
        self._selected_candidate = tk.StringVar()
        self._port_var = tk.StringVar(value=str(DEFAULT_HFSQL_PORT))
        self._version_var = tk.StringVar(value=DEFAULT_HFSQL_VERSION)
        self._install_info = None  # dict retourne par detect_install()

        self._build_header()
        self._build_status_panel()
        self._build_vega_panel()
        # Notebook avec 2 sous-onglets : Standard / Avance
        self._notebook = ttk.Notebook(self)
        self._notebook.grid(row=3, column=0, sticky="nsew", pady=(0, 8))
        self.rowconfigure(3, weight=0)  # le notebook fait sa taille naturelle
        self._build_standard_subtab()
        self._build_advanced_subtab()
        self._build_log_panel()

        # Premier refresh auto
        self.after(200, self.refresh_all)

    # ---------- Layout ----------

    def _build_header(self):
        # Pas de header dedie : on utilise les titres des LabelFrames pour
        # rester coherent avec les autres onglets (Migration, Pare-feu, etc).
        pass

    def _build_status_panel(self):
        panel = tk.LabelFrame(
            self, text="État de l'installation HFSQL", padx=8, pady=6, bg=WINDOW_BG,
        )
        panel.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        panel.columnconfigure(1, weight=1)

        tk.Label(panel, text="HFSQL Serveur :", bg=WINDOW_BG, font=("Tahoma", 9),
                 ).grid(row=0, column=0, sticky="w", padx=(0, 12))
        self._status_badge_var = tk.StringVar(value="Vérification...")
        self._status_badge = value_cell(panel, textvariable=self._status_badge_var,
                                         width=46, anchor="w")
        self._status_badge.grid(row=0, column=1, sticky="ew")
        # Bouton "Verifier" : controle cible de la presence d'un moteur HFSQL
        # (service Windows Hyper File Server + port en ecoute), sans relancer
        # tout le scan des dossiers Vega comme le fait "Actualiser".
        self._verify_btn = tool_button(
            panel, text="Vérifier", command=self.on_verify_engine,
            image=self.app.icons.get("verifier"), width=12,
        )
        self._verify_btn.grid(row=0, column=2, sticky="e", padx=(8, 0))

    def _build_vega_panel(self):
        panel = tk.LabelFrame(
            self, text="Dossier Vega cible (répertoire serveur HFSQL)",
            padx=8, pady=6, bg=WINDOW_BG,
        )
        panel.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        panel.columnconfigure(1, weight=1)

        tk.Label(panel, text="Répertoire :", bg=WINDOW_BG, font=("Tahoma", 9),
                 ).grid(row=0, column=0, sticky="w", padx=(0, 12))
        path_label = value_cell(panel, textvariable=self._selected_candidate,
                                width=60, anchor="w")
        path_label.grid(row=0, column=1, sticky="ew")
        # Bouton "Bases" : menu deroulant des racines Vega detectees (comme dans
        # Migration / Nettoyage). Choisir une racine la definit comme repertoire
        # serveur HFSQL cible.
        tool_button(panel, text="Bases ▾", command=self.show_bases_menu,
                    image=self.app.icons.get("database"), width=10,
                    ).grid(row=0, column=2, sticky="e", padx=(8, 0))
        tool_button(panel, text="Changer...", command=self._on_change_vega_root,
                    image=self.app.icons.get("parcourir"), width=13,
                    ).grid(row=0, column=3, sticky="e", padx=(8, 0))

        self._vega_status_var = tk.StringVar(value="Recherche en cours...")
        tk.Label(
            panel, textvariable=self._vega_status_var,
            bg=WINDOW_BG, fg=MUTED_TEXT, font=("Tahoma", 8),
            anchor="w", justify="left", wraplength=820,
        ).grid(row=1, column=0, columnspan=3, sticky="ew", pady=(4, 0))

    def _build_standard_subtab(self):
        # Sous-onglet 'Installation standard' : parametres guides, defaults
        # intelligents. Le user a juste a cliquer Installer ou Reinstaller.
        panel = tk.Frame(self._notebook, bg=WINDOW_BG, padx=10, pady=8)
        self._notebook.add(panel, text="  Installation standard  ")
        panel.columnconfigure(2, weight=1)

        # Bandeau explicatif (style info_panel)
        info_panel(
            panel,
            "Configuration automatique : le dossier choisi ci-dessus est utilisé "
            "comme répertoire serveur, et le Centre de Contrôle est toujours installé.",
            wraplength=820,
        ).grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 8))

        # Port
        tk.Label(panel, text="Port HFSQL :", bg=WINDOW_BG, font=("Tahoma", 9),
                 ).grid(row=1, column=0, sticky="w", pady=2)
        tk.Entry(panel, textvariable=self._port_var, width=8, font=("Tahoma", 9),
                 ).grid(row=1, column=1, sticky="w", padx=(8, 0), pady=2)
        tk.Label(panel, text=f"(défaut : {DEFAULT_HFSQL_PORT}, conseillé tel quel)",
                 bg=WINDOW_BG, fg=MUTED_TEXT, font=("Tahoma", 8),
                 ).grid(row=1, column=2, sticky="w", padx=(8, 0), pady=2)

        # Version installeur (radios)
        tk.Label(panel, text="Version :", bg=WINDOW_BG, font=("Tahoma", 9),
                 ).grid(row=2, column=0, sticky="w", pady=2)
        ver_row = tk.Frame(panel, bg=WINDOW_BG)
        ver_row.grid(row=2, column=1, columnspan=2, sticky="w", padx=(8, 0), pady=2)
        for v in sorted(HFSQL_INSTALLERS.keys(), reverse=True):
            label = f"WinDev {v}"
            if v == DEFAULT_HFSQL_VERSION:
                label += " (recommandé)"
            tk.Radiobutton(
                ver_row, text=label, value=v, variable=self._version_var,
                bg=WINDOW_BG, activebackground=WINDOW_BG, font=("Tahoma", 9),
            ).pack(side="left", padx=(0, 12))

        # Boutons action
        actions = tk.Frame(panel, bg=WINDOW_BG)
        actions.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        self._install_btn = tool_button(
            actions, text="Installer HFSQL", command=self.on_install,
            image=self.app.icons.get("lancer"), anchor="center",
            state="disabled",
        )
        self._install_btn.pack(side="left", padx=(0, 6))

        self._update_btn = tool_button(
            actions, text="Mettre à jour HFSQL", command=self.on_update,
            image=self.app.icons.get("actualiser"), anchor="center",
            state="disabled",
        )
        self._update_btn.pack(side="left", padx=(0, 6))

        self._uninstall_btn = tool_button(
            actions, text="Désinstaller HFSQL",
            command=self.on_uninstall,
            image=self.app.icons.get("bin"), anchor="center",
            state="disabled",
        )
        self._uninstall_btn.pack(side="left", padx=(0, 6))

        self._reinstall_btn = tool_button(
            actions, text="Désinstaller + Réinstaller",
            command=self.on_reinstall,
            image=self.app.icons.get("reparer"), anchor="center",
            state="disabled",
        )
        self._reinstall_btn.pack(side="left", padx=(0, 6))

        self._refresh_btn = tool_button(
            actions, text="Actualiser", command=self.refresh_all,
            image=self.app.icons.get("actualiser"), anchor="center",
        )
        self._refresh_btn.pack(side="left", padx=(0, 6))

    def _build_advanced_subtab(self):
        # Sous-onglet 'Installation avancee' : TOUS les champs editables.
        # Pour les utilisateurs avances qui veulent customiser le .INI.
        panel = tk.Frame(self._notebook, bg=WINDOW_BG, padx=10, pady=8)
        self._notebook.add(panel, text="  Installation avancée  ")
        panel.columnconfigure(1, weight=1)

        # Bandeau d'avertissement en ROUGE : signaler clairement que ce mode
        # n'est pas a utiliser a la legere (manipulation directe des params INI).
        warn_frame = tk.Frame(panel, bg=PANEL_BG, bd=1, relief="sunken", padx=8, pady=7)
        warn_frame.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 8))
        tk.Label(
            warn_frame,
            text="⚠ Mode avancé : tous les paramètres du fichier .INI de l'installeur "
                 "sont éditables. Réservé aux administrateurs expérimentés — "
                 "une mauvaise configuration peut empêcher HFSQL de démarrer.",
            bg=PANEL_BG, fg=ERROR_COLOR,
            justify="left", wraplength=820, font=("Tahoma", 9, "bold"),
        ).pack(anchor="w")

        # Defaults : hostname courant
        try:
            default_host = socket.gethostname()
        except Exception:
            default_host = "SERVEUR"

        # StringVars / BooleanVars du mode avance
        self._adv_plateforme = tk.IntVar(value=1)  # 1=Windows
        self._adv_maj = tk.IntVar(value=2)  # 2=install
        self._adv_cchf = tk.BooleanVar(value=True)
        self._adv_serveur = tk.StringVar(value=default_host)
        self._adv_machine = tk.StringVar(value=default_host)
        self._adv_port = tk.StringVar(value=str(DEFAULT_HFSQL_PORT))
        self._adv_repertoire = tk.StringVar(value="")
        self._adv_version = tk.StringVar(value=DEFAULT_HFSQL_VERSION)

        row = 1

        # Plateforme
        tk.Label(panel, text="Plateforme :", bg=WINDOW_BG, font=("Tahoma", 9),
                 ).grid(row=row, column=0, sticky="w", pady=2)
        plat_row = tk.Frame(panel, bg=WINDOW_BG)
        plat_row.grid(row=row, column=1, columnspan=2, sticky="w", padx=(8, 0), pady=2)
        # Plateforme : Windows local uniquement. Les options 'Windows distant'
        # et 'Linux' du pack PCSoft ne sont pas pertinentes ici (mode support).
        for val, lbl in [(1, "Windows")]:
            tk.Radiobutton(plat_row, text=lbl, value=val, variable=self._adv_plateforme,
                           bg=WINDOW_BG, activebackground=WINDOW_BG,
                           font=("Tahoma", 9)).pack(side="left", padx=(0, 12))
        row += 1

        # MAJ (mode operation)
        tk.Label(panel, text="Opération :", bg=WINDOW_BG, font=("Tahoma", 9),
                 ).grid(row=row, column=0, sticky="w", pady=2)
        maj_row = tk.Frame(panel, bg=WINDOW_BG)
        maj_row.grid(row=row, column=1, columnspan=2, sticky="w", padx=(8, 0), pady=2)
        for val, lbl in [(2, "Installation"), (1, "Mise à jour"), (3, "Désinstallation")]:
            tk.Radiobutton(maj_row, text=lbl, value=val, variable=self._adv_maj,
                           bg=WINDOW_BG, activebackground=WINDOW_BG,
                           font=("Tahoma", 9)).pack(side="left", padx=(0, 12))
        row += 1

        # Nom serveur
        tk.Label(panel, text="Nom du serveur :", bg=WINDOW_BG, font=("Tahoma", 9),
                 ).grid(row=row, column=0, sticky="w", pady=2)
        tk.Entry(panel, textvariable=self._adv_serveur, width=30,
                 font=("Tahoma", 9)).grid(row=row, column=1, sticky="w", padx=(8, 0), pady=2)
        row += 1

        # Nom machine
        tk.Label(panel, text="Nom de la machine :", bg=WINDOW_BG, font=("Tahoma", 9),
                 ).grid(row=row, column=0, sticky="w", pady=2)
        tk.Entry(panel, textvariable=self._adv_machine, width=30,
                 font=("Tahoma", 9)).grid(row=row, column=1, sticky="w", padx=(8, 0), pady=2)
        row += 1

        # Port
        tk.Label(panel, text="Port :", bg=WINDOW_BG, font=("Tahoma", 9),
                 ).grid(row=row, column=0, sticky="w", pady=2)
        tk.Entry(panel, textvariable=self._adv_port, width=8,
                 font=("Tahoma", 9)).grid(row=row, column=1, sticky="w", padx=(8, 0), pady=2)
        row += 1

        # Repertoire (libre + parcourir)
        tk.Label(panel, text="Répertoire serveur :", bg=WINDOW_BG, font=("Tahoma", 9),
                 ).grid(row=row, column=0, sticky="w", pady=2)
        rep_row = tk.Frame(panel, bg=WINDOW_BG)
        rep_row.grid(row=row, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=2)
        rep_row.columnconfigure(0, weight=1)
        tk.Entry(rep_row, textvariable=self._adv_repertoire,
                 font=("Tahoma", 9)).grid(row=0, column=0, sticky="ew")
        tk.Button(rep_row, text="Parcourir…",
                  command=self._browse_repertoire,
                  font=("Segoe UI", 8),
                  ).grid(row=0, column=1, padx=(6, 0))
        row += 1

        # CCHF
        tk.Checkbutton(
            panel, text="Installer le Centre de Contrôle HFSQL",
            variable=self._adv_cchf, bg=WINDOW_BG, activebackground=WINDOW_BG,
            font=("Tahoma", 9),
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(6, 2))
        row += 1

        # Version installeur
        tk.Label(panel, text="Version installeur :", bg=WINDOW_BG, font=("Tahoma", 9),
                 ).grid(row=row, column=0, sticky="w", pady=2)
        ver_row = tk.Frame(panel, bg=WINDOW_BG)
        ver_row.grid(row=row, column=1, columnspan=2, sticky="w", padx=(8, 0), pady=2)
        for v in sorted(HFSQL_INSTALLERS.keys(), reverse=True):
            tk.Radiobutton(ver_row, text=f"WinDev {v}", value=v,
                           variable=self._adv_version,
                           bg=WINDOW_BG, activebackground=WINDOW_BG,
                           font=("Tahoma", 9)).pack(side="left", padx=(0, 12))
        row += 1

        # Boutons : Pre-remplir depuis Standard + Lancer
        actions = tk.Frame(panel, bg=WINDOW_BG)
        actions.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(14, 0))
        tool_button(
            actions, text="Pré-remplir avec les valeurs standard",
            command=self._prefill_advanced, anchor="center",
        ).pack(side="left", padx=(0, 6))
        self._advanced_run_btn = tool_button(
            actions, text="Lancer l'installation avancée",
            command=self.on_advanced_run,
            image=self.app.icons.get("lancer"), anchor="center",
        )
        self._advanced_run_btn.pack(side="left", padx=(0, 6))
        # Met a jour le libelle quand l'utilisateur change le mode :
        # 1=Mise a jour, 2=Installation, 3=Desinstallation.
        self._adv_maj.trace_add("write", self._refresh_advanced_button_label)
        self._refresh_advanced_button_label()

    def _refresh_advanced_button_label(self, *_args):
        # Mode -> libelle bouton
        labels = {
            1: "Lancer la mise à jour",
            2: "Lancer l'installation avancée",
            3: "Lancer la désinstallation",
        }
        try:
            self._advanced_run_btn.configure(text=labels.get(self._adv_maj.get(), "Lancer"))
        except Exception:
            pass

    def _browse_repertoire(self):
        # Selection libre du dossier d'install serveur (mode avance)
        initial = self._adv_repertoire.get().strip() or self._selected_candidate.get() or "C:\\"
        if not Path(initial).is_dir():
            initial = "C:\\"
        chosen = filedialog.askdirectory(
            parent=self, title="Répertoire serveur HFSQL", initialdir=initial,
        )
        if chosen:
            self._adv_repertoire.set(chosen)

    def _prefill_advanced(self):
        # Recopie les valeurs du mode standard dans le mode avance.
        self._adv_port.set(self._port_var.get())
        self._adv_version.set(self._version_var.get())
        if self._selected_candidate.get():
            self._adv_repertoire.set(self._selected_candidate.get())
        try:
            host = socket.gethostname()
            self._adv_serveur.set(host)
            self._adv_machine.set(host)
        except Exception:
            pass
        self._adv_plateforme.set(1)
        self._adv_maj.set(2)
        self._adv_cchf.set(True)
        self.app.logger.info("Champs avancés pré-remplis depuis les valeurs standard.")

    def _build_log_panel(self):
        # Panneau journal local SUPPRIME : le journal global (tiroir bas)
        # capture deja toutes les lignes via le ToolLogger sink, evite la
        # duplication. Cette methode est gardee pour compat mais ne fait rien.
        return

    def show_bases_menu(self):
        # Menu deroulant des racines Vega detectees (self._candidates, alimente
        # par refresh_all -> scan_vega_roots). Choisir une racine la definit
        # comme repertoire serveur HFSQL. Si le scan n'a pas encore tourne, on
        # relance une detection puis on ouvre le menu.
        valid = [c for c in (self._candidates or []) if c.get("valid")]
        if not valid:
            self._vega_status_var.set("Recherche des bases Vega en cours...")

            def task():
                return self.app.hfsql_installer.scan_vega_roots()

            def on_success(candidates):
                self._candidates = candidates or []
                self._apply_candidates(self._candidates)
                fresh = [c for c in self._candidates if c.get("valid")]
                if fresh:
                    self._popup_bases_menu(fresh)
                else:
                    self._vega_status_var.set(
                        "Aucune base Vega détectée. Utilisez Changer… pour choisir manuellement."
                    )

            self.app.run_task(
                "Détection bases HFSQL", task,
                on_success=on_success, key="hfsql_detect",
            )
            return
        self._popup_bases_menu(valid)

    def _popup_bases_menu(self, valid):
        current = self._selected_candidate.get().strip()
        menu = tk.Menu(self, tearoff=0)
        for c in valid:
            path = c["path"]
            bases = ", ".join(c.get("bases") or []) or "aucune base"
            label = f"{c.get('name') or path} ({path}) — {bases}"
            if path == current:
                label = "✓ " + label
            menu.add_command(
                label=label,
                command=lambda p=path: self._select_base(p),
            )
        try:
            menu.tk_popup(self.winfo_pointerx(), self.winfo_pointery())
        finally:
            menu.grab_release()

    def _select_base(self, path):
        self._selected_candidate.set(path)
        self._vega_status_var.set("Répertoire serveur choisi via « Bases ».")
        self._set_buttons_enabled(True)

    def _on_change_vega_root(self):
        # Permet a l'utilisateur de choisir un autre dossier que celui auto-detecte.
        initial = self._selected_candidate.get() or "C:\\"
        if not Path(initial).is_dir():
            initial = "C:\\"
        chosen = filedialog.askdirectory(
            parent=self, title="Choisir le dossier Vega (VEGAHF ou VEGACS)",
            initialdir=initial,
        )
        if chosen:
            self._selected_candidate.set(chosen)
            self._set_buttons_enabled(True)
            self._vega_status_var.set(f"Répertoire personnalisé choisi.")

    # ---------- Refresh ----------

    def refresh_all(self):
        # Marque le statut 'recherche en cours' systematiquement, pour pas
        # garder un vieux 'Recherche en cours...' si le precedent refresh
        # avait crash silencieusement.
        self._vega_status_var.set("Recherche en cours...")
        self.app.logger.info("Recherche du moteur HFSQL et des dossiers Vega...")
        self._set_buttons_enabled(False)

        def task():
            # Un seul scan_vega_roots() reutilise pour detect_install et
            # pour la liste candidats : evite 2x le cout (PowerShell + iter
            # de toutes les lettres de disques).
            candidates = self.app.hfsql_installer.scan_vega_roots()
            info = self.app.hfsql_installer.detect_install(vega_candidates=candidates)
            return {"info": info, "candidates": candidates}

        def on_success(result):
            self._install_info = result["info"]
            self._candidates = result["candidates"]
            self._apply_install_info(result["info"])
            self._apply_candidates(result["candidates"])
            self._set_buttons_enabled(True)

        def on_error(exc):
            self.app.logger.error(f"Erreur de détection : {exc}")
            # On reset le status text pour pas laisser 'Recherche en cours'
            # coince a l'infini si la detection a echoue.
            self._vega_status_var.set(f"Erreur de détection : {exc}")
            self._set_buttons_enabled(True)

        self.app.run_task(
            "Détection HFSQL", task,
            on_success=on_success, on_error=on_error,
            key="hfsql_detect",
        )

    def on_verify_engine(self):
        # Verification cible : y a-t-il un moteur HFSQL (service Hyper File
        # Server) sur ce poste ? On liste toutes les instances + leurs ports.
        self._status_badge_var.set("Vérification du moteur HFSQL...")
        self._status_badge.configure(fg=MUTED_TEXT)
        self._verify_btn.configure(state="disabled")
        self.app.logger.info("Vérification de la présence d'un moteur HFSQL...")

        def task():
            return self.app.hfsql_installer.detect_all_hfsql_instances()

        def on_success(instances):
            instances = instances or []
            self._verify_btn.configure(state="normal")
            if not instances:
                self._status_badge_var.set("✕ Aucun moteur HFSQL détecté sur ce poste")
                self._status_badge.configure(fg=ERROR_COLOR)
                self.app.logger.warn("Aucun moteur HFSQL détecté (service absent).")
                return
            # Un ou plusieurs moteurs trouves : on resume dans le badge + log.
            running = [i for i in instances if str(i.get("status", "")).lower() == "running"]
            first = instances[0]
            ports = ", ".join(str(p) for p in (first.get("ports") or [])) or "port inconnu"
            name = first.get("service_name") or first.get("display_name") or "?"
            state = "en cours" if running else "arrêté"
            if len(instances) == 1:
                self._status_badge_var.set(
                    f"✓ Moteur HFSQL détecté : {name} ({state}, {ports})"
                )
            else:
                self._status_badge_var.set(
                    f"✓ {len(instances)} moteurs HFSQL détectés ({len(running)} en cours)"
                )
            self._status_badge.configure(fg=OK_COLOR if running else WARN_COLOR)
            for inst in instances:
                iports = ", ".join(str(p) for p in (inst.get("ports") or [])) or "—"
                self.app.logger.info(
                    f"Moteur HFSQL : {inst.get('service_name') or '?'} "
                    f"({inst.get('display_name') or ''}) — "
                    f"statut {inst.get('status') or '?'} · port(s) {iports}"
                )

        def on_error(exc):
            self._verify_btn.configure(state="normal")
            self._status_badge_var.set(f"Erreur de vérification : {exc}")
            self._status_badge.configure(fg=ERROR_COLOR)
            self.app.logger.error(f"Vérification moteur HFSQL échouée : {exc}")

        self.app.run_task(
            "Vérification moteur HFSQL", task,
            on_success=on_success, on_error=on_error,
            key="hfsql_verify",
        )

    def _apply_install_info(self, info):
        # Affichage minimaliste : 1 ligne d'etat avec la version, c'est tout.
        installed = info.get("installed", False)
        version = info.get("version")
        if installed:
            if version:
                self._status_badge_var.set(f"✓ HFSQL Serveur installé — version WinDev {version}")
            else:
                self._status_badge_var.set("✓ HFSQL Serveur installé")
            self._status_badge.configure(fg=OK_COLOR)
        else:
            self._status_badge_var.set("✕ HFSQL Serveur n'est pas installé sur ce poste")
            self._status_badge.configure(fg=ERROR_COLOR)

    def _apply_candidates(self, candidates):
        valid = [c for c in candidates if c["valid"]]
        if not valid:
            # Conserve le _selected_candidate precedent si on en avait un
            # (scan vide ne doit pas ecraser un repertoire deja choisi).
            if not self._selected_candidate.get():
                self._vega_status_var.set(
                    "Aucune installation Vega trouvée. Cliquez Changer… pour en choisir un manuellement."
                )
            else:
                self._vega_status_var.set("")
            return

        if len(valid) == 1:
            self._selected_candidate.set(valid[0]["path"])
            self._vega_status_var.set("")
            return

        # Plusieurs : popup pour faire choisir le user. Si choix deja fait
        # auparavant et toujours dans la liste, on le conserve sans re-demander.
        paths = [c["path"] for c in valid]
        if self._selected_candidate.get() and self._selected_candidate.get() in paths:
            self._vega_status_var.set("")
            return
        # Pre-selectionne le 1er au cas ou le user ferme le popup
        self._selected_candidate.set(valid[0]["path"])
        self._vega_status_var.set("")
        # Skip le popup si le tutoriel est actif : la popup serait cachee
        # derriere l'overlay -topmost du tutoriel et donnerait l'impression
        # d'un freeze. Le user choisira via 'Changer...' apres le tutoriel.
        if getattr(self.app, "_tutorial", None) is not None:
            self._vega_status_var.set(
                f"{len(valid)} dossiers Vega détectés (1er sélectionné). Utilisez Changer… pour basculer."
            )
            return
        # Differe le popup pour qu'il s'ouvre apres le rendu courant
        self.after(50, lambda: self._prompt_choose_candidate(valid))

    def _prompt_choose_candidate(self, valid):
        # Popup modal : radio buttons pour choisir parmi plusieurs VEGAHF/VEGACS detectes.
        dialog = tk.Toplevel(self)
        try:
            from ..resources import set_window_icon
            set_window_icon(dialog)
        except Exception:
            pass
        dialog.title("Plusieurs dossiers Vega détectés")
        dialog.transient(self.winfo_toplevel())
        dialog.resizable(False, False)

        host = ttk.Frame(dialog, padding=14)
        host.pack(fill="both", expand=True)

        tk.Label(
            host,
            text=f"{len(valid)} installations Vega ont été détectées sur ce poste.\n"
                 "Laquelle utiliser pour le serveur HFSQL ?",
            font=("Tahoma", 9), justify="left", anchor="w",
        ).pack(anchor="w", pady=(0, 10))

        choice_var = tk.StringVar(value=self._selected_candidate.get() or valid[0]["path"])
        for c in valid:
            bases = ", ".join(c["bases"]) if c.get("bases") else "(aucune base détectée)"
            tk.Radiobutton(
                host,
                text=f"{c['path']}   —   bases : {bases}",
                value=c["path"], variable=choice_var,
                font=("Tahoma", 9), anchor="w", justify="left",
            ).pack(anchor="w", pady=2)

        buttons = ttk.Frame(host)
        buttons.pack(fill="x", pady=(14, 0))

        def confirm():
            self._selected_candidate.set(choice_var.get())
            self._set_buttons_enabled(True)
            dialog.destroy()

        def cancel():
            dialog.destroy()

        tool_button(buttons, text="Valider", command=confirm, width=12, anchor="center",
                    ).pack(side="right", padx=(6, 0))
        tool_button(buttons, text="Annuler", command=cancel, width=12, anchor="center",
                    ).pack(side="right")

        dialog.protocol("WM_DELETE_WINDOW", cancel)
        dialog.bind("<Return>", lambda _e: confirm())
        dialog.bind("<Escape>", lambda _e: cancel())

        dialog.update_idletasks()
        parent = self.winfo_toplevel()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        dw, dh = dialog.winfo_width(), dialog.winfo_height()
        dialog.geometry(f"+{px + max((pw - dw) // 2, 0)}+{py + max((ph - dh) // 2, 0)}")
        dialog.grab_set()
        dialog.focus_force()

    def _set_buttons_enabled(self, enabled):
        # Etat des 4 boutons d'action selon l'etat detect + presence de candidat
        if not enabled:
            self._install_btn.configure(state="disabled")
            self._update_btn.configure(state="disabled")
            self._uninstall_btn.configure(state="disabled")
            self._reinstall_btn.configure(state="disabled")
            return
        has_candidate = bool(self._selected_candidate.get()) and any(
            c["valid"] for c in self._candidates
        )
        is_installed = bool(self._install_info and self._install_info.get("installed"))
        # Installer seulement si pas deja installe
        self._install_btn.configure(
            state="normal" if (has_candidate and not is_installed) else "disabled"
        )
        # Mettre a jour seulement si deja installe ET pas deja a la derniere
        # version connue (= DEFAULT_HFSQL_VERSION). Si on est deja en 31, il
        # n'y a plus rien a mettre a jour - on grise pour eviter la confusion.
        detected_version = (self._install_info or {}).get("version")
        already_latest = detected_version and str(detected_version) == str(DEFAULT_HFSQL_VERSION)
        self._update_btn.configure(
            state="normal" if (has_candidate and is_installed and not already_latest) else "disabled"
        )
        # Desinstaller : il faut a la fois un candidat ET une install detectee.
        # Le candidat sert de Repertoire pour MAJ=3 (sinon PCSoft exit 0
        # silencieux) ET de cible pour le nettoyage des binaires orphelins.
        self._uninstall_btn.configure(
            state="normal" if (has_candidate and is_installed) else "disabled"
        )
        # Reinstaller seulement si deja installe + candidat selectionne
        self._reinstall_btn.configure(
            state="normal" if (has_candidate and is_installed) else "disabled"
        )

    # ---------- Actions ----------

    def on_install(self):
        port = self._validate_port()
        if port is None:
            return
        vega_root = self._selected_candidate.get()
        if not vega_root:
            self.app.logger.error("Aucun dossier Vega selectionné.")
            return
        version = self._version_var.get() or DEFAULT_HFSQL_VERSION

        if not is_admin():
            self.app.confirm_action(
                APP_TITLE,
                "L'installation HFSQL nécessite les droits administrateur.\n"
                "Veuillez relancer Vega Toolbox en administrateur.",
            )
            return

        if not self.app.confirm_action(
            APP_TITLE,
            f"Installer HFSQL Client/Serveur version WinDev {version} ?\n\n"
            f"  • Dossier cible : {vega_root}\n"
            f"  • Port : {port}\n"
            f"  • Centre de Contrôle : installé\n\n"
            "Le téléchargement de l'installeur (~500 Mo) peut prendre quelques minutes.\n"
            "Continuer ?",
        ):
            return

        self._set_buttons_enabled(False)
        self.app.logger.info(f"=== INSTALLATION HFSQL WinDev {version} ===")
        self.app.logger.info(f"Dossier : {vega_root}")
        self.app.logger.info(f"Port : {port}")

        def on_progress(_step, message):
            self.after(0, lambda m=message: self.app.logger.info(m))

        def task():
            return self.app.hfsql_installer.install(
                vega_root=vega_root, port=port, version=version,
                on_progress=on_progress,
            )

        def on_success(result):
            self._log_install_result(result, mode="install")
            self.refresh_all()
            self.app.audit.log_action(
                "hfsql_install",
                status="success" if result["verification"]["overall_ok"] else "warning",
                details={
                    "version": version, "repertoire": vega_root, "port": port,
                    "verification": result["verification"],
                },
            )

        def on_error(exc):
            self.app.logger.error(f"ECHEC : {exc}")
            self._set_buttons_enabled(True)
            self.app.audit.log_action(
                "hfsql_install", status="failure",
                details={"version": version, "repertoire": vega_root, "port": port},
                error=str(exc),
            )

        self.app.run_task(
            f"Installation HFSQL v{version}", task,
            on_success=on_success, on_error=on_error,
            key="hfsql_install",
        )

    def on_update(self):
        port = self._validate_port()
        if port is None:
            return
        vega_root = self._selected_candidate.get()
        if not vega_root:
            self.app.logger.error("Aucun dossier Vega selectionné.")
            return
        version = self._version_var.get() or DEFAULT_HFSQL_VERSION

        if not is_admin():
            self.app.confirm_action(
                APP_TITLE,
                "La mise à jour HFSQL nécessite les droits administrateur.\n"
                "Veuillez relancer Vega Toolbox en administrateur.",
            )
            return

        if not self.app.confirm_action(
            APP_TITLE,
            f"Mettre à jour HFSQL Client/Serveur vers WinDev {version} ?\n\n"
            f"  • Le service HFSQL sera momentanément arrêté\n"
            f"  • Les binaires et DLL seront remplacés sur place\n"
            f"  • Les bases de données et la configuration sont CONSERVÉES\n"
            f"  • Dossier cible : {vega_root}\n"
            f"  • Port : {port}\n\n"
            "Plus rapide qu'une réinstallation complète.\n\n"
            "Continuer ?",
        ):
            return

        self._set_buttons_enabled(False)
        self.app.logger.info(f"=== MISE A JOUR HFSQL WinDev {version} ===")
        self.app.logger.info(f"Dossier : {vega_root}")
        self.app.logger.info(f"Port : {port}")

        def on_progress(_step, message):
            self.after(0, lambda m=message: self.app.logger.info(m))

        def task():
            return self.app.hfsql_installer.update(
                vega_root=vega_root, port=port, version=version,
                on_progress=on_progress,
            )

        def on_success(result):
            self._log_install_result(result, mode="update")
            self.refresh_all()
            self.app.audit.log_action(
                "hfsql_update",
                status="success" if result["verification"]["overall_ok"] else "warning",
                details={
                    "version": version, "repertoire": vega_root, "port": port,
                    "verification": result["verification"],
                },
            )

        def on_error(exc):
            self.app.logger.error(f"ECHEC : {exc}")
            self._set_buttons_enabled(True)
            self.app.audit.log_action(
                "hfsql_update", status="failure",
                details={"version": version, "repertoire": vega_root, "port": port},
                error=str(exc),
            )

        self.app.run_task(
            f"Mise à jour HFSQL v{version}", task,
            on_success=on_success, on_error=on_error,
            key="hfsql_install",
        )

    def on_uninstall(self):
        # Desinstallation SEULE (sans reinstall). Le candidat selectionne
        # (vega_root) sert de Repertoire pour l'INI MAJ=3 — c'est important
        # car PCSoft exit 0 silencieusement si le Repertoire ne pointe pas
        # sur le vrai dossier d'install. On nettoie aussi la racine VEGAHF.
        version = self._version_var.get() or DEFAULT_HFSQL_VERSION
        vega_root = self._selected_candidate.get() or None

        if not is_admin():
            self.app.confirm_action(
                APP_TITLE,
                "La désinstallation HFSQL nécessite les droits administrateur.\n"
                "Veuillez relancer Vega Toolbox en administrateur.",
            )
            return

        # Liste TOUTES les instances HFSQL detectees + leurs ports d'ecoute,
        # affichee dans le confirm pour eviter de desinstaller par megarde un
        # autre HFSQL coexistant sur un autre port (cas client : 2 logiciels
        # metier qui ont chacun leur HFSQL).
        try:
            instances = self.app.hfsql_installer.detect_all_hfsql_instances()
        except Exception:
            instances = []

        msg = (
            f"Désinstaller HFSQL Client/Serveur WinDev {version} ?\n\n"
            "  • Le service HFSQL sera arrêté et supprimé\n"
            "  • Les binaires et DLL seront supprimés\n"
            "  • Le Centre de Contrôle HFSQL sera désinstallé\n"
        )
        if vega_root:
            msg += (
                f"  • Le dossier {vega_root} sera nettoyé\n"
                "    (tout sauf BDD, et fichiers HFSQL cachés __ dans BDD)\n"
            )
        msg += "\nLes bases de données dans BDD/ sont CONSERVÉES.\n"

        # Avertissement liste : SEULEMENT s'il y a plus d'une instance HFSQL.
        # Avec une seule instance c'est de la pollution visuelle.
        if len(instances) > 1:
            msg += (
                f"\n⚠ Plusieurs instances HFSQL détectées sur ce poste ({len(instances)}) :\n"
            )
            for inst in instances:
                ports = ", ".join(str(p) for p in (inst.get("ports") or [])) or "—"
                status = inst.get("status") or "?"
                name = inst.get("service_name") or "?"
                display = inst.get("display_name") or ""
                msg += f"  • {name}"
                if display and display != name:
                    msg += f" ({display})"
                msg += f"\n      Statut : {status} · Port(s) : {ports}\n"
            msg += (
                "\nVérifiez que vous ne désinstallez QUE l'instance Vega.\n"
            )

        msg += "\nContinuer ?"
        if not self.app.confirm_action(APP_TITLE, msg):
            return

        self._set_buttons_enabled(False)
        self.app.logger.info(f"=== DESINSTALLATION HFSQL WinDev {version} ===")

        def on_progress(_step, message):
            self.after(0, lambda m=message: self.app.logger.info(m))

        def task():
            uninstall_result = self.app.hfsql_installer.uninstall_and_purge(
                version=version, vega_root=vega_root, on_progress=on_progress,
            )
            clean_result = None
            if vega_root:
                on_progress("clean_vega", "Nettoyage du dossier Vega...")
                clean_result = self.app.hfsql_installer.clean_vega_root_after_uninstall(
                    vega_root=vega_root,
                )
            return {"uninstall": uninstall_result, "clean_vega": clean_result}

        def on_success(result):
            uninstall = result["uninstall"]
            if uninstall.get("already_clean"):
                self.app.logger.info("Aucune installation existante.")
            else:
                purged = uninstall.get("purged") or {}
                self.app.logger.info(f"Service supprimé : {purged.get('service_deleted')}")
                self.app.logger.info(f"Registre supprimé : {purged.get('registry_deleted')}")
                self.app.logger.info(f"Dossier install supprimé : {purged.get('install_dir_deleted')}")
                u_log_ok = uninstall.get("uninstall_log_ok")
                if u_log_ok is True:
                    self.app.logger.info("Install.log uninstall : OK (validé par PCSoft).")
                elif u_log_ok is False:
                    self.app.logger.warn(
                        f"Install.log uninstall : NON validé — "
                        f"contenu : {uninstall.get('uninstall_log_content')}"
                    )
            clean = result.get("clean_vega") or {}
            root_del = clean.get("root_items_deleted") or []
            bdd_del = clean.get("bdd_items_deleted") or []
            root_left = clean.get("root_items_remaining") or []
            if root_del or bdd_del or root_left:
                self.app.logger.info(
                    f"--- Nettoyage dossier Vega ({clean.get('passes', 0)} passe(s)) ---"
                )
                for item in root_del:
                    self.app.logger.info(f"Racine : {item} supprimé")
                for item in bdd_del:
                    self.app.logger.info(f"BDD/{item} (HFSQL caché) supprimé")
                for item in root_left:
                    self.app.logger.warn(
                        f"Racine : {item} N'A PAS pu être supprimé (verrouillé ?)"
                    )
            for err in (clean.get("errors") or []):
                self.app.logger.warn(f"Nettoyage Vega : {err}")
            self.refresh_all()
            self.app.audit.log_action(
                "hfsql_uninstall",
                status="success" if uninstall.get("uninstall_log_ok") else "warning",
                details={
                    "version": version, "repertoire": vega_root,
                    "uninstall": uninstall, "clean_vega": clean,
                },
            )

        def on_error(exc):
            self.app.logger.error(f"ECHEC : {exc}")
            self._set_buttons_enabled(True)
            self.app.audit.log_action(
                "hfsql_uninstall", status="failure",
                details={"version": version, "repertoire": vega_root},
                error=str(exc),
            )

        self.app.run_task(
            f"Désinstallation HFSQL v{version}", task,
            on_success=on_success, on_error=on_error,
            key="hfsql_install",
        )

    def on_reinstall(self):
        port = self._validate_port()
        if port is None:
            return
        vega_root = self._selected_candidate.get()
        if not vega_root:
            self.app.logger.error("Aucun dossier Vega selectionné.")
            return
        version = self._version_var.get() or DEFAULT_HFSQL_VERSION

        if not is_admin():
            self.app.confirm_action(
                APP_TITLE,
                "La réinstallation HFSQL nécessite les droits administrateur.\n"
                "Veuillez relancer Vega Toolbox en administrateur.",
            )
            return

        if not self.app.confirm_action(
            APP_TITLE,
            f"Désinstaller PUIS réinstaller HFSQL WinDev {version} ?\n\n"
            f"  • Le service HFSQL sera arrêté\n"
            f"  • L'installation actuelle sera SUPPRIMÉE (binaires + DLL résiduelles)\n"
            f"  • Le moteur sera réinstallé dans : {vega_root}\n"
            f"  • Port : {port}\n\n"
            "ATTENTION : les bases de données dans le dossier Vega seront conservées,\n"
            "mais toute connexion en cours sera coupée.\n\n"
            "Continuer ?",
        ):
            return

        self._set_buttons_enabled(False)
        self.app.logger.info(f"=== REINSTALLATION HFSQL WinDev {version} ===")

        def on_progress(_step, message):
            self.after(0, lambda m=message: self.app.logger.info(m))

        def task():
            # Etape 1 : desinstall + purge (vega_root passe pour que MAJ=3
            # pointe sur le vrai Repertoire — sinon PCSoft exit 0 silencieux)
            uninstall_result = self.app.hfsql_installer.uninstall_and_purge(
                version=version, vega_root=vega_root, on_progress=on_progress,
            )
            # Etape 1bis : nettoyage du dossier VEGAHF/VEGACS
            # (vide la racine sauf BDD, supprime les fichiers HFSQL caches
            # __JNL/__JNLBackup/__System/__TRS dans BDD, conserve les bases)
            on_progress("clean_vega", "Nettoyage du dossier Vega...")
            clean_result = self.app.hfsql_installer.clean_vega_root_after_uninstall(
                vega_root=vega_root,
            )
            # Etape 2 : install
            install_result = self.app.hfsql_installer.install(
                vega_root=vega_root, port=port, version=version,
                on_progress=on_progress,
            )
            return {
                "uninstall": uninstall_result,
                "clean_vega": clean_result,
                "install": install_result,
            }

        def on_success(result):
            self.app.logger.info("--- Désinstallation ---")
            uninstall = result["uninstall"]
            if uninstall.get("already_clean"):
                self.app.logger.info("Aucune installation existante.")
            else:
                purged = uninstall.get("purged") or {}
                self.app.logger.info(f"Service supprimé : {purged.get('service_deleted')}")
                self.app.logger.info(f"Registre supprimé : {purged.get('registry_deleted')}")
                self.app.logger.info(f"Dossier install supprimé : {purged.get('install_dir_deleted')}")
                for extra in purged.get("extra_dirs_deleted", []):
                    self.app.logger.info(f"Dossier résiduel supprimé : {extra}")
                u_log_ok = uninstall.get("uninstall_log_ok")
                if u_log_ok is True:
                    self.app.logger.info("Install.log uninstall : OK (validé par PCSoft).")
                elif u_log_ok is False:
                    self.app.logger.warn(
                        f"Install.log uninstall : NON validé — "
                        f"contenu : {uninstall.get('uninstall_log_content')}"
                    )
            clean = result.get("clean_vega") or {}
            root_del = clean.get("root_items_deleted") or []
            bdd_del = clean.get("bdd_items_deleted") or []
            root_left = clean.get("root_items_remaining") or []
            if root_del or bdd_del or root_left:
                self.app.logger.info(
                    f"--- Nettoyage dossier Vega ({clean.get('passes', 0)} passe(s)) ---"
                )
                for item in root_del:
                    self.app.logger.info(f"Racine : {item} supprimé")
                for item in bdd_del:
                    self.app.logger.info(f"BDD/{item} (HFSQL caché) supprimé")
                for item in root_left:
                    self.app.logger.warn(
                        f"Racine : {item} N'A PAS pu être supprimé (verrouillé ?)"
                    )
            for err in (clean.get("errors") or []):
                self.app.logger.warn(f"Nettoyage Vega : {err}")
            self.app.logger.info("--- Installation ---")
            self._log_install_result(result["install"], mode="install")
            self.refresh_all()
            verif = result["install"]["verification"]
            self.app.audit.log_action(
                "hfsql_reinstall",
                status="success" if verif["overall_ok"] else "warning",
                details={
                    "version": version, "repertoire": vega_root, "port": port,
                    "uninstall": uninstall,
                    "verification": verif,
                },
            )

        def on_error(exc):
            self.app.logger.error(f"ECHEC : {exc}")
            self._set_buttons_enabled(True)
            self.app.audit.log_action(
                "hfsql_reinstall", status="failure",
                details={"version": version, "repertoire": vega_root, "port": port},
                error=str(exc),
            )

        self.app.run_task(
            f"Réinstallation HFSQL v{version}", task,
            on_success=on_success, on_error=on_error,
            key="hfsql_install",
        )

    def on_advanced_run(self):
        # Lance l'installeur en mode avance avec tous les champs custom.
        if not is_admin():
            self.app.confirm_action(
                APP_TITLE,
                "Le mode avancé nécessite les droits administrateur.\n"
                "Veuillez relancer Vega Toolbox en administrateur.",
            )
            return

        try:
            port = int(self._adv_port.get().strip())
        except ValueError:
            self.app.logger.error("Mode avancé : port invalide.")
            return
        if not (1 <= port <= 65535):
            self.app.logger.info(f"Mode avancé : port hors plage ({port}).", error=True)
            return

        repertoire = self._adv_repertoire.get().strip()
        if not repertoire:
            self.app.logger.error("Mode avancé : répertoire manquant.")
            return

        params = {
            "plateforme": self._adv_plateforme.get(),
            "maj": self._adv_maj.get(),
            "cchf": self._adv_cchf.get(),
            "serveur": self._adv_serveur.get().strip() or "SERVEUR",
            "machine": self._adv_machine.get().strip() or "SERVEUR",
            "port": port,
            "repertoire": repertoire,
        }
        version = self._adv_version.get() or DEFAULT_HFSQL_VERSION
        maj_label = {1: "Mise à jour", 2: "Installation", 3: "Désinstallation"}.get(params["maj"], "?")

        if not self.app.confirm_action(
            APP_TITLE,
            f"Lancer l'opération HFSQL en mode AVANCÉ ?\n\n"
            f"  • Opération : {maj_label}\n"
            f"  • Version installeur : WinDev {version}\n"
            f"  • Plateforme : {params['plateforme']}\n"
            f"  • Serveur : {params['serveur']}\n"
            f"  • Machine : {params['machine']}\n"
            f"  • Port : {params['port']}\n"
            f"  • Répertoire : {params['repertoire']}\n"
            f"  • Centre de Contrôle : {'OUI' if params['cchf'] else 'NON'}\n\n"
            "Continuer ?",
        ):
            return

        self._set_buttons_enabled(False)
        self.app.logger.info(f"=== MODE AVANCÉ : {maj_label} HFSQL WinDev {version} ===")
        for k, v in params.items():
            self.app.logger.info(f"  {k} = {v}")

        def on_progress(_step, message):
            self.after(0, lambda m=message: self.app.logger.info(m))

        def task():
            return self.app.hfsql_installer.run_custom(
                ini_params=params, version=version, on_progress=on_progress,
            )

        def on_success(result):
            self._log_install_result(result, mode="advanced")
            self.refresh_all()
            self.app.audit.log_action(
                "hfsql_advanced_run",
                status="success" if result["verification"]["overall_ok"] else "warning",
                details={
                    "version": version, "operation": maj_label,
                    "params": params,
                    "verification": result["verification"],
                },
            )

        def on_error(exc):
            self.app.logger.error(f"ECHEC : {exc}")
            self._set_buttons_enabled(True)
            self.app.audit.log_action(
                "hfsql_advanced_run", status="failure",
                details={"version": version, "operation": maj_label, "params": params},
                error=str(exc),
            )

        self.app.run_task(
            f"HFSQL avancé ({maj_label})", task,
            on_success=on_success, on_error=on_error,
            key="hfsql_install",
        )

    def _validate_port(self):
        # Verifie + popup si != 4900
        raw = self._port_var.get().strip()
        try:
            port = int(raw)
        except ValueError:
            self.app.logger.error(f"Port invalide : '{raw}' n'est pas un nombre.")
            return None
        if not (1 <= port <= 65535):
            self.app.logger.info(f"Port hors plage : {port} (doit être entre 1 et 65535).", error=True)
            return None
        if port != DEFAULT_HFSQL_PORT:
            confirm = self.app.confirm_action(
                APP_TITLE,
                f"Le port standard HFSQL est {DEFAULT_HFSQL_PORT}.\n"
                f"Vous avez choisi {port}.\n\n"
                f"Êtes-vous sûr de vouloir utiliser ce port ?\n"
                "(Si non, cliquez Annuler et remettez 4900.)",
            )
            if not confirm:
                return None
        return port

    def _log_install_result(self, result, mode="install"):
        verif = result.get("verification", {})
        rc = result.get("return_code")
        self.app.logger.info(f"Code retour installeur : {rc}")
        self.app.logger.info(f"Journal : {result.get('log_path')}")
        # Install.log dans le Repertoire = signal informatif PCSoft.
        # Pas de warning si le service tourne quand meme : ca veut dire que
        # l'install a marche, le fichier est juste absent / dans un autre
        # chemin / format inattendu.
        if verif.get("install_log_present"):
            if verif.get("install_log_ok"):
                self.app.logger.info("Install.log PCSoft : OK (validé)")
            else:
                self.app.logger.info(
                    f"Install.log PCSoft : contenu non standard — "
                    f"{verif.get('install_log_content')}"
                )
        else:
            self.app.logger.info("Install.log PCSoft : absent (non bloquant)")
        self.app.logger.info(
            f"Service Windows : {'OK' if verif.get('service_ok') else 'KO'} "
            f"({verif.get('service_name') or '—'})"
        )
        self.app.logger.info(
            f"Port {result.get('port')} accessible : "
            f"{'OK' if verif.get('port_ok') else 'KO'}"
        )
        if verif.get("log_errors"):
            self.app.logger.info("Erreurs dans le log :")
            for line in verif["log_errors"]:
                self.app.logger.info(f"  - {line}")
        if verif.get("overall_ok"):
            self.app.logger.info("=== INSTALLATION RÉUSSIE ===")
        else:
            self.app.logger.warn("=== INSTALLATION TERMINÉE AVEC AVERTISSEMENTS — voir détails ci-dessus ===")

