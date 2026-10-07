import queue
import tkinter as tk
from tkinter import ttk

from ._crashlog import install_tk as install_tk_crash_log
from ._status_bar import StatusBarMixin
from ._task_runner import TaskRunnerMixin

from vega_backend import (
    AuditLogger,
    CleanVegaManager,
    DownloadManager,
    FirewallManager,
    FixedIPManager,
    ImpressionsManager,
    MigrationManager,
    NotepadInstallerManager,
    PcInfoManager,
    RetailForceManager,
    SmtpTestManager,
    ToolLogger,
    UpdateChecker,
    ensure_defender_exclusions,
    is_admin,
)
from vega_backend.hfsql_installer import HfsqlInstallerManager
from vega_backend.cerberit import CerberitManager
from vega_security import (
    consume_skip_login_once,
    load_screen_mode_override,
    load_window_geometry,
    save_window_geometry,
    set_skip_login_once,
)

from .resources import load_image, load_scaled_logo, set_window_icon
from .tabs.clean import CleanTabV2
from .tabs.download import CompactDownloadTab
from .tabs.firewall import FirewallTab
from .tabs.fixed_ip import FixedIPTab
from .tabs.impressions import ImpressionsTab
from .tabs.hfsql import HfsqlTab
from .tabs.cerberit import CerberitTab
from .tabs.migration import MigrationTab
from .tabs.pc_info import PcInfoTab
from .tabs.smtp_test import SmtpTestTab
from .theme import (
    ACCENT_BLUE,
    APP_FOOTER,
    APP_TITLE,
    APP_VERSION,
    ERROR_COLOR,
    ICON_FILES,
    MUTED_TEXT,
    OK_COLOR,
    PANEL_BG,
    WARN_COLOR,
    WINDOW_BG,
)
from .utils import apply_adaptive_scaling, enable_dpi_awareness, fit_window_to_screen, is_small_screen
from .views.home import HomeView
from .views.login import LoginView
from .views.support import SupportTab
from .widgets import LogDrawer, ScrollableHost, Sidebar, tool_button


class VegaToolApp(TaskRunnerMixin, StatusBarMixin, tk.Tk):
    def __init__(self):
        enable_dpi_awareness()
        super().__init__()
        apply_adaptive_scaling(self)
        set_window_icon(self)
        self.title("Accès protégé")
        fit_window_to_screen(self, 360, 500, 340, 480, zoom_if_small=False)
        self.resizable(True, True)
        self.configure(bg=WINDOW_BG)

        self.event_queue = queue.Queue()
        self.busy_keys = set()
        self.log_drawer = None
        self.sidebar = None
        self.content_stack = None
        self.modules = {}
        self.current_module_key = None
        self.status_var = tk.StringVar(value="Prêt.")
        self._sticky_error = False
        self._pending_status = None

        self.icons = {name: load_image(path) for name, path in ICON_FILES.items()}
        # Logo CerberIT (kiwi.png) : mis a l'echelle de la sidebar (les icones
        # silk font 16 px, ce logo fait 36x38 -> on le reduit pour rester aligne).
        self.icons["cerberit"] = load_scaled_logo("media/kiwi.png", 18)

        self.logger = ToolLogger(self.enqueue_log)
        self.migration = MigrationManager(self.logger)
        self.impressions = ImpressionsManager(self.logger)
        self.firewall = FirewallManager(self.logger)
        self.fixed_ip = FixedIPManager(self.logger)
        self.cleaner = CleanVegaManager(self.logger)
        self.smtp = SmtpTestManager(self.logger)
        self.notepad = NotepadInstallerManager(self.logger)
        self.retailforce = RetailForceManager(self.logger)
        self.downloads = DownloadManager(self.logger, self.enqueue_download_progress)
        self.pc_info = PcInfoManager(self.logger)
        self.hfsql_installer = HfsqlInstallerManager(self.logger)
        self.cerberit = CerberitManager(self.logger)
        self.audit = AuditLogger(self.logger, tool_version=APP_VERSION)
        # Audit auto de TOUTES les lignes de log : chaque info/warn/error
        # remonte dans le journal d'audit avec un statut adapte.
        def _logger_to_audit(level, message):
            status = {"info": "info", "warn": "warning", "error": "failure"}.get(level, "info")
            self.audit.log_action("log", status=status, details={"level": level, "message": message})
        self.logger.set_audit_sink(_logger_to_audit)
        # Premier log : ouverture de l'app
        self.audit.log_action(
            "app_start",
            status="info",
            details={"version": APP_VERSION, "exe": getattr(__import__('sys'), 'executable', '?')},
        )
        self.updater = UpdateChecker(self.logger, APP_VERSION)
        self._update_in_progress = False
        # Ajoute %TEMP%\\_MEI* a l'exclusion Windows Defender pour eviter
        # le popup "Failed to load python312.dll" pendant les auto-updates.
        # Idempotent : Add-MpPreference ignore les chemins deja presents.
        # Non-bloquant : PowerShell tourne en arriere-plan, on n'attend pas.
        try:
            ensure_defender_exclusions(self.logger)
        except Exception:
            pass

        self.logo_vega = None
        self.logo_zucchetti = None

        # Les exceptions levees dans un callback Tk ne remontent PAS a
        # sys.excepthook : sans ce relais, un plantage apres le login est
        # totalement silencieux en build fenetre.
        install_tk_crash_log(self)

        self.apply_style()
        if consume_skip_login_once():
            # Bypass demande par un redemarrage interne (toggle ecran) : on saute directement le login.
            self.login_view = None
            self.after(50, self.on_login_success)
        else:
            self.login_view = LoginView(self, self.on_login_success, self.destroy)
        self.after(40, self.process_events)

    def apply_style(self):
        style = ttk.Style(self)
        for name in ("winnative", "vista", "xpnative", "default", "classic"):
            if name in style.theme_names():
                style.theme_use(name)
                break
        style.configure("Treeview", font=("Tahoma", 9), rowheight=22)
        style.configure("Treeview.Heading", font=("Tahoma", 9, "bold"))
        style.configure("TScrollbar", arrowsize=14)

    def show_info(self, title, message):
        # Popup info sans choix (juste OK) — pour les messages purement informatifs
        # ou erreurs qui ne demandent pas de decision.
        from tkinter import messagebox
        messagebox.showinfo(title, message, parent=self)

    def confirm_action(self, title, message):
        result = {"value": False}
        dialog = tk.Toplevel(self)
        set_window_icon(dialog)
        dialog.title(title)
        dialog.resizable(False, False)
        dialog.configure(bg=WINDOW_BG)
        dialog.transient(self)

        host = tk.Frame(dialog, bg=WINDOW_BG, padx=16, pady=14)
        host.pack(fill="both", expand=True)
        host.columnconfigure(0, weight=1)

        tk.Label(
            host,
            text=message,
            bg=WINDOW_BG,
            justify="left",
            wraplength=380,
            font=("Tahoma", 9),
        ).grid(row=0, column=0, sticky="w")

        buttons = tk.Frame(host, bg=WINDOW_BG)
        buttons.grid(row=1, column=0, sticky="e", pady=(16, 0))

        def close_with(value):
            result["value"] = value
            dialog.destroy()

        tool_button(
            buttons,
            text="Oui",
            command=lambda: close_with(True),
            width=10,
            anchor="center",
        ).grid(row=0, column=0, padx=(0, 8))
        tool_button(
            buttons,
            text="Non",
            command=lambda: close_with(False),
            width=10,
            anchor="center",
        ).grid(row=0, column=1)

        dialog.protocol("WM_DELETE_WINDOW", lambda: close_with(False))
        dialog.bind("<Escape>", lambda _event: close_with(False))
        dialog.bind("<Return>", lambda _event: close_with(True))

        dialog.update_idletasks()
        parent_x = self.winfo_rootx()
        parent_y = self.winfo_rooty()
        parent_w = self.winfo_width()
        parent_h = self.winfo_height()
        dialog_w = dialog.winfo_width()
        dialog_h = dialog.winfo_height()
        pos_x = parent_x + max((parent_w - dialog_w) // 2, 0)
        pos_y = parent_y + max((parent_h - dialog_h) // 2, 0)
        dialog.geometry(f"+{pos_x}+{pos_y}")

        dialog.grab_set()
        dialog.focus_force()
        self.wait_window(dialog)
        return result["value"]

    def on_login_success(self):
        if self.login_view:
            self.login_view.destroy()
            self.login_view = None

        self.title(APP_TITLE)
        override = load_screen_mode_override()
        if override == "small":
            self.small_screen = True
        elif override == "large":
            self.small_screen = False
        else:
            self.small_screen = is_small_screen(self)
        # Tailles en "pixels-96" (unites logiques) : la fonction les rescale au DPI reel.
        # Augmente par rapport a 1024x640 pour absorber la status grid sans scroll.
        if self.small_screen:
            fit_window_to_screen(self, 1000, 660, 920, 600, zoom_if_small=False)
        else:
            fit_window_to_screen(self, 1180, 760, 1020, 660, zoom_if_small=False)
        self.resizable(True, True)
        # Restaure la geometrie precedente (taille + position + maximize) si presente.
        # Clamp la position au virtual screen courant : evite que la fenetre apparaisse
        # off-screen si on a debranche le moniteur ou les coordonnees stockees pointent
        # vers un ecran qui n'existe plus (cas vu : x=4278 sur un setup monoecran).
        saved_geom = load_window_geometry()
        if saved_geom:
            x, y = saved_geom["x"], saved_geom["y"]
            w, h = saved_geom["w"], saved_geom["h"]
            # Virtual screen bounds = union de tous les ecrans actifs
            try:
                vx = self.winfo_vrootx()
                vy = self.winfo_vrooty()
                vw = self.winfo_vrootwidth()
                vh = self.winfo_vrootheight()
                # Si le coin haut-gauche est hors du virtual screen, on recentre
                # sur l'ecran primaire (0,0).
                if x < vx or y < vy or x + 100 > vx + vw or y + 50 > vy + vh:
                    x, y = max(vx, 0), max(vy, 0)
            except Exception:
                pass
            self.geometry(f"{w}x{h}+{x}+{y}")
            if saved_geom.get("zoomed"):
                try:
                    self.state("zoomed")
                except Exception:
                    pass
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.build_main_ui()

    def _on_close(self):
        # Persiste la geometrie avant fermeture (pas de mots de passe ni d'onglets memorises).
        try:
            zoomed = self.state() == "zoomed"
            if zoomed:
                # On lit la geometrie 'normal' avant maximize pour la restaurer plus tard.
                self.state("normal")
                self.update_idletasks()
            save_window_geometry(
                x=max(self.winfo_x(), 0),
                y=max(self.winfo_y(), 0),
                w=self.winfo_width(),
                h=self.winfo_height(),
                zoomed=zoomed,
            )
        except Exception:
            pass
        self.destroy()

    def build_main_ui(self):
        # Construit la shell MMC-style : header compact, sidebar, stack modules, drawer journal, statusbar.
        self.logo_vega_small = load_scaled_logo("media/logo_vega.png", 26)
        self.logo_zucchetti_small = load_scaled_logo("media/Logo_Zucchetti.png-small.png", 28)

        container = tk.Frame(self, bg=WINDOW_BG)
        container.pack(fill="both", expand=True)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(1, weight=1)

        # Header compact : logo + titre à gauche, Infos à droite.
        header = tk.Frame(container, bg=WINDOW_BG, padx=10, pady=6, bd=1, relief="flat")
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)

        title_block = tk.Frame(header, bg=WINDOW_BG)
        title_block.grid(row=0, column=0, sticky="w")
        if self.logo_vega_small:
            tk.Label(title_block, image=self.logo_vega_small, bg=WINDOW_BG).pack(side="left", padx=(0, 8))
        tk.Label(title_block, text=APP_TITLE, font=("Tahoma", 12, "bold"), bg=WINDOW_BG).pack(side="left")
        # Badge DEV permanent quand on tourne vega_toolbox_dev.exe.
        import sys as _sys
        from pathlib import Path as _P
        _exe_stem = _P(_sys.executable).stem.lower() if getattr(_sys, "frozen", False) else ""
        if "_dev" in _exe_stem:
            tk.Label(
                title_block,
                text="  DEV",
                font=("Tahoma", 14, "bold"),
                bg=WINDOW_BG,
                fg=ERROR_COLOR,
            ).pack(side="left")

        header_actions = tk.Frame(header, bg=WINDOW_BG)
        header_actions.grid(row=0, column=2, sticky="e")
        # Ordre d'affichage (pack side="right" empile de droite a gauche) :
        # ... [Tutoriel] [Infos] [logo Zucchetti].
        tool_button(
            header_actions,
            text="Infos",
            command=self.show_support,
            image=self.icons.get("info"),
            anchor="center",
        ).pack(side="right")
        tool_button(
            header_actions,
            text="Tutoriel",
            command=self.start_tutorial,
            image=self.icons.get("verifier"),
            anchor="center",
        ).pack(side="right", padx=(0, 6))
        # Discret marquage editeur a droite : label cree, packe seulement en mode large.
        # header_actions expose en public pour le mode Tutoriel (surbrillance des boutons).
        self._header_actions = header_actions
        self.header_actions = header_actions
        self._zucchetti_label = None
        if self.logo_zucchetti_small:
            self._zucchetti_label = tk.Label(header_actions, image=self.logo_zucchetti_small, bg=WINDOW_BG)
            if not self.small_screen:
                self._zucchetti_label.pack(side="right", padx=(0, 10))

        # Corps : sidebar à gauche, zone module + drawer à droite.
        body = tk.Frame(container, bg=WINDOW_BG)
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)
        self._body = body

        self.sidebar = Sidebar(body, width=184, on_select=self.show_module, compact=self.small_screen)
        self.sidebar.grid(row=0, column=0, sticky="ns", padx=(6, 0), pady=6)

        content_host = tk.Frame(body, bg=WINDOW_BG)
        content_host.grid(row=0, column=1, sticky="nsew", padx=6, pady=6)
        content_host.columnconfigure(0, weight=1)
        content_host.rowconfigure(0, weight=1)

        self.content_stack = tk.Frame(content_host, bg=WINDOW_BG, bd=1, relief="sunken")
        self.content_stack.grid(row=0, column=0, sticky="nsew")
        self.content_stack.columnconfigure(0, weight=1)
        self.content_stack.rowconfigure(0, weight=1)

        drawer_height = 140 if self.small_screen else 200
        self.log_drawer = LogDrawer(content_host, self, expanded_height=drawer_height)
        self.log_drawer.grid(row=1, column=0, sticky="ew", pady=(4, 0))

        # Enregistrement des modules dans la stack.
        self._register_module("home", HomeView)
        self._register_module("migration", MigrationTab)
        self._register_module("impressions", ImpressionsTab)
        self._register_module("firewall", FirewallTab)
        self._register_module("fixed_ip", FixedIPTab)
        self._register_module("clean", CleanTabV2)
        self._register_module("smtp_test", SmtpTestTab)
        self._register_module("download", CompactDownloadTab)
        self._register_module("pc_info", PcInfoTab)
        self._register_module("hfsql", HfsqlTab)
        self._register_module("cerberit", CerberitTab)
        self._register_module("support", SupportTab)

        # Synchronisations déjà en place dans l'ancienne version.
        migration = self.modules["migration"]
        self.modules["download"].set_vega_root(migration.root_var.get())
        self.modules["clean"].set_root(migration.root_var.get())
        # L'accueil partage la racine Vega de l'onglet Migration (edition 2-way).
        self.modules["home"].bind_root_var(migration.root_var)

        # Audit logger : ecrit son journal dans la racine Vega courante.
        # Hook sur le trace du root_var pour suivre les changements.
        self.audit.set_vega_root(migration.root_var.get())
        migration.root_var.trace_add(
            "write",
            lambda *_: self.audit.set_vega_root(migration.root_var.get()),
        )

        self._populate_sidebar()

        # Statusbar compacte XP.
        statusbar = tk.Frame(container, bg=WINDOW_BG, bd=1, relief="sunken")
        statusbar.grid(row=2, column=0, sticky="ew")
        statusbar.columnconfigure(1, weight=1)

        status_left = tk.Frame(statusbar, bg=WINDOW_BG, padx=6, pady=2)
        status_left.grid(row=0, column=0, sticky="w")
        tk.Label(
            status_left,
            text=("Admin" if is_admin() else "Utilisateur"),
            fg=(OK_COLOR if is_admin() else ERROR_COLOR),
            bg=WINDOW_BG,
            font=("Tahoma", 8, "bold"),
        ).pack(side="left")

        self.status_label = tk.Label(
            statusbar,
            textvariable=self.status_var,
            bg=WINDOW_BG,
            anchor="w",
            font=("Tahoma", 8),
            padx=10,
            pady=2,
        )
        self.status_label.grid(row=0, column=1, sticky="ew")

        # Bouton "×" d'acquittement : visible uniquement quand une erreur est epinglee.
        self.status_dismiss = tk.Button(
            statusbar,
            text="×",
            command=self.dismiss_status_error,
            bd=0,
            relief="flat",
            bg=WINDOW_BG,
            fg=ERROR_COLOR,
            activebackground=WINDOW_BG,
            activeforeground=ERROR_COLOR,
            font=("Tahoma", 11, "bold"),
            cursor="hand2",
            padx=6,
            pady=0,
        )

        self.progressbar = ttk.Progressbar(statusbar, mode="indeterminate", length=120)
        # Grid mais pas visible tant que set_busy(True) n'est pas appelé.

        if not self.small_screen:
            status_right = tk.Frame(statusbar, bg=WINDOW_BG, padx=6, pady=2)
            status_right.grid(row=0, column=4, sticky="e")
            tk.Label(
                status_right,
                text=APP_FOOTER,
                bg=WINDOW_BG,
                fg=MUTED_TEXT,
                font=("Tahoma", 8),
            ).pack(side="right")

        # Defer pour laisser les Configure events des modules se resoudre
        # avant le tkraise : sinon la home apparait vide au premier affichage
        # (necessitait un clic manuel sur "Accueil" pour s'afficher).
        self.after_idle(lambda: self.show_module("home"))
        self._bind_shortcuts()
        # Listener resize : ajuste sidebar + cosmetiques quand la largeur effective franchit le seuil.
        self.bind("<Configure>", self._on_root_configure)
        # Verification de mise a jour silencieuse 3s apres l'ouverture (laisse l'UI s'afficher).
        self.after(3000, self._check_for_update_silent)

    def _populate_sidebar(self):
        # Population idempotente : sert au build initial et a la reconstruction sur resize.
        # Icones : un visuel different par entree pour eviter la repetition (avant : 'reparer'
        # apparaissait deux fois, 'actualiser' aussi). Source FamFamFam Silk (CC BY 2.5).
        self.sidebar.add_entry("home", "Accueil", icon=self.icons.get("house"))
        self.sidebar.add_separator()
        self.sidebar.add_caption("Modules")
        self.sidebar.add_entry("migration", "Migration", icon=self.icons.get("arrow_switch"))
        self.sidebar.add_entry("impressions", "Impressions Vega", icon=self.icons.get("impression"))
        self.sidebar.add_entry("firewall", "Pare-feu", icon=self.icons.get("shield"))
        self.sidebar.add_entry("fixed_ip", "IP fixe", icon=self.icons.get("computer"))
        self.sidebar.add_entry("clean", "Nettoyage Vega", icon=self.icons.get("bin"))
        self.sidebar.add_entry("smtp_test", "Envoi mail", icon=self.icons.get("email_go"))
        self.sidebar.add_entry("download", "Téléchargement", icon=self.icons.get("arrow_down"))
        self.sidebar.add_entry("pc_info", "Info PC", icon=self.icons.get("computer"))
        self.sidebar.add_entry("hfsql", "HFSQL Serveur", icon=self.icons.get("hfsql"))
        self.sidebar.add_entry("cerberit", "CerberIT", icon=self.icons.get("cerberit"))

        self.sidebar.add_separator(bottom=True)
        admin_color = OK_COLOR if is_admin() else ERROR_COLOR
        if self.small_screen:
            admin_box = tk.Frame(self.sidebar._bottom, bg=PANEL_BG, bd=1, relief="sunken", padx=4, pady=4)
            admin_box.pack(padx=6, pady=(0, 6))
            tk.Label(
                admin_box,
                text="A" if is_admin() else "U",
                fg=admin_color,
                bg=PANEL_BG,
                font=("Tahoma", 11, "bold"),
                width=2,
            ).pack()
        else:
            admin_box = tk.Frame(self.sidebar._bottom, bg=PANEL_BG, bd=1, relief="sunken", padx=8, pady=6)
            admin_box.pack(fill="x", padx=8, pady=(0, 8))
            tk.Label(admin_box, text="Administrateur", bg=PANEL_BG, font=("Tahoma", 8, "bold")).pack(anchor="w")
            tk.Label(
                admin_box,
                text="OUI" if is_admin() else "NON",
                fg=admin_color,
                bg=PANEL_BG,
                font=("Tahoma", 9, "bold"),
            ).pack(anchor="w")

    def _apply_screen_mode(self, small):
        # Reconstruit les elements depend du mode (sidebar, logo Zucchetti) sans toucher
        # aux modules eux-memes (preserve l'etat : log drawer, formulaires, selections).
        if small == self.small_screen:
            return
        self.small_screen = small
        previous_selected = self.sidebar.selected_key if self.sidebar else "home"
        if self.sidebar:
            self.sidebar.destroy()
        self.sidebar = Sidebar(self._body, width=184, on_select=self.show_module, compact=small)
        self.sidebar.grid(row=0, column=0, sticky="ns", padx=(6, 0), pady=6)
        self._populate_sidebar()
        if previous_selected:
            self.sidebar.select(previous_selected)
        if self._zucchetti_label:
            if small:
                self._zucchetti_label.pack_forget()
            else:
                self._zucchetti_label.pack(side="right", padx=(0, 10))

    def _on_root_configure(self, event):
        # Debounce : un seul recalc apres 200ms de stabilite (evite les flashes pendant un drag).
        if event.widget is not self:
            return
        if getattr(self, "_resize_after_id", None):
            self.after_cancel(self._resize_after_id)
        self._resize_after_id = self.after(200, self._check_screen_threshold)

    def _check_screen_threshold(self):
        self._resize_after_id = None
        # Compare la largeur courante (en pixels-96) au seuil 1100 px logiques.
        from .utils import get_system_dpi
        dpi = get_system_dpi()
        effective_w = self.winfo_width() * 96 / dpi
        new_small = effective_w < 1100
        self._apply_screen_mode(new_small)

    def _bind_shortcuts(self):
        # F1 Infos, F5 refresh, F12 maximize toggle, Ctrl+L tiroir journal, Ctrl+1..7 modules.
        self.bind_all("<F1>", lambda _e: self.show_support())
        self.bind_all("<F5>", lambda _e: self._refresh_current_module())
        self.bind_all("<F12>", self._toggle_maximize)
        self.bind_all("<Control-l>", lambda _e: self.log_drawer.toggle() if self.log_drawer else None)
        self.bind_all("<Control-L>", lambda _e: self.log_drawer.toggle() if self.log_drawer else None)
        ordered = ["home", "migration", "impressions", "firewall", "fixed_ip", "clean", "smtp_test", "download", "pc_info", "hfsql", "cerberit"]
        # ATTENTION : seuls les chiffres 1 a 9 existent comme keysym Tk. Au-dela
        # ("<Control-Key-10>"), Tk leve TclError: bad event type or keysym.
        # Tk 8.6 recent tolerait silencieusement, celui livre avec Python 3.7
        # (build legacy) refuse et faisait planter l'application juste apres le
        # login. On ne lie donc que les neuf premiers modules ; les suivants
        # restent accessibles par la barre laterale.
        for index, key in enumerate(ordered[:9], start=1):
            self.bind_all(f"<Control-Key-{index}>", lambda _e, k=key: self.show_module(k))

    def _toggle_maximize(self, _event=None):
        # F12 : bascule maximize/restore. zoomed = maximisee (pas plein ecran).
        try:
            if self.state() == "zoomed":
                self.state("normal")
            else:
                self.state("zoomed")
        except tk.TclError:
            pass

    def _refresh_current_module(self):
        module = self.modules.get(self.current_module_key)
        if module is None:
            return
        for method_name in ("refresh", "refresh_adapters", "refresh_catalog", "refresh_analysis"):
            method = getattr(module, method_name, None)
            if callable(method):
                method()
                return

    def _register_module(self, key, cls):
        module = ScrollableHost(self.content_stack, cls, self)
        module.grid(row=0, column=0, sticky="nsew")
        self.modules[key] = module

    def show_module(self, key):
        module = self.modules.get(key)
        if module is None:
            return
        # Audit navigation : log a chaque changement d'onglet.
        if self.current_module_key != key:
            try:
                self.audit.log_action(
                    "navigate",
                    status="info",
                    details={"from": self.current_module_key or "(boot)", "to": key},
                )
            except Exception:
                pass
        # Construit le contenu si lazy-pending : evite l'onglet vide au premier affichage.
        if hasattr(module, "ensure_content"):
            module.ensure_content()
        module.tkraise()
        self.current_module_key = key
        if self.sidebar and key in self.sidebar.entries:
            self.sidebar.select(key)
        self._trigger_lazy_load(key)
        if key == "home":
            if hasattr(module, "refresh"):
                module.refresh()
            self.set_status("Prêt.", OK_COLOR)

    def _trigger_lazy_load(self, key):
        module = self.modules.get(key)
        if module is None:
            return
        if key == "fixed_ip" and not getattr(module, "loaded_once", True):
            module.refresh_adapters(initial=True)
        elif key == "download" and not getattr(module, "dynamic_loaded", True):
            module.refresh_catalog()

    def show_home(self):
        self.show_module("home")

    def show_support(self):
        self.show_module("support")

    def show_migration_tab(self):
        self.show_module("migration")

    def show_download_tab(self):
        self.show_module("download")

    def show_clean_tab(self):
        self.show_module("clean")

    def show_ip_tab(self):
        self.show_module("fixed_ip")

    def start_tutorial(self):
        # Lance le mode Tutoriel : overlay sombre + spotlight + callout.
        # Une seule instance a la fois ; un re-clic ne fait rien si deja actif.
        existing = getattr(self, "_tutorial", None)
        if existing is not None and getattr(existing, "overlay", None) is not None:
            return
        from ._tutorial import TutorialOverlay, get_default_steps
        self._tutorial = TutorialOverlay(self, get_default_steps())
        self._tutorial.start()

    def _check_for_update_silent(self):
        # Verification non-bloquante au demarrage : ne rien afficher si pas de reseau,
        # pas d'update, ou en mode source. Seul un update disponible declenche la popup.
        # Skip aussi sur les builds dev (exe = vega_toolbox_dev.exe) : pas envie qu'un
        # tester soit force a downgrader vers la prod.
        if self._update_in_progress:
            return
        if not self.updater.is_frozen():
            return
        import sys as _sys
        from pathlib import Path as _P
        exe_name = _P(_sys.executable).stem.lower() if getattr(_sys, "frozen", False) else ""
        if "_dev" in exe_name:
            return

        def on_success(result):
            if not result:
                try:
                    self.audit.log_action(
                        "update_check", status="warning",
                        details={"verdict": "Impossible de comparer les versions"},
                    )
                except Exception:
                    pass
                return
            remote = result.get("remote", "?")
            current = result.get("current", "?")
            has_update = result.get("has_update", False)
            try:
                self.audit.log_action(
                    "update_check",
                    status="success" if has_update else "info",
                    details={
                        "version_installee": current,
                        "version_disponible": remote,
                        "mise_a_jour_disponible": has_update,
                        "source": result.get("source"),
                    },
                )
            except Exception:
                pass
            if not has_update:
                return
            message = (
                f"Une nouvelle version de {APP_TITLE} est disponible.\n\n"
                f"  Version installee : {current}\n"
                f"  Version disponible : {remote}\n\n"
                "Voulez-vous telecharger et appliquer la mise a jour maintenant ?\n"
                "L'application va se relancer automatiquement."
            )
            if self.confirm_action("Mise a jour disponible", message):
                self._apply_update()

        def on_error(exc):
            # Loggue l'echec pour qu'on puisse diagnostiquer depuis l'audit.
            try:
                self.audit.log_action(
                    "update_check", status="failure",
                    details={"raison": "API GitLab inaccessible"},
                    error=str(exc),
                )
            except Exception:
                pass

        self.run_task(
            "Verification des mises a jour",
            self.updater.check,
            on_success=on_success,
            on_error=on_error,
            key="update_check",
        )

    def check_for_update_manual(self):
        # Verification manuelle declenchee par l'utilisateur (bouton 'Verifier
        # les mises a jour' dans Support). Contrairement a _check_for_update_silent,
        # ce check affiche toujours un retour : 'a jour', 'update disponible',
        # ou 'erreur reseau'. Il fonctionne aussi en mode source/dev (juste
        # pour pouvoir tester le check) mais ne propose pas le download dans
        # ce cas.
        if self._update_in_progress:
            self.set_status("Mise à jour déjà en cours.", WARN_COLOR)
            return

        import sys as _sys
        from pathlib import Path as _P
        is_frozen = getattr(_sys, "frozen", False)
        exe_name = _P(_sys.executable).stem.lower() if is_frozen else ""
        is_dev_build = "_dev" in exe_name

        def on_success(result):
            if not result:
                self.show_info(
                    "Vérification des mises à jour",
                    "Impossible de comparer les versions (réponse vide de GitLab).",
                )
                try:
                    self.audit.log_action(
                        "update_check_manual", status="warning",
                        details={"verdict": "Reponse vide"},
                    )
                except Exception:
                    pass
                return
            remote = result.get("remote", "?")
            current = result.get("current", "?")
            has_update = result.get("has_update", False)
            try:
                self.audit.log_action(
                    "update_check_manual",
                    status="success" if has_update else "info",
                    details={
                        "version_installee": current,
                        "version_disponible": remote,
                        "mise_a_jour_disponible": has_update,
                        "source": result.get("source"),
                    },
                )
            except Exception:
                pass
            if not has_update:
                self.show_info(
                    "Vérification des mises à jour",
                    f"{APP_TITLE} est à jour.\n\n"
                    f"  Version installée : {current}\n"
                    f"  Dernière version disponible : {remote}",
                )
                return
            # Update dispo : proposer le download (sauf en source / dev)
            if not is_frozen:
                self.show_info(
                    "Mise à jour disponible",
                    f"Une nouvelle version est disponible.\n\n"
                    f"  Version installée : {current}\n"
                    f"  Version disponible : {remote}\n\n"
                    "Téléchargement automatique non disponible en mode source.",
                )
                return
            if is_dev_build:
                self.show_info(
                    "Mise à jour disponible",
                    f"Build de développement détecté.\n\n"
                    f"  Version dev installée : {current}\n"
                    f"  Version prod disponible : {remote}\n\n"
                    "Le téléchargement automatique est désactivé sur les builds dev "
                    "pour éviter un downgrade involontaire.",
                )
                return
            message = (
                f"Une nouvelle version de {APP_TITLE} est disponible.\n\n"
                f"  Version installée : {current}\n"
                f"  Version disponible : {remote}\n\n"
                "Voulez-vous télécharger et appliquer la mise à jour maintenant ?\n"
                "L'application va se relancer automatiquement."
            )
            if self.confirm_action("Mise à jour disponible", message):
                self._apply_update()

        def on_error(exc):
            try:
                self.audit.log_action(
                    "update_check_manual", status="failure",
                    details={"raison": "API GitLab inaccessible"},
                    error=str(exc),
                )
            except Exception:
                pass
            self.show_info(
                "Vérification des mises à jour",
                "Impossible de joindre GitLab pour vérifier les mises à jour.\n\n"
                f"Détail : {exc}",
            )

        self.set_status("Vérification des mises à jour...", ACCENT_BLUE)
        self.run_task(
            "Vérification des mises à jour",
            self.updater.check,
            on_success=on_success,
            on_error=on_error,
            key="update_check_manual",
        )

    def _apply_update(self):
        if self._update_in_progress:
            return
        self._update_in_progress = True

        def progress(_step, message):
            self.event_queue.put(("log", message, "info"))

        def on_success(_result):
            # Le batch de remplacement a ete spawn detache : on doit liberer
            # le fichier .exe en quittant immediatement.
            self.set_status("Mise a jour : redemarrage...", OK_COLOR)
            self.after(500, self._exit_for_update)

        def on_error(exc):
            self._update_in_progress = False
            self.set_status(f"Mise a jour echouee : {exc}", ERROR_COLOR)

        self.run_task(
            "Telechargement de la mise a jour",
            lambda: self.updater.download_and_apply(progress),
            on_success=on_success,
            on_error=on_error,
            key="update_apply",
        )

    def _exit_for_update(self):
        # Sauvegarde la geometrie comme un close normal puis quitte le process pour
        # que le batch detache puisse remplacer le .exe (file lock libere).
        # os._exit : sort sans finalisation Python ni atexit ; evite que le bootloader
        # PyInstaller reste en vie pendant que le batch remplace le .exe sous lui
        # (cause du popup "Failed to load python312.dll" en post-update).
        try:
            self._on_close()
        except Exception:
            pass
        import os as _os
        _os._exit(0)

    # enqueue_log / enqueue_download_progress / process_events / run_task / _task_worker
    # sont herites de TaskRunnerMixin.
    # set_status / dismiss_status_error / _show_status_dismiss / set_busy
    # sont herites de StatusBarMixin.


def main():
    app = VegaToolApp()
    app.mainloop()
