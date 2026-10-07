import tkinter as tk
from tkinter import ttk

from ..theme import ACCENT_BLUE, APP_TITLE, ERROR_COLOR, OK_COLOR, WARN_COLOR, WINDOW_BG
from ..widgets import tool_button, value_cell


class ImpressionsTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=8)
        self.app = app
        self.value_labels = {}
        self.summary_var = tk.StringVar(value="À vérifier")
        self.build_ui()

    def build_ui(self):
        self.columnconfigure(0, weight=3)
        self.columnconfigure(1, weight=2)
        self.rowconfigure(1, weight=1)

        toolbar = tk.LabelFrame(self, text="Actions Impressions Vega", padx=8, pady=6, bg=WINDOW_BG)
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

        status_frame = tk.LabelFrame(self, text="État de C:\\ImpressionsVega", padx=8, pady=6, bg=WINDOW_BG)
        status_frame.grid(row=1, column=0, sticky="nsew")
        status_frame.columnconfigure(1, weight=1)

        rows = [
            ("Dossier présent", "folder_exists"),
            ("Partage présent", "share_exists"),
            ("Chemin du partage correct", "share_path_ok"),
            ("Partage Tout le monde : contrôle total", "share_everyone_full"),
            ("Droits NTFS Tout le monde : contrôle total", "ntfs_everyone_full"),
        ]
        for index, (label, key) in enumerate(rows):
            tk.Label(status_frame, text=label, bg=WINDOW_BG).grid(row=index, column=0, sticky="w", pady=(0, 4))
            value = value_cell(status_frame, text="À vérifier", width=18)
            value.grid(row=index, column=1, sticky="w", padx=(12, 0), pady=(0, 4))
            self.value_labels[key] = value

        summary_frame = tk.LabelFrame(self, text="Résumé", padx=8, pady=6, bg=WINDOW_BG)
        summary_frame.grid(row=1, column=1, sticky="nsew", padx=(10, 0))
        summary_frame.columnconfigure(0, weight=1)

        tk.Label(summary_frame, text="Partage attendu", bg=WINDOW_BG, font=("Tahoma", 8, "bold")).grid(
            row=0,
            column=0,
            sticky="w",
        )
        value_cell(summary_frame, text="ImpressionsVega$", width=24).grid(row=1, column=0, sticky="ew", pady=(4, 12))

        tk.Label(summary_frame, text="Chemin attendu", bg=WINDOW_BG, font=("Tahoma", 8, "bold")).grid(
            row=2,
            column=0,
            sticky="w",
        )
        value_cell(summary_frame, text=r"C:\ImpressionsVega", width=24).grid(row=3, column=0, sticky="ew", pady=(4, 12))

        tk.Label(summary_frame, text="État général", bg=WINDOW_BG, font=("Tahoma", 8, "bold")).grid(
            row=4,
            column=0,
            sticky="w",
        )
        self.summary_label = value_cell(
            summary_frame,
            textvariable=self.summary_var,
            width=24,
            font=("Tahoma", 10, "bold"),
        )
        self.summary_label.grid(row=5, column=0, sticky="ew", pady=(4, 0))
        self.summary_label.configure(fg=ACCENT_BLUE)

    def refresh_status(self):
        self.app.run_task(
            "Vérification du module Impressions Vega",
            self.app.impressions.check_status,
            on_success=self.apply_status,
            clear_display=False,
        )

    def apply_status(self, status):
        all_ok = True
        for key in [
            "folder_exists",
            "share_exists",
            "share_path_ok",
            "share_everyone_full",
            "ntfs_everyone_full",
        ]:
            label = self.value_labels[key]
            if status[key]:
                label.configure(text="OK", fg=OK_COLOR)
            else:
                label.configure(text="NON", fg=ERROR_COLOR)
                all_ok = False
        if all_ok:
            self.summary_var.set("Conforme")
            self.summary_label.configure(fg=OK_COLOR)
            self.app.set_status("Vérification Impressions Vega OK.", OK_COLOR)
        else:
            self.summary_var.set("À corriger")
            self.summary_label.configure(fg=WARN_COLOR)
            self.app.set_status("Vérification Impressions Vega terminée avec écarts.", WARN_COLOR)

    def create_or_repair(self):
        if not self.app.confirm_action(APP_TITLE, "Créer ou réparer C:\\ImpressionsVega et le partage ImpressionsVega$ ?"):
            return

        def on_success(status):
            self.apply_status(status)
            self.app.set_status("Impressions Vega vérifié / réparé.", OK_COLOR)
            self.app.audit.log_action(
                "impressions_vega_create_or_repair",
                status="success",
                details={
                    "folder_ok": bool((status or {}).get("folder_ok")),
                    "share_ok": bool((status or {}).get("share_ok")),
                },
            )

        self.app.run_task(
            "Création / réparation du module Impressions Vega",
            self.app.impressions.create_or_repair,
            on_success=on_success,
            clear_display=True,
        )
