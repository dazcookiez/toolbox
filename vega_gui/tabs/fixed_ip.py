import tkinter as tk
from tkinter import ttk

from ..theme import APP_TITLE, ERROR_COLOR, OK_COLOR, WARN_COLOR, WINDOW_BG
from ..widgets import info_panel, tool_button, value_cell


class FixedIPTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=8)
        self.app = app
        self.loaded_once = False
        self.auto_loading = False
        self.adapters = {}
        self.adapter_var = tk.StringVar(value="")
        self.description_var = tk.StringVar(value="À vérifier")
        self.status_var = tk.StringVar(value="À vérifier")
        self.mode_var = tk.StringVar(value="À vérifier")
        self.state_var = tk.StringVar(value="À vérifier")
        self.ip_var = tk.StringVar(value="À vérifier")
        self.mask_var = tk.StringVar(value="À vérifier")
        self.gateway_var = tk.StringVar(value="À vérifier")
        self.dns_var = tk.StringVar(value="À vérifier")
        self.build_ui()

    def build_ui(self):
        # L'écran reste lisible même avec plusieurs cartes réseau.
        self.columnconfigure(0, weight=3)
        self.columnconfigure(1, weight=2)
        self.rowconfigure(1, weight=1)

        selector = tk.LabelFrame(self, text="Carte réseau", padx=8, pady=6, bg=WINDOW_BG)
        selector.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self.selector = selector
        selector.columnconfigure(1, weight=1)

        tk.Label(selector, text="Interface", bg=WINDOW_BG).grid(row=0, column=0, sticky="w")
        self.adapter_combo = ttk.Combobox(
            selector,
            textvariable=self.adapter_var,
            state="readonly",
            values=[],
            font=("Tahoma", 9),
        )
        self.adapter_combo.grid(row=0, column=1, sticky="ew", padx=(10, 0))
        self.adapter_combo.bind("<<ComboboxSelected>>", lambda _event: self.load_configuration(auto=True))

        tool_button(
            selector,
            text="Actualiser les cartes",
            command=self.refresh_adapters,
            image=self.app.icons.get("actualiser"),
        ).grid(row=0, column=2, sticky="e", padx=(10, 0))

        config_frame = tk.LabelFrame(self, text="Configuration actuelle", padx=8, pady=6, bg=WINDOW_BG)
        config_frame.grid(row=1, column=0, sticky="nsew")
        config_frame.columnconfigure(1, weight=1)

        fields = [
            ("Description", self.description_var),
            ("Statut", self.status_var),
            ("Mode IPv4", self.mode_var),
            ("État IPv4", self.state_var),
            ("Adresse IPv4", self.ip_var),
            ("Masque / préfixe", self.mask_var),
            ("Passerelle", self.gateway_var),
            ("DNS", self.dns_var),
        ]
        for index, (label, variable) in enumerate(fields):
            tk.Label(config_frame, text=label, bg=WINDOW_BG).grid(row=index, column=0, sticky="w", pady=(0, 4))
            value = value_cell(config_frame, textvariable=variable, width=38, anchor="w")
            if label == "DNS":
                value.configure(justify="left", wraplength=360)
            value.grid(row=index, column=1, sticky="ew", padx=(12, 0), pady=(0, 4))

        actions = tk.LabelFrame(self, text="Actions IP fixe", padx=8, pady=6, bg=WINDOW_BG)
        actions.grid(row=1, column=1, sticky="nsew", padx=(10, 0))
        actions.columnconfigure(0, weight=1)

        tool_button(
            actions,
            text="Lire la configuration",
            command=self.load_configuration,
            image=self.app.icons.get("verifier"),
        ).grid(row=0, column=0, sticky="ew")

        self.apply_button = tool_button(
            actions,
            text="Appliquer en IP fixe",
            command=self.apply_static,
            image=self.app.icons.get("reparer"),
            state="disabled",
        )
        self.apply_button.grid(row=1, column=0, sticky="ew", pady=(8, 0))

        self.dhcp_button = tool_button(
            actions,
            text="Remettre en DHCP",
            command=self.set_dhcp,
            image=self.app.icons.get("retour"),
            state="disabled",
        )
        self.dhcp_button.grid(row=2, column=0, sticky="ew", pady=(8, 0))

        info_panel(
            actions,
            "L'outil relit les paramètres IPv4 actuels de la carte choisie puis peut les réappliquer en IP fixe. "
            "Un bouton permet aussi de revenir en DHCP.",
            wraplength=300,
        ).grid(row=3, column=0, sticky="ew", pady=(8, 0))

        info_panel(
            actions,
            "Conseil : utilisez cette fonction uniquement sur la bonne interface réseau. "
            "Un changement IP peut couper la connexion quelques secondes.",
            wraplength=300,
        ).grid(row=4, column=0, sticky="ew", pady=(10, 0))

    def refresh_adapters(self, initial=False):
        if initial and (self.loaded_once or self.auto_loading):
            return False

        def on_success(adapters):
            self.auto_loading = False
            self.loaded_once = True
            self.apply_adapters(adapters)
            if self.adapter_var.get():
                self.load_configuration(auto=True)
            elif not adapters:
                self.clear_configuration("Aucune carte détectée")
                self.app.set_status("Aucune carte réseau exploitable détectée.", WARN_COLOR)

        started = self.app.run_task(
            "Lecture des cartes réseau",
            self.app.fixed_ip.list_adapters,
            on_success=on_success,
            clear_display=not initial,
        )
        if started and initial:
            self.auto_loading = True
        return started

    def apply_adapters(self, adapters):
        current = self.adapter_var.get()
        self.adapters = {item["alias"]: item for item in adapters}
        values = [item["alias"] for item in adapters]
        self.adapter_combo.configure(values=values)
        if current in self.adapters:
            self.adapter_var.set(current)
        elif values:
            self.adapter_var.set(values[0])
        else:
            self.adapter_var.set("")

    def load_configuration(self, auto=False):
        alias = self.adapter_var.get().strip()
        if not alias:
            self.app.set_status("Sélectionnez une carte réseau.", ERROR_COLOR)
            return

        def on_success(config):
            self.apply_configuration(config)
            if config.get("link_local"):
                self.app.set_status(
                    "Configuration réseau chargée. La carte est en état invalide : adresse automatique détectée.",
                    WARN_COLOR,
                )
            elif not config.get("gateway_ok"):
                self.app.set_status(
                    "Configuration réseau chargée, mais la passerelle actuelle n'est pas cohérente.",
                    WARN_COLOR,
                )
            else:
                self.app.set_status("Configuration réseau chargée.", OK_COLOR)

        self.app.run_task(
            "Lecture de la configuration réseau",
            lambda: self.app.fixed_ip.get_configuration(alias),
            on_success=on_success,
            clear_display=not auto,
        )

    def apply_configuration(self, config):
        # Réinjecte la configuration courante dans les champs de l'écran.
        self.description_var.set(config.get("description") or "-")
        self.status_var.set(config.get("status") or "-")
        self.mode_var.set("DHCP" if config.get("dhcp_enabled") else "IP fixe")
        if config.get("link_local") and config.get("dhcp_enabled"):
            self.state_var.set("Adresse automatique (DHCP sans bail)")
        elif config.get("link_local"):
            self.state_var.set("Configuration invalide")
        elif config.get("gateway_ok"):
            self.state_var.set("Prête pour IP fixe")
        else:
            self.state_var.set("Passerelle incohérente")
        ip_value = config.get("ipv4") or "Aucune adresse IPv4"
        if config.get("link_local") and config.get("ipv4"):
            ip_value = f"{config['ipv4']} (adresse automatique)"
        self.ip_var.set(ip_value)

        prefix_length = config.get("prefix_length") or 0
        subnet_mask = config.get("subnet_mask") or ""
        if prefix_length and subnet_mask:
            self.mask_var.set(f"{subnet_mask} (/{prefix_length})")
        elif prefix_length:
            self.mask_var.set(f"/{prefix_length}")
        else:
            self.mask_var.set("-")

        gateway_value = config.get("gateway") or "-"
        if config.get("gateway") and not config.get("gateway_ok"):
            gateway_value = f"{config['gateway']} (incohérente)"
        self.gateway_var.set(gateway_value)
        dns_servers = config.get("dns_servers") or []
        self.dns_var.set(", ".join(dns_servers) if dns_servers else "Aucun DNS IPv4")

        can_apply = bool(config.get("ipv4")) and not config.get("link_local") and config.get("gateway_ok", True)
        self.apply_button.configure(state="normal" if can_apply else "disabled")
        self.dhcp_button.configure(state="normal")

    def clear_configuration(self, message="À vérifier"):
        self.description_var.set(message)
        self.status_var.set(message)
        self.mode_var.set(message)
        self.state_var.set(message)
        self.ip_var.set(message)
        self.mask_var.set(message)
        self.gateway_var.set(message)
        self.dns_var.set(message)
        self.apply_button.configure(state="disabled")
        self.dhcp_button.configure(state="disabled")

    def apply_static(self):
        alias = self.adapter_var.get().strip()
        if not alias:
            self.app.set_status("Sélectionnez une carte réseau.", ERROR_COLOR)
            return

        if not self.app.confirm_action(
            APP_TITLE,
            f"Définir les paramètres réseau actuels de '{alias}' en IP fixe ?",
        ):
            return

        def on_success(config):
            self.apply_configuration(config)
            self.app.set_status(f"La carte {alias} est maintenant en IP fixe.", OK_COLOR)
            self.app.audit.log_action(
                "network_set_static_ip",
                status="success",
                details={
                    "adapter": alias,
                    "ipv4": (config or {}).get("ipv4"),
                    "subnet": (config or {}).get("subnet"),
                    "gateway": (config or {}).get("gateway"),
                },
            )

        self.app.run_task(
            "Passage en IP fixe",
            lambda: self.app.fixed_ip.apply_static(alias),
            on_success=on_success,
            clear_display=True,
        )

    def set_dhcp(self):
        alias = self.adapter_var.get().strip()
        if not alias:
            self.app.set_status("Sélectionnez une carte réseau.", ERROR_COLOR)
            return

        if not self.app.confirm_action(
            APP_TITLE,
            f"Remettre la carte '{alias}' en DHCP ?",
        ):
            return

        def on_success(config):
            self.apply_configuration(config)
            self.app.set_status(f"La carte {alias} est repassée en DHCP.", OK_COLOR)
            self.app.audit.log_action(
                "network_set_dhcp",
                status="success",
                details={"adapter": alias},
            )

        self.app.run_task(
            "Retour en DHCP",
            lambda: self.app.fixed_ip.set_dhcp(alias),
            on_success=on_success,
            clear_display=True,
        )
