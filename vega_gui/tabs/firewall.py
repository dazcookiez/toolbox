import tkinter as tk
from tkinter import ttk

from ..theme import ACCENT_BLUE, APP_TITLE, ERROR_COLOR, OK_COLOR, WARN_COLOR, WINDOW_BG
from ..widgets import info_panel, tool_button, value_cell


class FirewallTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=8)
        self.app = app
        self.retailforce_labels = {}
        self.hfsql_labels = {}
        self.summary_labels = {}
        self.summary_var = tk.StringVar(value="À vérifier")
        self.build_ui()

    def build_ui(self):
        # Mise en page compacte pour garder toutes les infos visibles.
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)

        toolbar = tk.LabelFrame(self, text="Actions pare-feu", padx=8, pady=6, bg=WINDOW_BG)
        toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self.toolbar = toolbar

        tool_button(
            toolbar,
            text="Vérifier",
            command=self.refresh_status,
            image=self.app.icons.get("verifier"),
        ).pack(side="left", padx=(0, 8))
        tool_button(
            toolbar,
            text="Créer / réparer",
            command=self.create_or_repair,
            image=self.app.icons.get("reparer"),
        ).pack(side="left")

        retailforce_frame = tk.LabelFrame(self, text="Service Fiscal RetailForce", padx=8, pady=6, bg=WINDOW_BG)
        retailforce_frame.grid(row=1, column=0, sticky="nsew")
        retailforce_frame.columnconfigure(1, weight=1)

        rows = [
            ("Règle présente", "rule_present"),
            ("Nom affiché correct", "display_name_ok"),
            ("Règle activée", "enabled_ok"),
            ("Direction entrante", "direction_ok"),
            ("Action autoriser", "action_ok"),
            ("Protocole TCP", "protocol_ok"),
            ("Port local 7678", "port_ok"),
            ("Profils Domaine/Privé/Public", "profiles_ok"),
        ]
        for index, (label, key) in enumerate(rows):
            tk.Label(retailforce_frame, text=label, bg=WINDOW_BG).grid(row=index, column=0, sticky="w", pady=(0, 4))
            value = value_cell(retailforce_frame, text="À vérifier", width=15)
            value.grid(row=index, column=1, sticky="ew", padx=(12, 0), pady=(0, 4))
            self.retailforce_labels[key] = value

        hfsql_frame = tk.LabelFrame(self, text="HFSQL Server", padx=8, pady=6, bg=WINDOW_BG)
        hfsql_frame.grid(row=1, column=1, sticky="nsew", padx=(10, 0))
        hfsql_frame.columnconfigure(1, weight=1)

        hfsql_rows = [
            ("Port TCP 4900 ouvert", "hfsql_port_open"),
            ("Profils Domaine/Privé/Public", "hfsql_profiles_ok"),
        ]
        for index, (label, key) in enumerate(hfsql_rows):
            tk.Label(hfsql_frame, text=label, bg=WINDOW_BG).grid(row=index, column=0, sticky="w", pady=(0, 4))
            value = value_cell(hfsql_frame, text="À vérifier", width=15)
            value.grid(row=index, column=1, sticky="ew", padx=(12, 0), pady=(0, 4))
            self.hfsql_labels[key] = value

        tk.Label(hfsql_frame, text="Règle créée si besoin", bg=WINDOW_BG, font=("Tahoma", 8, "bold")).grid(
            row=2,
            column=0,
            sticky="w",
            pady=(8, 0),
        )
        value_cell(hfsql_frame, text="HFSQL Server", width=18, anchor="w").grid(
            row=3,
            column=0,
            sticky="w",
            pady=(4, 8),
        )

        tk.Label(hfsql_frame, text="Configuration attendue", bg=WINDOW_BG, font=("Tahoma", 8, "bold")).grid(
            row=2,
            column=1,
            sticky="w",
            padx=(12, 0),
            pady=(8, 0),
        )
        value_cell(hfsql_frame, text="TCP 4900 - 3 profils", width=18, anchor="w").grid(
            row=3,
            column=1,
            sticky="w",
            padx=(12, 0),
            pady=(4, 8),
        )

        info_panel(
            hfsql_frame,
            "Si le port 4900 n'est pas ouvert sur les 3 profils (Domaine, Privé, Public), l'outil crée la règle HFSQL Server entrante couvrant les 3.",
            wraplength=360,
        ).grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))

        summary_frame = tk.LabelFrame(self, text="Résumé", padx=8, pady=6, bg=WINDOW_BG)
        summary_frame.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        summary_frame.columnconfigure(1, weight=1)

        summary_rows = [
            ("RetailForce 7678", "retailforce"),
            ("HFSQL 4900", "hfsql"),
            ("État général", "overall"),
        ]
        for index, (label, key) in enumerate(summary_rows):
            tk.Label(summary_frame, text=label, bg=WINDOW_BG, font=("Tahoma", 8, "bold")).grid(
                row=index,
                column=0,
                sticky="w",
                pady=(0, 8) if index < 2 else (0, 0),
            )
            value = value_cell(
                summary_frame,
                text="À vérifier" if key != "overall" else "",
                textvariable=self.summary_var if key == "overall" else None,
                width=18,
                font=("Tahoma", 10, "bold") if key == "overall" else ("Tahoma", 9),
            )
            value.grid(
                row=index,
                column=1,
                sticky="ew",
                padx=(14, 0),
                pady=(0, 8) if index < 2 else (0, 0),
            )
            self.summary_labels[key] = value

        self.summary_label = self.summary_labels["overall"]
        self.summary_label.configure(fg=ACCENT_BLUE)

    def refresh_status(self):
        self.app.run_task(
            "Vérification des règles pare-feu",
            self.app.firewall.check_status,
            on_success=self.apply_status,
            clear_display=False,
        )

    def apply_status(self, status):
        retailforce_ok = True
        for key in [
            "rule_present",
            "display_name_ok",
            "enabled_ok",
            "direction_ok",
            "action_ok",
            "protocol_ok",
            "port_ok",
            "profiles_ok",
        ]:
            label = self.retailforce_labels[key]
            if status.get(key):
                label.configure(text="OK", fg=OK_COLOR)
            else:
                label.configure(text="NON", fg=ERROR_COLOR)
                retailforce_ok = False

        hfsql_port = bool(status.get("hfsql_port_open"))
        hfsql_profiles = bool(status.get("hfsql_profiles_ok"))
        for key, ok in (("hfsql_port_open", hfsql_port), ("hfsql_profiles_ok", hfsql_profiles)):
            label = self.hfsql_labels[key]
            if ok:
                label.configure(text="OK", fg=OK_COLOR)
            else:
                label.configure(text="NON", fg=ERROR_COLOR)
        hfsql_ok = hfsql_port and hfsql_profiles

        self.summary_labels["retailforce"].configure(
            text="Conforme" if retailforce_ok else "À corriger",
            fg=OK_COLOR if retailforce_ok else WARN_COLOR,
        )
        self.summary_labels["hfsql"].configure(
            text="Conforme" if hfsql_ok else "À corriger",
            fg=OK_COLOR if hfsql_ok else WARN_COLOR,
        )

        if retailforce_ok and hfsql_ok:
            self.summary_var.set("Conforme")
            self.summary_label.configure(fg=OK_COLOR)
            self.app.set_status("Vérification pare-feu OK.", OK_COLOR)
        else:
            self.summary_var.set("À corriger")
            self.summary_label.configure(fg=WARN_COLOR)
            self.app.set_status("Vérification pare-feu terminée avec écarts.", WARN_COLOR)

    def create_or_repair(self):
        if not self.app.confirm_action(
            APP_TITLE,
            "Créer ou réparer les règles pare-feu Service Fiscal RetailForce (TCP 7678) et HFSQL Server (TCP 4900) ?",
        ):
            return

        def on_success(status):
            self.apply_status(status)
            self.app.set_status("Règles pare-feu vérifiées / réparées.", OK_COLOR)
            self.app.audit.log_action(
                "firewall_rules_create_or_repair",
                status="success",
                details={
                    "retailforce_ok": bool((status or {}).get("retailforce", {}).get("ok")),
                    "hfsql_ok": bool((status or {}).get("hfsql", {}).get("ok")),
                },
            )

        self.app.run_task(
            "Création / réparation des règles pare-feu",
            self.app.firewall.create_or_repair,
            on_success=on_success,
            clear_display=True,
        )
