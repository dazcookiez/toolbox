"""Onglet Info PC : dashboard visuel style Task Manager.

Layout dashboard :
  - Bandeau hero en haut : 2 grosses gauges circulaires CPU + RAM cote a cote
    avec gros chiffres au centre et nom du CPU / RAM totale en sous-titre
  - Graph CPU 60 sec en banniere sous le hero, gradient bleu
  - Grille 2x2 de cards en dessous : Identite systeme | OS | Carte mere/BIOS | GPU
  - Card disques : 1 bar par volume cote a cote (pas en ligne)
  - Card reseau : 1 bloc par adaptateur
  - Card Vega : status HFSQL + partages + .exe en plein cadre

Loading state : overlay centre 'Chargement...' avec progressbar indeterminate
pendant la collecte PowerShell initiale.

Live metrics : ctypes vers WinAPI, refresh 1.5 sec, stop quand l'onglet n'est
plus visible.
"""
import ctypes
import ctypes.wintypes
import math
import tkinter as tk
from collections import deque
from datetime import datetime
from tkinter import ttk

from ..theme import (
    ACCENT_BLUE,
    ERROR_COLOR,
    MUTED_TEXT,
    OK_COLOR,
    PANEL_BG,
    WARN_COLOR,
    WHITE_BG,
    WINDOW_BG,
)
from ..widgets import tool_button


# ---------- Helpers de formatage ----------

def _fmt(value, default="—"):
    if value is None or value == "":
        return default
    return str(value)


def _fmt_gb(value):
    if value is None:
        return "—"
    try:
        return f"{float(value):.1f} Go"
    except (TypeError, ValueError):
        return str(value)


def _fmt_mhz(value):
    if value is None or value == 0:
        return "—"
    return f"{int(value)} MHz"


# ---------- Live metrics via WinAPI ----------


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.wintypes.DWORD),
        ("dwMemoryLoad", ctypes.wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _get_memory_status():
    try:
        kernel32 = ctypes.windll.kernel32
        mem = _MEMORYSTATUSEX()
        mem.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
        if not kernel32.GlobalMemoryStatusEx(ctypes.byref(mem)):
            return None
        total = mem.ullTotalPhys / (1024 ** 3)
        avail = mem.ullAvailPhys / (1024 ** 3)
        return {
            "percent": mem.dwMemoryLoad,
            "total_gb": total,
            "available_gb": avail,
            "used_gb": total - avail,
        }
    except Exception:
        return None


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", ctypes.wintypes.DWORD),
                ("dwHighDateTime", ctypes.wintypes.DWORD)]


def _ft_int(ft):
    return (ft.dwHighDateTime << 32) + ft.dwLowDateTime


class _CpuSampler:
    def __init__(self):
        self._k32 = ctypes.windll.kernel32
        self._last_idle = None
        self._last_total = None

    def sample(self):
        idle, kernel, user = _FILETIME(), _FILETIME(), _FILETIME()
        try:
            if not self._k32.GetSystemTimes(
                ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
            ):
                return None
        except Exception:
            return None
        idle_t = _ft_int(idle)
        total_t = _ft_int(kernel) + _ft_int(user)
        if self._last_idle is None:
            self._last_idle = idle_t
            self._last_total = total_t
            return None
        idle_diff = idle_t - self._last_idle
        total_diff = total_t - self._last_total
        self._last_idle = idle_t
        self._last_total = total_t
        if total_diff <= 0:
            return 0.0
        return max(0.0, min(100.0, 100.0 * (1 - idle_diff / total_diff)))


def _get_disk_free(drive):
    try:
        k32 = ctypes.windll.kernel32
        free = ctypes.c_ulonglong(0)
        total = ctypes.c_ulonglong(0)
        free_total = ctypes.c_ulonglong(0)
        if not k32.GetDiskFreeSpaceExW(
            ctypes.c_wchar_p(drive), ctypes.byref(free), ctypes.byref(total),
            ctypes.byref(free_total),
        ):
            return None
        if total.value <= 0:
            return None
        return {
            "total_gb": total.value / (1024 ** 3),
            "free_gb": free.value / (1024 ** 3),
            "used_gb": (total.value - free.value) / (1024 ** 3),
            "percent": 100.0 * (1 - free.value / total.value),
        }
    except Exception:
        return None


# ---------- Couleurs / theme dashboard ----------

CARD_BG = "#ffffff"
CARD_BORDER = "#d0d4dc"
HEADER_BG = "#0a246a"
HEADER_FG = "#ffffff"
GAUGE_BG = "#eef1f7"
GAUGE_TRACK = "#e3e7ee"


def _color_for_pct(pct):
    if pct >= 90:
        return ERROR_COLOR
    if pct >= 75:
        return WARN_COLOR
    return ACCENT_BLUE


# ---------- Widget : card avec header colore ----------

def _make_card(parent, title, height=None):
    # Card avec bordure + header colore + zone contenu blanche.
    outer = tk.Frame(parent, bg=CARD_BORDER, bd=0)
    outer.columnconfigure(0, weight=1)
    header = tk.Frame(outer, bg=HEADER_BG)
    header.grid(row=0, column=0, sticky="ew")
    tk.Label(
        header, text=title, bg=HEADER_BG, fg=HEADER_FG,
        font=("Segoe UI", 10, "bold"), padx=10, pady=5, anchor="w",
    ).pack(fill="x")
    body = tk.Frame(outer, bg=CARD_BG, padx=12, pady=10)
    body.grid(row=1, column=0, sticky="nsew", padx=1, pady=(0, 1))
    body.columnconfigure(0, weight=1)
    outer.rowconfigure(1, weight=1)
    if height:
        body.configure(height=height)
        body.grid_propagate(False)
    return outer, body


# ---------- Widget : gauge circulaire (arc Tkinter) ----------

class _CircularGauge(tk.Canvas):
    def __init__(self, parent, size=180, **kw):
        super().__init__(parent, width=size, height=size, bg=CARD_BG,
                         highlightthickness=0, **kw)
        self.size = size
        self._percent = 0.0
        self._label = ""
        self._sublabel = ""
        self._color = ACCENT_BLUE

    def set_value(self, percent, label="", sublabel="", color=None):
        self._percent = max(0.0, min(100.0, percent or 0.0))
        self._label = label or f"{self._percent:.0f}%"
        self._sublabel = sublabel
        self._color = color or _color_for_pct(self._percent)
        self._redraw()

    def _redraw(self):
        self.delete("all")
        w = int(self["width"])
        h = int(self["height"])
        margin = 12
        thickness = 18
        x0, y0 = margin, margin
        x1, y1 = w - margin, h - margin
        # Track gris
        self.create_arc(
            x0, y0, x1, y1, start=90, extent=359.999,
            style="arc", outline=GAUGE_TRACK, width=thickness,
        )
        # Arc valeur (sens horaire depuis le haut)
        extent = -(self._percent / 100.0) * 360
        if abs(extent) > 0.5:
            self.create_arc(
                x0, y0, x1, y1, start=90, extent=extent,
                style="arc", outline=self._color, width=thickness,
            )
        # Texte central : grosse valeur
        cx, cy = w / 2, h / 2
        self.create_text(
            cx, cy - 6, text=self._label,
            font=("Segoe UI", 24, "bold"), fill="#222",
        )
        if self._sublabel:
            self.create_text(
                cx, cy + 22, text=self._sublabel,
                font=("Segoe UI", 9), fill=MUTED_TEXT,
            )


# ---------- Widget : bar de progression custom (Canvas) ----------

class _ProgressBar(tk.Canvas):
    def __init__(self, parent, height=18, **kw):
        super().__init__(parent, height=height, bg=GAUGE_TRACK,
                         highlightthickness=1, highlightbackground=CARD_BORDER, **kw)
        self._percent = 0.0
        self._show_text = True
        self.bind("<Configure>", lambda _e: self._redraw())

    def set_value(self, percent, show_text=True):
        self._percent = max(0.0, min(100.0, percent or 0.0))
        self._show_text = show_text
        self._redraw()

    def _redraw(self):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 4:
            return
        fill_w = (self._percent / 100.0) * w
        color = _color_for_pct(self._percent)
        if fill_w > 0:
            self.create_rectangle(0, 0, fill_w, h, fill=color, outline="")
        if self._show_text:
            self.create_text(
                w / 2, h / 2, text=f"{self._percent:.1f}%",
                font=("Segoe UI", 9, "bold"),
                fill="white" if self._percent > 35 else "#222",
            )


# ---------- Tab principal ----------

class PcInfoTab(tk.Frame):
    LIVE_REFRESH_MS = 1500
    GRAPH_HISTORY = 60

    def __init__(self, parent, app):
        super().__init__(parent, bg=WINDOW_BG, padx=10, pady=8)
        self.app = app
        self.data = None
        self._loading = False
        self._cpu_sampler = _CpuSampler()
        self._cpu_history = deque(maxlen=self.GRAPH_HISTORY)
        self._live_after_id = None
        self._disk_widgets = {}

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self._build_toolbar()
        self._build_main_area()

        self.after(150, self.refresh)
        self.after(700, self._tick_live)

    # ---------- Toolbar ----------

    def _build_toolbar(self):
        bar = tk.Frame(self, bg=WINDOW_BG)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        bar.columnconfigure(0, weight=1)

        tk.Label(
            bar, text="Informations PC", bg=WINDOW_BG, fg=ACCENT_BLUE,
            font=("Segoe UI", 14, "bold"),
        ).grid(row=0, column=0, sticky="w")

        self.status_var = tk.StringVar(value="")
        tk.Label(
            bar, textvariable=self.status_var, bg=WINDOW_BG, fg=MUTED_TEXT,
            font=("Segoe UI", 8),
        ).grid(row=0, column=1, sticky="e", padx=(0, 10))

        # Boutons Verifier compat + Exporter : grises tant que la collecte
        # PC n'a pas reussi (self.data = None). Evite un clic premature qui
        # afficherait des donnees vides ou planterait.
        self._compat_btn = tool_button(
            bar, text="Vérifier compatibilité Vega", command=self.run_compat_check,
            image=self.app.icons.get("verifier"), anchor="center",
            state="disabled",
        )
        self._compat_btn.grid(row=0, column=2, sticky="e", padx=(0, 4))

        self._export_btn = tool_button(
            bar, text="Exporter rapport", command=self.export_report,
            image=self.app.icons.get("journal"), anchor="center",
            state="disabled",
        )
        self._export_btn.grid(row=0, column=3, sticky="e", padx=(0, 4))

        tool_button(
            bar, text="Actualiser", command=self.refresh,
            image=self.app.icons.get("actualiser"), anchor="center",
        ).grid(row=0, column=4, sticky="e")

    def _build_main_area(self):
        self.main_frame = tk.Frame(self, bg=WINDOW_BG)
        self.main_frame.grid(row=1, column=0, sticky="nsew")
        self.main_frame.columnconfigure(0, weight=1)
        self.main_frame.rowconfigure(0, weight=1)

        # Loading view
        self.loading_view = tk.Frame(self.main_frame, bg=WINDOW_BG)
        self.loading_view.columnconfigure(0, weight=1)
        self.loading_view.rowconfigure(0, weight=1)
        self.loading_view.rowconfigure(3, weight=1)
        tk.Label(
            self.loading_view, text="⌛  Chargement des informations PC...",
            bg=WINDOW_BG, fg=ACCENT_BLUE, font=("Segoe UI", 16, "bold"),
        ).grid(row=1, column=0, pady=(0, 12))
        tk.Label(
            self.loading_view,
            text="Collecte des informations en cours, patientez quelques secondes...",
            bg=WINDOW_BG, fg=MUTED_TEXT, font=("Segoe UI", 9),
        ).grid(row=2, column=0, pady=(0, 16))
        self.loading_pb = ttk.Progressbar(
            self.loading_view, mode="indeterminate", length=320,
        )
        self.loading_pb.grid(row=3, column=0)

        # Content view : grid pur dans la tab, pas de scrollable canvas.
        # Chaque rangee a un rowconfigure weight pour partager la hauteur.
        self.content_view = tk.Frame(self.main_frame, bg=WINDOW_BG)
        self.content_view.columnconfigure(0, weight=1)
        self.content_view.rowconfigure(0, weight=1)

        self.dashboard = tk.Frame(self.content_view, bg=WINDOW_BG)
        self.dashboard.grid(row=0, column=0, sticky="nsew")
        # 4 colonnes egales
        for col in range(4):
            self.dashboard.columnconfigure(col, weight=1, uniform="dash")
        # 3 rangees : hero, identite, reseau+vega — partage equitable de la hauteur.
        # Hero (live monitoring) un peu plus haut, identite plus court, bas large.
        self.dashboard.rowconfigure(0, weight=3, minsize=180)  # hero
        self.dashboard.rowconfigure(1, weight=4, minsize=200)  # identite (plus de KVs)
        self.dashboard.rowconfigure(2, weight=4, minsize=200)  # reseau + vega

        self._show_loading()

    def _show_loading(self):
        self.content_view.grid_forget()
        self.loading_view.grid(row=0, column=0, sticky="nsew")
        try:
            self.loading_pb.start(8)
        except Exception:
            pass

    def _show_content(self):
        try:
            self.loading_pb.stop()
        except Exception:
            pass
        self.loading_view.grid_forget()
        self.content_view.grid(row=0, column=0, sticky="nsew")

    # ---------- Refresh ----------

    def refresh(self):
        if self._loading:
            return
        self._loading = True
        self.status_var.set("Collecte en cours...")
        # Re-grise les 2 boutons pendant la collecte : evite qu'un user les
        # active a partir des donnees du refresh precedent puis se prenne
        # une erreur si la nouvelle collecte echoue.
        self._set_action_buttons_enabled(False)
        self._show_loading()

        def on_success(data):
            self._loading = False
            self.data = data
            self._render_dashboard()
            self._show_content()
            self.status_var.set("Mise a jour : " + datetime.now().strftime("%H:%M:%S"))
            self._set_action_buttons_enabled(True)

        def on_error(exc):
            self._loading = False
            self.status_var.set(f"Echec : {exc}")
            for w in self.dashboard.winfo_children():
                w.destroy()
            tk.Label(
                self.dashboard, text=f"Erreur de collecte : {exc}",
                bg=WINDOW_BG, fg=ERROR_COLOR, font=("Segoe UI", 10),
                wraplength=600, justify="left",
            ).grid(padx=10, pady=10)
            self._show_content()
            # Boutons restent grises : self.data n'a pas ete mis a jour

        self.app.run_task(
            "Info PC", self.app.pc_info.collect,
            on_success=on_success, on_error=on_error,
            key="pc_info_collect",
        )

    def _set_action_buttons_enabled(self, enabled):
        # Active / desactive les boutons 'Verifier compatibilite' + 'Exporter'.
        state = "normal" if enabled else "disabled"
        for btn in (getattr(self, "_compat_btn", None), getattr(self, "_export_btn", None)):
            if btn is not None:
                try:
                    btn.configure(state=state)
                except tk.TclError:
                    pass

    # ---------- Dashboard rendering ----------

    def _render_dashboard(self):
        for w in self.dashboard.winfo_children():
            w.destroy()
        if not self.data:
            return

        # 3 colonnes egales pour la ligne hero (CPU | RAM | Disques)
        # 4 colonnes egales pour la grille d'identite (Systeme | OS | Mobo | GPU)
        # Reseau | Vega en bas, 2 colonnes
        # Ligne 0 : hero 3 colonnes
        self._render_hero_3col(row=0)
        # Ligne 1 : 4 cards d'identite compactes
        self._render_info_cards(start_row=1)
        # Ligne 2 : Reseau (gauche) | Vega (droite)
        self._render_network_and_vega(row=2)

    # --- HERO : 3 colonnes (CPU | RAM | Disques) ---

    def _render_hero_3col(self, row):
        hero = tk.Frame(self.dashboard, bg=WINDOW_BG)
        hero.grid(row=row, column=0, columnspan=4, sticky="nsew", pady=(0, 8))
        hero.rowconfigure(0, weight=1)
        for i in range(3):
            hero.columnconfigure(i, weight=1, uniform="hero")

        # --- CPU card (gauge + mini graph cote a cote) ---
        cpu_card, cpu_body = _make_card(hero, "Processeur — temps reel")
        cpu_card.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        cpu_body.columnconfigure(0, weight=0)
        cpu_body.columnconfigure(1, weight=1)
        self.cpu_gauge = _CircularGauge(cpu_body, size=110)
        self.cpu_gauge.grid(row=0, column=0, rowspan=3, padx=(0, 8))
        self.cpu_gauge.set_value(0, label="…", sublabel="")
        # CPU name + specs a droite de la gauge
        c = self.data.get("cpu", {}) or {}
        tk.Label(
            cpu_body, text=_fmt(c.get("Name")), bg=CARD_BG,
            font=("Segoe UI", 8, "bold"), anchor="w", justify="left",
            wraplength=180,
        ).grid(row=0, column=1, sticky="sw")
        cpu_info = f"{_fmt(c.get('Cores'))}c / {_fmt(c.get('Threads'))}t • {_fmt_mhz(c.get('MaxClockMHz'))}"
        tk.Label(
            cpu_body, text=cpu_info, bg=CARD_BG, fg=MUTED_TEXT,
            font=("Segoe UI", 8), anchor="w",
        ).grid(row=1, column=1, sticky="nw")
        # Mini graph CPU sous le bloc, hauteur fixe minimale (40 px)
        self.cpu_canvas = tk.Canvas(
            cpu_body, height=40, bg=WHITE_BG,
            highlightthickness=1, highlightbackground=CARD_BORDER,
        )
        self.cpu_canvas.grid(row=2, column=1, sticky="ew", pady=(4, 0))
        self.cpu_canvas.bind("<Configure>", lambda _e: self._redraw_cpu_graph())

        # --- RAM card ---
        ram_card, ram_body = _make_card(hero, "Memoire RAM — temps reel")
        ram_card.grid(row=0, column=1, sticky="nsew", padx=4)
        ram_body.columnconfigure(0, weight=0)
        ram_body.columnconfigure(1, weight=1)
        self.ram_gauge = _CircularGauge(ram_body, size=110)
        self.ram_gauge.grid(row=0, column=0, rowspan=3, padx=(0, 8))
        self.ram_gauge.set_value(0, label="…", sublabel="")
        s = self.data.get("system", {}) or {}
        ram_total = s.get("TotalPhysicalMemoryGB")
        tk.Label(
            ram_body, text=f"{_fmt_gb(ram_total)} installes", bg=CARD_BG,
            font=("Segoe UI", 9, "bold"), anchor="w",
        ).grid(row=0, column=1, sticky="sw")
        modules = (self.data.get("ram", {}) or {}).get("Modules") or []
        if modules:
            mtypes = sorted({m.get("MemoryType") for m in modules if m.get("MemoryType")})
            speeds = sorted({m.get("SpeedMHz") for m in modules if m.get("SpeedMHz")})
            ram_subtext = (
                f"{len(modules)} module(s)"
                + (f" • {'/'.join(str(t) for t in mtypes)}" if mtypes else "")
                + (f" • {max(speeds)} MHz" if speeds else "")
            )
        else:
            ram_subtext = "Modules indisponibles"
        tk.Label(
            ram_body, text=ram_subtext, bg=CARD_BG, fg=MUTED_TEXT,
            font=("Segoe UI", 8), anchor="w", wraplength=180,
        ).grid(row=1, column=1, sticky="nw")

        # --- Disques card (stack de bars compactes) ---
        ld = self.data.get("logicalDisks") or []
        disks_card, disks_body = _make_card(hero, "Disques — utilisation")
        disks_card.grid(row=0, column=2, sticky="nsew", padx=(4, 0))
        disks_body.columnconfigure(0, weight=1)
        self._disk_widgets = {}
        if not ld:
            tk.Label(disks_body, text="Aucun volume detecte.", bg=CARD_BG, fg=MUTED_TEXT).grid()
        else:
            for i, d in enumerate(ld):
                letter = d.get("DeviceID")
                if not letter:
                    continue
                row_frame = tk.Frame(disks_body, bg=CARD_BG)
                row_frame.grid(row=i, column=0, sticky="ew", pady=(0, 6))
                row_frame.columnconfigure(0, weight=1)
                head = tk.Frame(row_frame, bg=CARD_BG)
                head.grid(row=0, column=0, sticky="ew")
                head.columnconfigure(0, weight=1)
                label_text = f"{letter}"
                if d.get("VolumeName"):
                    label_text += f"  ({d['VolumeName']})"
                tk.Label(head, text=label_text, bg=CARD_BG,
                         font=("Segoe UI", 9, "bold"), anchor="w").grid(row=0, column=0, sticky="w")
                pct_var = tk.StringVar(value="—")
                tk.Label(head, textvariable=pct_var, bg=CARD_BG,
                         font=("Consolas", 9, "bold"), fg=ACCENT_BLUE).grid(row=0, column=1, sticky="e")
                bar = _ProgressBar(row_frame, height=14)
                bar.grid(row=1, column=0, sticky="ew", pady=(2, 0))
                detail_var = tk.StringVar(value="")
                tk.Label(row_frame, textvariable=detail_var, bg=CARD_BG, fg=MUTED_TEXT,
                         font=("Segoe UI", 7), anchor="w").grid(row=2, column=0, sticky="ew")
                self._disk_widgets[letter] = {
                    "pct_var": pct_var, "bar": bar, "detail_var": detail_var,
                }

    # --- Cards d'identite : 2 par ligne ---

    def _render_info_cards(self, start_row):
        # 4 cards d'identite sur UNE seule ligne (Systeme | OS | Mobo | GPU).
        sys_card, sys_body = _make_card(self.dashboard, "Systeme")
        sys_card.grid(row=start_row, column=0, sticky="nsew", padx=(0, 4), pady=(0, 8))
        self._fill_system(sys_body)

        os_card, os_body = _make_card(self.dashboard, "Systeme d'exploitation")
        os_card.grid(row=start_row, column=1, sticky="nsew", padx=4, pady=(0, 8))
        self._fill_os(os_body)

        mb_card, mb_body = _make_card(self.dashboard, "Carte mere & BIOS")
        mb_card.grid(row=start_row, column=2, sticky="nsew", padx=4, pady=(0, 8))
        self._fill_mobo(mb_body)

        gpu_card, gpu_body = _make_card(self.dashboard, "Carte graphique")
        gpu_card.grid(row=start_row, column=3, sticky="nsew", padx=(4, 0), pady=(0, 8))
        self._fill_gpu(gpu_body)

    def _kv(self, parent, row, key, value, color=None, bg=CARD_BG):
        tk.Label(
            parent, text=key, bg=bg, fg=MUTED_TEXT,
            font=("Segoe UI", 9), anchor="w",
        ).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=2)
        tk.Label(
            parent, text=value, bg=bg, fg=color or "#222",
            font=("Segoe UI", 9, "bold"), anchor="w",
            wraplength=320, justify="left",
        ).grid(row=row, column=1, sticky="w", pady=2)

    def _fill_system(self, body):
        s = self.data.get("system", {}) or {}
        body.columnconfigure(1, weight=1)
        self._kv(body, 0, "Nom du poste", _fmt(s.get("Hostname")))
        net_key = "Domaine" if s.get("PartOfDomain") else "Workgroup"
        net_val = _fmt(s.get("Domain") if s.get("PartOfDomain") else (s.get("Workgroup") or s.get("Domain")))
        self._kv(body, 1, net_key, net_val)
        self._kv(body, 2, "Constructeur", _fmt(s.get("Manufacturer")))
        self._kv(body, 3, "Modele", _fmt(s.get("Model")))
        self._kv(body, 4, "Architecture", _fmt(s.get("SystemType")))
        self._kv(body, 5, "Utilisateur", _fmt(s.get("CurrentUser")))

    def _fill_os(self, body):
        o = self.data.get("os", {}) or {}
        body.columnconfigure(1, weight=1)
        self._kv(body, 0, "Edition", _fmt(o.get("Caption")))
        self._kv(body, 1, "Version", f"{_fmt(o.get('Version'))} (build {_fmt(o.get('BuildNumber'))})")
        self._kv(body, 2, "Architecture", _fmt(o.get("OSArchitecture")))
        self._kv(body, 3, "Langue", _fmt(o.get("Language")))
        self._kv(body, 4, "Installe le", _fmt(o.get("InstallDate")))
        self._kv(body, 5, "Dernier demarrage", _fmt(o.get("LastBootUpTime")))
        self._kv(body, 6, "Uptime", _fmt(o.get("Uptime")), color=OK_COLOR)

    def _fill_mobo(self, body):
        b = self.data.get("bios", {}) or {}
        m = self.data.get("motherboard", {}) or {}
        body.columnconfigure(1, weight=1)
        self._kv(body, 0, "Carte mere", f"{_fmt(m.get('Manufacturer'))} {_fmt(m.get('Product'))}")
        self._kv(body, 1, "  Version", _fmt(m.get("Version")))
        self._kv(body, 2, "  N° serie", _fmt(m.get("SerialNumber")))
        self._kv(body, 3, "BIOS", _fmt(b.get("Manufacturer")))
        self._kv(body, 4, "  Version", _fmt(b.get("Version")))
        self._kv(body, 5, "  Date", _fmt(b.get("ReleaseDate")))
        self._kv(body, 6, "  N° serie", _fmt(b.get("SerialNumber")))

    def _fill_gpu(self, body):
        gpus = self.data.get("gpus") or []
        body.columnconfigure(1, weight=1)
        if not gpus:
            tk.Label(body, text="Aucun GPU detecte.", bg=CARD_BG, fg=MUTED_TEXT).grid(row=0, column=0)
            return
        for i, g in enumerate(gpus):
            base = i * 4
            self._kv(body, base + 0, f"GPU #{i+1}", _fmt(g.get("Name")), color=ACCENT_BLUE)
            vram = g.get("VRAMMB")
            if vram:
                self._kv(body, base + 1, "  VRAM", f"{int(vram)} Mo")
            self._kv(body, base + 2, "  Driver", f"{_fmt(g.get('DriverVersion'))} ({_fmt(g.get('DriverDate'))})")
            if g.get("VideoMode"):
                self._kv(body, base + 3, "  Mode video", _fmt(g.get("VideoMode")))

    # --- Ligne bas : Reseau (large) | Vega (large) ---

    def _render_network_and_vega(self, row):
        # 2 cards cote a cote : Reseau a gauche, Vega a droite (2+2 colonnes).
        nics = self.data.get("network") or []
        if nics:
            net_card, net_body = _make_card(self.dashboard, f"Adaptateurs reseau ({len(nics)})")
            net_card.grid(row=row, column=0, columnspan=2, sticky="nsew", padx=(0, 4))
            net_body.columnconfigure(0, weight=1)
            for i, n in enumerate(nics):
                cell = tk.Frame(net_body, bg=CARD_BG)
                cell.grid(row=i, column=0, sticky="ew", pady=(0, 6))
                cell.columnconfigure(1, weight=1)
                tk.Label(cell, text=_fmt(n.get("Name") or n.get("Description")),
                         bg=CARD_BG, font=("Segoe UI", 9, "bold"), fg=ACCENT_BLUE,
                         anchor="w").grid(row=0, column=0, columnspan=2, sticky="w")
                self._kv(cell, 1, "  MAC", _fmt(n.get("MAC")), bg=CARD_BG)
                self._kv(cell, 2, "  IPv4", ", ".join(n.get("IPv4") or []) or "—", bg=CARD_BG)
                gw = n.get("Gateway") or []
                if gw:
                    self._kv(cell, 3, "  Passerelle", ", ".join(gw), bg=CARD_BG)
                mode = "DHCP" if n.get("DHCPEnabled") else "Statique"
                self._kv(cell, 4, "  Mode", mode, bg=CARD_BG,
                         color=OK_COLOR if not n.get("DHCPEnabled") else None)

        # Vega : 3 sous-blocs a l'interieur (HFSQL / partages / installations)
        v = self.data.get("vega", {}) or {}
        hfsql = v.get("HFSQL")
        shares = v.get("Shares") or []
        exes = v.get("Exes") or []
        vega_card, vega_body = _make_card(self.dashboard, "Contexte Vega")
        vega_card.grid(row=row, column=2, columnspan=2, sticky="nsew", padx=(4, 0))
        for c in range(3):
            vega_body.columnconfigure(c, weight=1, uniform="vega")

        # Bloc HFSQL
        hf_cell = tk.Frame(vega_body, bg=CARD_BG)
        hf_cell.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        hf_cell.columnconfigure(0, weight=1)
        tk.Label(hf_cell, text="Service HFSQL", bg=CARD_BG, font=("Segoe UI", 9, "bold"),
                 fg=ACCENT_BLUE, anchor="w").grid(row=0, column=0, sticky="w")
        if hfsql:
            status = hfsql.get("Status")
            color = OK_COLOR if status == "Running" else (WARN_COLOR if status == "Stopped" else ERROR_COLOR)
            tk.Label(hf_cell, text=status, bg=CARD_BG, fg=color,
                     font=("Segoe UI", 14, "bold"), anchor="w").grid(row=1, column=0, sticky="w", pady=(2, 0))
            tk.Label(hf_cell, text=_fmt(hfsql.get("DisplayName")), bg=CARD_BG, fg=MUTED_TEXT,
                     font=("Segoe UI", 8), anchor="w", wraplength=160, justify="left").grid(row=2, column=0, sticky="w")
            tk.Label(hf_cell, text=f"Demarrage : {_fmt(hfsql.get('StartType'))}", bg=CARD_BG,
                     fg=MUTED_TEXT, font=("Segoe UI", 7), anchor="w").grid(row=3, column=0, sticky="w")
        else:
            tk.Label(hf_cell, text="Non detecte", bg=CARD_BG, fg=MUTED_TEXT,
                     font=("Segoe UI", 10, "bold"), anchor="w").grid(row=1, column=0, sticky="w", pady=2)

        # Bloc partages
        sh_cell = tk.Frame(vega_body, bg=CARD_BG)
        sh_cell.grid(row=0, column=1, sticky="nsew", padx=4)
        sh_cell.columnconfigure(0, weight=1)
        tk.Label(sh_cell, text=f"Partages ({len(shares)})", bg=CARD_BG,
                 font=("Segoe UI", 9, "bold"), fg=ACCENT_BLUE, anchor="w").grid(row=0, column=0, sticky="w")
        if shares:
            for i, s in enumerate(shares):
                tk.Label(sh_cell, text=f"\\\\{s.get('Name')}", bg=CARD_BG,
                         font=("Segoe UI", 9, "bold"), anchor="w").grid(
                    row=1 + i * 2, column=0, sticky="w", pady=(2, 0),
                )
                tk.Label(
                    sh_cell,
                    text=f"  {s.get('CurrentUsers', 0)} session(s)\n  {s.get('OpenFiles', 0)} fichier(s)",
                    bg=CARD_BG, fg=MUTED_TEXT, font=("Segoe UI", 7),
                    anchor="w", justify="left", wraplength=160,
                ).grid(row=2 + i * 2, column=0, sticky="w")
        else:
            tk.Label(sh_cell, text="Aucun", bg=CARD_BG, fg=MUTED_TEXT,
                     font=("Segoe UI", 9), anchor="w").grid(row=1, column=0, sticky="w", pady=2)

        # Bloc installations
        ex_cell = tk.Frame(vega_body, bg=CARD_BG)
        ex_cell.grid(row=0, column=2, sticky="nsew", padx=(4, 0))
        ex_cell.columnconfigure(0, weight=1)
        tk.Label(ex_cell, text=f"Installations ({len(exes)})", bg=CARD_BG,
                 font=("Segoe UI", 9, "bold"), fg=ACCENT_BLUE, anchor="w").grid(row=0, column=0, sticky="w")
        if exes:
            for i, e in enumerate(exes):
                tk.Label(ex_cell, text=f"v{_fmt(e.get('Version'))}", bg=CARD_BG,
                         font=("Segoe UI", 9, "bold"), anchor="w").grid(
                    row=1 + i * 2, column=0, sticky="w", pady=(2, 0),
                )
                tk.Label(ex_cell, text=_fmt(e.get("Path")), bg=CARD_BG, fg=MUTED_TEXT,
                         font=("Segoe UI", 7), anchor="w", wraplength=160,
                         justify="left").grid(row=2 + i * 2, column=0, sticky="w")
        else:
            tk.Label(ex_cell, text="Aucune", bg=CARD_BG, fg=MUTED_TEXT,
                     font=("Segoe UI", 9), anchor="w").grid(row=1, column=0, sticky="w", pady=2)

    # ---------- Live tick ----------

    def _tick_live(self):
        try:
            if getattr(self.app, "current_module_key", None) != "pc_info":
                self._live_after_id = self.after(1000, self._tick_live)
                return

            cpu_pct = self._cpu_sampler.sample()
            if cpu_pct is not None and hasattr(self, "cpu_gauge"):
                self._cpu_history.append(cpu_pct)
                c = self.data.get("cpu", {}) or {}
                clock = c.get("CurrentClockMHz") or c.get("MaxClockMHz")
                sub = f"{int(clock)} MHz" if clock else ""
                self.cpu_gauge.set_value(cpu_pct, label=f"{cpu_pct:.0f}%", sublabel=sub)
                self._redraw_cpu_graph()

            mem = _get_memory_status()
            if mem and hasattr(self, "ram_gauge"):
                self.ram_gauge.set_value(
                    mem["percent"],
                    label=f"{mem['percent']:.0f}%",
                    sublabel=f"{mem['used_gb']:.1f} / {mem['total_gb']:.1f} Go",
                )

            for letter, refs in self._disk_widgets.items():
                info = _get_disk_free(letter + "\\")
                if not info:
                    continue
                refs["pct_var"].set(f"{info['percent']:.1f}%")
                refs["detail_var"].set(
                    f"{info['used_gb']:.1f} Go utilises • {info['free_gb']:.1f} Go libres • {info['total_gb']:.1f} Go total"
                )
                refs["bar"].set_value(info["percent"], show_text=False)
        except tk.TclError:
            return
        except Exception:
            pass
        self._live_after_id = self.after(self.LIVE_REFRESH_MS, self._tick_live)

    # ---------- CPU graph drawing ----------

    def _redraw_cpu_graph(self):
        if not hasattr(self, "cpu_canvas"):
            return
        c = self.cpu_canvas
        c.delete("all")
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 10 or h < 10:
            return
        # Grille horizontale
        for i in range(1, 5):
            y = h - (i * h / 4)
            c.create_line(0, y, w, y, fill="#eef1f7")
            c.create_text(w - 4, y, anchor="ne", text=f"{i*25}%", fill="#aaa", font=("Segoe UI", 7))
        if len(self._cpu_history) >= 2:
            pts = []
            for i, v in enumerate(self._cpu_history):
                x = (i / max(self.GRAPH_HISTORY - 1, 1)) * w
                y = h - (v / 100.0) * h
                pts.extend([x, y])
            # Aire bleue translucide
            fill_pts = [pts[0], h] + pts + [pts[-2], h]
            c.create_polygon(fill_pts, fill="#cfe0ff", outline="")
            c.create_line(pts, fill=ACCENT_BLUE, width=2, smooth=True)

    # ---------- Compatibility check ----------

    def run_compat_check(self):
        # Etape 1 : demander le profil (Serveur Vega ou Poste de travail).
        if not self.data:
            self.status_var.set("Collecte des infos PC d'abord...")
            self.refresh()
            return
        profile = self._prompt_profile()
        if not profile:
            return
        # Etape 2 : run le check + ouvrir popup resultats.
        from vega_backend.compatibility import check_compatibility
        overall, criteria = check_compatibility(self.data, profile=profile)
        # Audit
        try:
            self.app.audit.log_action(
                "vega_compatibility_check",
                status="success" if overall == "ok" else ("warning" if overall == "warning" else "failure"),
                details={"profil": profile, "resultat_global": overall, "nb_criteres": len(criteria)},
            )
        except Exception:
            pass
        self._show_compat_result(profile, overall, criteria)

    def _prompt_profile(self):
        # Popup demandant le profil. Retourne "server" | "workstation" | None.
        # Highlight bleu autour de la carte recommandee (Windows Server ->
        # Serveur, sinon -> Poste de travail) avec un tag "Suggere" en
        # chevauchement sur la bordure superieure (place() y=0 anchor=n).
        result = {"value": None}
        os_caption = ((self.data or {}).get("os") or {}).get("Caption") or ""
        suggested = "server" if "server" in os_caption.lower() else "workstation"

        dialog = tk.Toplevel(self)
        try:
            from ..resources import set_window_icon
            set_window_icon(dialog)
        except Exception:
            pass
        dialog.title("Profil du poste")
        dialog.configure(bg=WINDOW_BG)
        dialog.transient(self.winfo_toplevel())
        dialog.resizable(False, False)

        host = tk.Frame(dialog, bg=WINDOW_BG, padx=26, pady=18)
        host.pack(fill="both", expand=True)
        # Colonnes uniformes pour que les 2 cartes aient EXACTEMENT la meme largeur.
        host.columnconfigure(0, weight=1, uniform="cards")
        host.columnconfigure(1, weight=1, uniform="cards")

        tk.Label(
            host, text="Vérification de compatibilité Vega",
            bg=WINDOW_BG, fg=ACCENT_BLUE, font=("Segoe UI", 14, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 4))
        tk.Label(
            host,
            text="Indiquez la nature de ce poste :",
            bg=WINDOW_BG, font=("Segoe UI", 10),
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 16))

        def pick(profile):
            result["value"] = profile
            dialog.destroy()

        def build_card(parent, profile_key, icon, label):
            is_suggested = (profile_key == suggested)
            # Cellule wrapper : meme padding haut pour LES 2 cartes (16px),
            # ce qui garantit que la base des cartes est alignee et que le
            # tag 'Suggere' a une place naturelle au-dessus de la carte
            # suggeree (sans tag dans l'autre cellule, juste de l'espace).
            cell = tk.Frame(parent, bg=WINDOW_BG)
            border_color = ACCENT_BLUE if is_suggested else CARD_BORDER
            border_thick = 3 if is_suggested else 1
            card = tk.Frame(
                cell, bg=WHITE_BG, bd=0,
                highlightbackground=border_color,
                highlightcolor=border_color,
                highlightthickness=border_thick,
                padx=14, pady=22,
            )
            card.pack(fill="both", expand=True, pady=(16, 0))
            # Contenu : icon + titre, puis spacer extensible, puis bouton
            # centre en bas. expand=True sur le spacer pousse le bouton bas
            # quel que soit la taille de la carte → buttons alignes entre
            # les 2 cartes (memes elements, donc memes hauteurs).
            tk.Label(
                card, text=f"{icon}  {label}", bg=WHITE_BG,
                font=("Segoe UI", 14, "bold"), fg=ACCENT_BLUE,
            ).pack(anchor="center", pady=(4, 14))
            tk.Frame(card, bg=WHITE_BG).pack(fill="both", expand=True)
            tool_button(
                card, text=f"Choisir {label}",
                command=lambda: pick(profile_key), anchor="center",
            ).pack(anchor="center", pady=(0, 4))

            # Tag "Recommandé" en chevauchement sur la bordure superieure
            if is_suggested:
                tag_outer = tk.Frame(cell, bg=ACCENT_BLUE)
                tk.Label(
                    tag_outer, text="  Recommandé  ",
                    bg=ACCENT_BLUE, fg="white",
                    font=("Segoe UI", 9, "bold"), padx=8, pady=3,
                ).pack()
                # y=4 anchor=n -> haut centre, chevauche le bord superieur
                tag_outer.place(relx=0.5, y=4, anchor="n")
            return cell

        srv_cell = build_card(host, "server", "🖥", "Serveur")
        srv_cell.grid(row=2, column=0, sticky="nsew", padx=(0, 8), pady=(0, 16))
        ws_cell = build_card(host, "workstation", "💻", "Poste de travail")
        ws_cell.grid(row=2, column=1, sticky="nsew", padx=(8, 0), pady=(0, 16))

        # Annuler centre
        tool_button(host, text="Annuler", command=dialog.destroy,
                    anchor="center", width=14).grid(row=3, column=0, columnspan=2)

        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.bind("<Escape>", lambda _e: dialog.destroy())
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

    def _show_compat_result(self, profile, overall, criteria):
        # Popup avec table 3 colonnes (Composant / Configuration détectée / Statut).
        # Si un critère n'est pas OK, un bouton 'En savoir plus' apparaît en
        # bout de ligne et ouvre une mini-popup avec le détail des prérequis
        # et la recommandation. Plus de colonne 'Attendu' qui était redondante
        # et pas pro.
        overall_label = {
            "ok":      "Conforme",
            "warning": "Conforme avec améliorations possibles",
            "fail":    "Non conforme",
        }.get(overall, overall)
        overall_color = {
            "ok":      OK_COLOR,
            "warning": WARN_COLOR,
            "fail":    ERROR_COLOR,
        }.get(overall, "#222")
        profile_label = "Serveur" if profile == "server" else "Poste de travail"

        dialog = tk.Toplevel(self)
        try:
            from ..resources import set_window_icon
            set_window_icon(dialog)
        except Exception:
            pass
        dialog.title(f"Compatibilité Vega — {profile_label}")
        dialog.configure(bg=WINDOW_BG)
        dialog.transient(self.winfo_toplevel())

        host = tk.Frame(dialog, bg=WINDOW_BG, padx=20, pady=16)
        host.pack(fill="both", expand=True)
        host.columnconfigure(0, weight=1)

        # Header
        header = tk.Frame(host, bg=WINDOW_BG)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        header.columnconfigure(0, weight=1)
        tk.Label(header, text=f"Profil évalué : {profile_label}",
                 bg=WINDOW_BG, fg=MUTED_TEXT, font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w")
        tk.Label(header, text=overall_label, bg=WINDOW_BG, fg=overall_color,
                 font=("Segoe UI", 16, "bold")).grid(row=1, column=0, sticky="w", pady=(2, 0))

        # Tableau critères
        table_host = tk.Frame(host, bg=CARD_BORDER, bd=0)
        table_host.grid(row=1, column=0, sticky="nsew")
        host.rowconfigure(1, weight=1)
        table_host.columnconfigure(1, weight=1)  # Détecté prend la place restante

        # Header table : 3 colonnes (Composant / Detecte / Statut).
        # Le 'En savoir plus' est inline dans la cellule Statut (icone ⓘ
        # a cote du badge, avec tooltip au survol) - plus de 4e colonne vide.
        cols = [
            ("Composant", 0),
            ("Configuration détectée", 1),
            ("Statut", 0),
        ]
        for c, (label, weight) in enumerate(cols):
            tk.Label(table_host, text=label, bg=HEADER_BG, fg=HEADER_FG,
                     font=("Segoe UI", 9, "bold"), padx=10, pady=8, anchor="w",
                     ).grid(row=0, column=c, sticky="ew", padx=(0, 1), pady=(0, 1))

        for i, crit in enumerate(criteria, start=1):
            bg = {"ok": "#f5fbf3", "warning": "#fffaeb", "fail": "#fef0f0"}.get(crit["status"], CARD_BG)
            fg = {"ok": OK_COLOR, "warning": WARN_COLOR, "fail": ERROR_COLOR}.get(crit["status"], "#222")
            badge_text = {
                "ok":      "✓ Conforme",
                "warning": "⚠ Acceptable",
                "fail":    "✕ Non conforme",
            }.get(crit["status"], crit["status"])

            tk.Label(table_host, text=crit["label"], bg=bg, anchor="w",
                     font=("Segoe UI", 9, "bold"), padx=10, pady=8,
                     ).grid(row=i, column=0, sticky="ew", padx=(0, 1), pady=(0, 1))
            tk.Label(table_host, text=crit["detected"], bg=bg, anchor="w",
                     font=("Segoe UI", 9), padx=10, pady=8, wraplength=420, justify="left",
                     ).grid(row=i, column=1, sticky="ew", padx=(0, 1), pady=(0, 1))

            # Cellule statut : badge + icone ⓘ (si pas OK) inline
            status_cell = tk.Frame(table_host, bg=bg)
            status_cell.grid(row=i, column=2, sticky="ew", padx=(0, 1), pady=(0, 1))
            inner = tk.Frame(status_cell, bg=bg, padx=10, pady=8)
            inner.pack(anchor="w")
            tk.Label(inner, text=badge_text, bg=bg, fg=fg,
                     font=("Segoe UI", 9, "bold"),
                     ).pack(side="left")
            if crit["status"] != "ok":
                info_lbl = tk.Label(
                    inner, text=" ⓘ", bg=bg, fg=ACCENT_BLUE,
                    font=("Segoe UI", 11, "bold"), cursor="hand2",
                )
                info_lbl.pack(side="left", padx=(4, 0))
                _Tooltip(info_lbl, _build_tooltip_text(crit))

        # Bouton fermer
        tool_button(host, text="Fermer", command=dialog.destroy,
                    anchor="center", width=14).grid(row=2, column=0, pady=(16, 0))

        dialog.update_idletasks()
        parent = self.winfo_toplevel()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        dw = max(dialog.winfo_width(), 900)
        dh = dialog.winfo_height()
        dialog.geometry(f"{dw}x{dh}+{px + max((pw - dw) // 2, 0)}+{py + max((ph - dh) // 2, 0)}")
        dialog.grab_set()
        dialog.focus_force()


    # ---------- Export rapport ----------

    def export_report(self):
        # Genere un fichier HTML autonome (CSS inline) avec toutes les infos
        # PC + un check de compatibilite des 2 profils, puis ouvre dans le
        # navigateur par defaut. L'utilisateur peut faire Ctrl+P -> Save as PDF.
        if not self.data:
            self.status_var.set("Collecte des infos PC d'abord...")
            self.refresh()
            return


        from datetime import datetime
        from pathlib import Path
        import sys as _sys
        import tempfile
        import webbrowser

        # Cible : dossier de l'exe en mode frozen, sinon temp.
        if getattr(_sys, "frozen", False):
            out_dir = Path(_sys.executable).parent
        else:
            out_dir = Path(tempfile.gettempdir())
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = out_dir / f"rapport_info_pc_{ts}.html"

        # Compat pour les 2 profils, integree au rapport
        try:
            from vega_backend.compatibility import check_compatibility
            compat = {
                "workstation": check_compatibility(self.data, profile="workstation"),
                "server": check_compatibility(self.data, profile="server"),
            }
        except Exception:
            compat = None

        html = _build_report_html(self.data, compat=compat)
        try:
            out_path.write_text(html, encoding="utf-8")
        except Exception as exc:
            self.status_var.set(f"Echec ecriture rapport : {exc}")
            return
        # Ouvre dans le navigateur par defaut
        try:
            webbrowser.open(out_path.as_uri())
        except Exception:
            pass
        self.status_var.set(f"Rapport genere : {out_path.name}")
        try:
            self.app.audit.log_action(
                "info_pc_report_export",
                status="success",
                details={"file": str(out_path)},
            )
        except Exception:
            pass


def _build_report_html(pc_data, compat=None):
    # Rapport HTML autonome - design premium pensé pour l'impression PDF.
    # Structure : toolbar (non imprimable) -> cover header avec hostname en
    # gros + verdict de compatibilite au premier coup d'oeil -> sections
    # detaillees en cards. Page breaks controles pour eviter les pages vides.
    from datetime import datetime
    import html as _h
    s = pc_data.get("system") or {}
    o = pc_data.get("os") or {}
    c = pc_data.get("cpu") or {}
    r = pc_data.get("ram") or {}
    gpus = pc_data.get("gpus") or []
    bios = pc_data.get("bios") or {}
    mobo = pc_data.get("motherboard") or {}
    ldisks = pc_data.get("logicalDisks") or []
    nics = pc_data.get("network") or []
    v = pc_data.get("vega") or {}

    def row(k, val):
        return f"<tr><th>{_h.escape(str(k))}</th><td>{_h.escape(str(val) if val is not None else '—')}</td></tr>"

    # Dedoublonnage des modules RAM strictement identiques (Slot+Capacite+
    # Vitesse+Type+Constructeur+PartNumber). Beaucoup de PC OEM remontent
    # 8 lignes identiques pour 2 barrettes - on consolide avec une colonne
    # Qte. Seuls les modules avec exactement les memes attributs sont
    # fusionnes ; deux barrettes de marques differentes restent separees.
    ram_modules_raw = r.get("Modules") or []
    ram_groups = []  # liste de (count, module_dict)
    for m in ram_modules_raw:
        key = (
            m.get("Slot"), m.get("CapacityGB"), m.get("SpeedMHz"),
            m.get("MemoryType"), m.get("Manufacturer"), m.get("PartNumber"),
        )
        if ram_groups and ram_groups[-1][1] == key:
            ram_groups[-1] = (ram_groups[-1][0] + 1, key)
        else:
            ram_groups.append((1, key))
    ram_modules_rows = ""
    for count, key in ram_groups:
        slot, cap, speed, mtype, manu, part = key
        qty_label = f"× {count}" if count > 1 else ""
        # Le fragment est calcule hors f-string : une expression de f-string ne
        # peut pas contenir d'antislash avant Python 3.12, et le build legacy
        # est compile en Python 3.8.
        qty_html = ' <span class="qty">' + qty_label + "</span>" if qty_label else ""
        ram_modules_rows += (
            f"<tr>"
            f"<td>{_h.escape(slot or '?')}{qty_html}</td>"
            f"<td>{_h.escape(str(cap or '?'))} Go</td>"
            f"<td>{_h.escape(str(speed or '?'))} MHz</td>"
            f"<td>{_h.escape(mtype or '?')}</td>"
            f"</tr>"
        )

    disk_rows = ""
    for d in ldisks:
        used = d.get("UsedPercent")
        klass = ""
        if used and used > 90:
            klass = "err"
        elif used and used > 75:
            klass = "warn"
        disk_rows += (
            f"<tr class='{klass}'><td>{_h.escape(d.get('DeviceID') or '?')}</td>"
            f"<td>{_h.escape(d.get('VolumeName') or '')}</td>"
            f"<td>{_h.escape(str(d.get('SizeGB') or '?'))} Go</td>"
            f"<td>{_h.escape(str(d.get('FreeGB') or '?'))} Go</td>"
            f"<td>{used if used is not None else '?'}%</td></tr>"
        )

    nic_rows = ""
    for n in nics:
        ipv4 = ", ".join(n.get("IPv4") or []) or "—"
        nic_rows += (
            f"<tr><td>{_h.escape(n.get('Name') or n.get('Description') or '?')}</td>"
            f"<td>{_h.escape(n.get('MAC') or '?')}</td>"
            f"<td>{_h.escape(ipv4)}</td>"
            f"<td>{'DHCP' if n.get('DHCPEnabled') else 'Statique'}</td></tr>"
        )

    gpu_rows = ""
    for g in gpus:
        gpu_rows += (
            f"<tr><td>{_h.escape(g.get('Name') or '?')}</td>"
            f"<td>{_h.escape(str(g.get('VRAMMB') or '?'))} Mo</td>"
            f"<td>{_h.escape(g.get('DriverVersion') or '?')}</td></tr>"
        )

    # ----------- Bloc compatibilite (2 profils) -----------
    profile_labels = {"workstation": "Poste de travail", "server": "Serveur Vega"}
    status_text = {"ok": "Conforme", "warning": "Acceptable", "warn": "Acceptable",
                   "fail": "Non conforme"}
    status_cls_map = {"ok": "ok", "warning": "warn", "warn": "warn", "fail": "fail"}

    def _profile_card(profile_key, overall, criteria):
        cls_overall = status_cls_map.get(overall, "warn")
        criteria_items = ""
        n_ok = n_warn = n_fail = 0
        for crit in (criteria or []):
            st = crit.get("status") or "warn"
            cls = status_cls_map.get(st, "warn")
            if cls == "ok":
                n_ok += 1
                icon = "✓"
            elif cls == "fail":
                n_fail += 1
                icon = "✗"
            else:
                n_warn += 1
                icon = "≈"
            criteria_items += (
                f"<li class='crit-item {cls}'>"
                f"<span class='crit-icon'>{icon}</span>"
                f"<div class='crit-body'>"
                f"<div class='crit-label'>{_h.escape(crit.get('label') or '')}</div>"
                f"<div class='crit-detected'>{_h.escape(crit.get('detected') or '—')}</div>"
                f"<div class='crit-req'>Attendu : {_h.escape(crit.get('requirement_short') or '—')}</div>"
                f"</div>"
                f"</li>"
            )
        return (
            f"<article class='profile-card {cls_overall}'>"
            f"<header class='profile-head'>"
            f"<div class='profile-title'>"
            f"<div class='profile-eyebrow'>Profil</div>"
            f"<h3>{_h.escape(profile_labels.get(profile_key, profile_key))}</h3>"
            f"</div>"
            f"<div class='profile-verdict'>"
            f"<div class='verdict-pill {cls_overall}'>{status_text.get(overall, '?')}</div>"
            f"<div class='verdict-counts'>"
            f"<span class='count ok'>{n_ok}<small>OK</small></span>"
            f"<span class='count warn'>{n_warn}<small>≈</small></span>"
            f"<span class='count fail'>{n_fail}<small>✗</small></span>"
            f"</div>"
            f"</div>"
            f"</header>"
            f"<ul class='criteria'>{criteria_items}</ul>"
            f"</article>"
        )

    if compat:
        ws_overall, ws_crit = compat.get("workstation", ("warn", []))
        sv_overall, sv_crit = compat.get("server", ("warn", []))
        compat_html = (
            _profile_card("workstation", ws_overall, ws_crit)
            + _profile_card("server", sv_overall, sv_crit)
        )
    else:
        compat_html = "<p class='hint'>Compatibilité non calculée.</p>"

    hostname = _h.escape(s.get('Hostname') or '?')
    domain_val = s.get("Domain") if s.get("PartOfDomain") else (s.get("Workgroup") or s.get("Domain"))
    # At-a-glance stats (4 chips dans le cover header)
    cpu_short = (c.get("Name") or "—").split("@")[0].strip()
    os_short = (o.get("Caption") or "—").replace("Microsoft ", "").strip()
    ram_short = f"{s.get('TotalPhysicalMemoryGB') or '?'} Go"
    disk_short = "—"
    if ldisks:
        d0 = ldisks[0]
        disk_short = f"{d0.get('SizeGB') or '?'} Go ({d0.get('UsedPercent') or '?'}%)"

    manufacturer_model = " ".join(
        x for x in [(s.get("Manufacturer") or "").strip(), (s.get("Model") or "").strip()] if x
    ) or "—"

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<title>Rapport Info PC - {hostname}</title>
<style id="page-style">
@page {{
  size: A4 portrait; margin: 15mm 15mm 18mm 15mm;
  /* Paged Media : Chrome/Edge supportent les zones @top-* et @bottom-* */
  @bottom-left {{
    content: "Vega Toolbox · Rapport Info PC · {hostname}";
    font-family: 'Segoe UI', Tahoma, sans-serif; font-size: 8pt; color: #6b7280;
  }}
  @bottom-right {{
    content: "Page " counter(page) " / " counter(pages);
    font-family: 'Segoe UI', Tahoma, sans-serif; font-size: 8pt; color: #6b7280;
  }}
}}
</style>
<style>
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; }}
  /* ============================================================
     A4 print-first : tout est dimensionne en pt (typo) et mm (espace
     page). Sur ecran on simule une page A4 a peu pres a echelle.
     Voir bonnes pratiques @page / paged media :
     https://developer.mozilla.org/en-US/docs/Web/CSS/@page
     ============================================================ */
  body {{
    font-family: 'Segoe UI', Tahoma, Arial, sans-serif;
    color: #1f2937; background: #e5e7eb;
    font-size: 10.5pt; line-height: 1.4;
    orphans: 3; widows: 3;
    -webkit-print-color-adjust: exact; print-color-adjust: exact; color-adjust: exact;
  }}

  /* Toolbar (non imprimable, en px car ecran-only) */
  .toolbar {{
    position: sticky; top: 0; z-index: 100;
    background: #0a246a; color: #fff;
    padding: 8px 16px; display: flex; align-items: center; gap: 8px;
    border-bottom: 1px solid #061a4f;
    font-size: 12px;
  }}
  .toolbar .title {{ flex: 1; font-weight: 600; }}
  .toolbar button {{
    background: #fff; color: #0a246a; border: 1px solid #cbd5e1;
    padding: 5px 12px; font-weight: 600; cursor: pointer;
    font-family: inherit; font-size: 12px;
  }}
  .toolbar button:hover {{ background: #f1f5f9; }}
  .toolbar .seg {{ display: inline-flex; }}
  .toolbar .seg button {{ border-radius: 0; }}
  .toolbar .seg button + button {{ border-left: 0; }}
  .toolbar .seg button.active {{ background: #0a246a; color: #fff; border-color: #061a4f; }}

  /* Document - simule une page A4 en portrait par defaut.
     A4 portrait : 210mm x 297mm. Avec 15mm de marges -> aire utile 180mm x 267mm.
     A4 paysage  : 297mm x 210mm. Avec 15mm de marges -> aire utile 267mm x 180mm. */
  .doc {{
    width: 180mm; margin: 6mm auto; padding: 0;
    background: #fff; border: 1px solid #cbd5e1;
    min-height: 267mm;
  }}
  body.landscape .doc {{ width: 267mm; min-height: 180mm; }}

  /* En-tete document */
  .doc-head {{
    padding: 5mm 7mm;
    background: #fafbfc;
    border-bottom: 0.6mm solid #0a246a;
  }}
  .doc-head .eyebrow {{
    font-size: 7.5pt; color: #6b7280; text-transform: uppercase;
    letter-spacing: 1.2px; font-weight: 700; margin-bottom: 1.5mm;
  }}
  .doc-head h1 {{
    margin: 0; font-size: 22pt; font-weight: 700; color: #0a246a;
    letter-spacing: -.3px; line-height: 1.1;
  }}
  .doc-head .sub {{ margin-top: 1.5mm; font-size: 10pt; color: #4b5563; font-weight: 500; }}
  .doc-meta {{
    margin-top: 3mm; display: flex; flex-wrap: wrap;
    gap: 5mm; font-size: 8.5pt; color: #6b7280;
  }}
  .doc-meta strong {{ color: #1f2937; font-weight: 600; }}

  /* Synthese (4 colonnes) */
  .summary {{
    display: grid; grid-template-columns: repeat(4, 1fr);
    border-top: 0.3mm solid #e5e7eb;
  }}
  .summary > div {{ padding: 3mm 4mm; border-right: 0.3mm solid #e5e7eb; }}
  .summary > div:last-child {{ border-right: 0; }}
  .summary .lbl {{
    font-size: 7.5pt; color: #6b7280; text-transform: uppercase;
    letter-spacing: .4px; margin-bottom: 1mm;
  }}
  .summary .val {{ font-size: 9.5pt; color: #1f2937; font-weight: 600; line-height: 1.3; }}

  /* Contenu */
  .content {{ padding: 6mm 7mm 6mm 7mm; }}

  .section-title {{
    margin: 5mm 0 2mm 0; font-size: 9.5pt; font-weight: 700;
    color: #0a246a; text-transform: uppercase; letter-spacing: .5px;
    padding-bottom: 1mm; border-bottom: 0.3mm solid #0a246a;
    break-after: avoid;
  }}
  .section-title:first-child {{ margin-top: 0; }}

  /* Compatibilite */
  .compat-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 4mm; }}
  body.portrait .compat-grid {{ grid-template-columns: 1fr; }}

  .profile-card {{ border: 0.3mm solid #cbd5e1; background: #fff; break-inside: avoid; }}
  .profile-head {{
    display: flex; justify-content: space-between; align-items: center;
    padding: 2.5mm 3mm;
    background: #f3f4f6;
    border-bottom: 0.3mm solid #cbd5e1;
  }}
  .profile-card.ok   .profile-head {{ background: #ecfdf5; border-bottom-color: #16a34a; }}
  .profile-card.warn .profile-head {{ background: #fffbeb; border-bottom-color: #d97706; }}
  .profile-card.fail .profile-head {{ background: #fef2f2; border-bottom-color: #dc2626; }}

  .profile-eyebrow {{ font-size: 7pt; color: #6b7280; text-transform: uppercase;
                      letter-spacing: .4px; }}
  .profile-title h3 {{ margin: 0; font-size: 10pt; color: #1f2937; font-weight: 700; }}

  .verdict-pill {{
    display: inline-block; padding: 0.8mm 3.5mm;
    font-size: 8.5pt; font-weight: 700; color: #fff;
    text-transform: uppercase; letter-spacing: .4px;
  }}
  .verdict-pill.ok   {{ background: #16a34a; }}
  .verdict-pill.warn {{ background: #d97706; }}
  .verdict-pill.fail {{ background: #dc2626; }}

  .verdict-counts {{ display: flex; gap: 1mm; font-size: 8pt; }}
  .count {{
    display: inline-block; padding: 0.3mm 1.8mm; font-weight: 600;
    border: 0.3mm solid #d1d5db; background: #fff; color: #6b7280;
  }}
  .count.ok   {{ color: #15803d; border-color: #16a34a; }}
  .count.warn {{ color: #b45309; border-color: #d97706; }}
  .count.fail {{ color: #b91c1c; border-color: #dc2626; }}
  .count small {{ font-weight: 500; }}

  .criteria {{ list-style: none; margin: 0; padding: 0; }}
  .crit-item {{
    display: grid; grid-template-columns: 5mm 1fr;
    gap: 2mm; padding: 1.6mm 3mm;
    border-bottom: 0.2mm solid #f3f4f6;
    break-inside: avoid;
  }}
  .crit-item:last-child {{ border-bottom: 0; }}
  .crit-icon {{ font-weight: 700; font-size: 9pt; text-align: center; }}
  .crit-item.ok   .crit-icon {{ color: #15803d; }}
  .crit-item.warn .crit-icon {{ color: #b45309; }}
  .crit-item.fail .crit-icon {{ color: #b91c1c; }}
  .crit-label {{ font-size: 9pt; font-weight: 600; color: #1f2937; }}
  .crit-detected {{ font-size: 8.5pt; color: #4b5563; }}
  .crit-req {{ font-size: 8pt; color: #9ca3af; }}

  /* Cards de details */
  .detail-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 3mm; }}
  body.landscape .detail-grid {{ grid-template-columns: 1fr 1fr 1fr; }}

  .card {{ border: 0.3mm solid #cbd5e1; background: #fff; break-inside: avoid; }}
  .card h4 {{
    margin: 0; padding: 1.5mm 3mm;
    font-size: 8.5pt; font-weight: 700; color: #1f2937;
    text-transform: uppercase; letter-spacing: .4px;
    background: #f3f4f6; border-bottom: 0.3mm solid #cbd5e1;
  }}
  .card.wide {{ grid-column: 1 / -1; }}

  .kv-list {{ list-style: none; margin: 0; padding: 0; }}
  .kv-list li {{
    display: grid; grid-template-columns: 45% 1fr; gap: 2mm;
    padding: 1mm 3mm; font-size: 9pt;
    border-bottom: 0.2mm solid #f3f4f6;
  }}
  .kv-list li:last-child {{ border-bottom: 0; }}
  .kv-list .k {{ color: #6b7280; }}
  .kv-list .v {{
    color: #1f2937; font-family: 'Consolas', 'Courier New', monospace;
    font-size: 8.5pt;
  }}

  table.compact {{ width: 100%; border-collapse: collapse; font-size: 8.5pt; break-inside: avoid; }}
  table.compact th {{
    background: #f8fafc; color: #374151; font-weight: 600;
    text-align: left; padding: 1.5mm 2mm; font-size: 7.5pt;
    text-transform: uppercase; letter-spacing: .3px;
    border-bottom: 0.3mm solid #cbd5e1;
  }}
  table.compact td {{
    padding: 1mm 2mm; border-bottom: 0.2mm solid #f3f4f6;
    font-family: 'Consolas', 'Courier New', monospace; font-size: 8pt;
  }}
  table.compact tr:last-child td {{ border-bottom: 0; }}
  table.compact tr.warn td {{ background: #fffbeb; }}
  table.compact tr.err  td {{ background: #fef2f2; }}
  table.compact .qty {{
    display: inline-block; margin-left: 4mm; padding: 0 2mm;
    font-family: 'Segoe UI', sans-serif; font-size: 7.5pt; font-weight: 700;
    color: #0a246a; background: #e8ecf5; border: 0.2mm solid #c7d2fe;
  }}

  .doc-footer {{
    padding: 2mm 7mm; font-size: 7.5pt; color: #6b7280;
    border-top: 0.3mm solid #cbd5e1; background: #fafbfc;
    display: flex; justify-content: space-between;
  }}

  /* ============ PRINT ============ */
  @media print {{
    body {{ background: #fff; }}
    .toolbar, .screen-only {{ display: none !important; }}
    /* En print, le @page gere les marges donc on retire celles simulees */
    .doc {{
      width: auto; min-height: 0; margin: 0; border: 0;
    }}
    /* Garde les page-break-inside sur tous les blocs lourds */
    .profile-card, .card, table.compact, .crit-item, .summary {{
      break-inside: avoid;
      page-break-inside: avoid; /* fallback ancien */
    }}
    .section-title {{
      break-after: avoid;
      page-break-after: avoid;
    }}
    /* Eviter veuves/orphelines (les 1-2 lignes isolees en bas/haut de page) */
    p, li {{ orphans: 3; widows: 3; }}
  }}
</style>
</head>
<body class="portrait">

<div class="toolbar">
  <span class="title">Rapport Info PC — {hostname}</span>
  <span class="seg" role="group">
    <button id="btn-port" class="active" onclick="setOrient(false)">Portrait</button>
    <button id="btn-land" onclick="setOrient(true)">Paysage</button>
  </span>
  <button onclick="doExportPDF()">Exporter PDF</button>
  <button onclick="window.print()">Imprimer</button>
</div>

<div class="doc">

  <div class="doc-head">
    <div class="eyebrow">Rapport d'inventaire &amp; compatibilité Vega</div>
    <h1>{hostname}</h1>
    <div class="sub">{_h.escape(manufacturer_model)}</div>
    <div class="doc-meta">
      <span><strong>Généré :</strong> {_h.escape(datetime.now().strftime('%d/%m/%Y %H:%M:%S'))}</span>
      <span><strong>Utilisateur :</strong> {_h.escape(str(s.get('CurrentUser') or '—'))}</span>
      <span><strong>Domaine :</strong> {_h.escape(str(domain_val or '—'))}</span>
    </div>
  </div>

  <div class="summary">
    <div>
      <div class="lbl">Système</div>
      <div class="val">{_h.escape(os_short)}</div>
    </div>
    <div>
      <div class="lbl">Processeur</div>
      <div class="val">{_h.escape(cpu_short)}</div>
    </div>
    <div>
      <div class="lbl">Mémoire</div>
      <div class="val">{_h.escape(ram_short)}</div>
    </div>
    <div>
      <div class="lbl">Disque C:</div>
      <div class="val">{_h.escape(disk_short)}</div>
    </div>
  </div>

  <div class="content">

    <!-- ============ COMPATIBILITÉ VEGA ============ -->
    <div class="section-title">Compatibilité Vega — Évaluation par profil d'usage</div>
    <div class="compat-grid">
      {compat_html}
    </div>

    <!-- ============ DÉTAILS MATÉRIEL ============ -->
    <div class="section-title">Détails matériel &amp; système</div>
    <div class="detail-grid">

      <div class="card">
        <h4>Système</h4>
        <ul class="kv-list">
          <li><span class="k">Nom du poste</span><span class="v">{_h.escape(str(s.get('Hostname') or '—'))}</span></li>
          <li><span class="k">Constructeur</span><span class="v">{_h.escape(str(s.get('Manufacturer') or '—'))}</span></li>
          <li><span class="k">Modèle</span><span class="v">{_h.escape(str(s.get('Model') or '—'))}</span></li>
          <li><span class="k">Architecture</span><span class="v">{_h.escape(str(s.get('SystemType') or '—'))}</span></li>
          <li><span class="k">Domaine / Workgroup</span><span class="v">{_h.escape(str(domain_val or '—'))}</span></li>
        </ul>
      </div>

      <div class="card">
        <h4>Système d'exploitation</h4>
        <ul class="kv-list">
          <li><span class="k">Édition</span><span class="v">{_h.escape(str(o.get('Caption') or '—'))}</span></li>
          <li><span class="k">Version</span><span class="v">{_h.escape(str(o.get('Version') or '—'))} (build {_h.escape(str(o.get('BuildNumber') or '—'))})</span></li>
          <li><span class="k">Architecture</span><span class="v">{_h.escape(str(o.get('OSArchitecture') or '—'))}</span></li>
          <li><span class="k">Langue</span><span class="v">{_h.escape(str(o.get('Language') or '—'))}</span></li>
          <li><span class="k">Installé le</span><span class="v">{_h.escape(str(o.get('InstallDate') or '—'))}</span></li>
          <li><span class="k">Uptime</span><span class="v">{_h.escape(str(o.get('Uptime') or '—'))}</span></li>
        </ul>
      </div>

      <div class="card">
        <h4>Processeur</h4>
        <ul class="kv-list">
          <li><span class="k">Nom</span><span class="v">{_h.escape(str(c.get('Name') or '—'))}</span></li>
          <li><span class="k">Cores / Threads</span><span class="v">{_h.escape(str(c.get('Cores') or '—'))} / {_h.escape(str(c.get('Threads') or '—'))}</span></li>
          <li><span class="k">Fréquence max</span><span class="v">{_h.escape(str(c.get('MaxClockMHz') or '—'))} MHz</span></li>
          <li><span class="k">Socket</span><span class="v">{_h.escape(str(c.get('Socket') or '—'))}</span></li>
        </ul>
      </div>

      <div class="card">
        <h4>Carte mère &amp; BIOS</h4>
        <ul class="kv-list">
          <li><span class="k">Carte mère</span><span class="v">{_h.escape((f"{mobo.get('Manufacturer') or ''} {mobo.get('Product') or ''}").strip() or '—')}</span></li>
          <li><span class="k">Version</span><span class="v">{_h.escape(str(mobo.get('Version') or '—'))}</span></li>
          <li><span class="k">BIOS</span><span class="v">{_h.escape(str(bios.get('Manufacturer') or '—'))}</span></li>
          <li><span class="k">Version BIOS</span><span class="v">{_h.escape(str(bios.get('Version') or '—'))}</span></li>
          <li><span class="k">Date BIOS</span><span class="v">{_h.escape(str(bios.get('ReleaseDate') or '—'))}</span></li>
        </ul>
      </div>

      <div class="card wide">
        <h4>Mémoire RAM</h4>
        <table class="compact">
          <thead><tr><th>Slot</th><th>Capacité</th><th>Vitesse</th><th>Type</th></tr></thead>
          <tbody>{ram_modules_rows or '<tr><td colspan="4">Aucun module détecté</td></tr>'}</tbody>
        </table>
      </div>

      <div class="card wide">
        <h4>Carte graphique</h4>
        <table class="compact">
          <thead><tr><th>Modèle</th><th>VRAM</th><th>Driver</th></tr></thead>
          <tbody>{gpu_rows or '<tr><td colspan="3">Aucun GPU détecté</td></tr>'}</tbody>
        </table>
      </div>

      <div class="card wide">
        <h4>Disques</h4>
        <table class="compact">
          <thead><tr><th>Lettre</th><th>Volume</th><th>Taille</th><th>Libre</th><th>Utilisation</th></tr></thead>
          <tbody>{disk_rows or '<tr><td colspan="5">Aucun disque détecté</td></tr>'}</tbody>
        </table>
      </div>

      <div class="card wide">
        <h4>Adaptateurs réseau</h4>
        <table class="compact">
          <thead><tr><th>Nom</th><th>MAC</th><th>IPv4</th><th>Mode</th></tr></thead>
          <tbody>{nic_rows or '<tr><td colspan="4">Aucun adaptateur détecté</td></tr>'}</tbody>
        </table>
      </div>

      <div class="card wide">
        <h4>Contexte Vega</h4>
        <ul class="kv-list">
          <li><span class="k">Service HFSQL</span><span class="v">{_h.escape((v.get('HFSQL') or {}).get('DisplayName') or 'Non détecté')} ({_h.escape((v.get('HFSQL') or {}).get('Status') or '—')})</span></li>
          <li><span class="k">Partages Vega</span><span class="v">{len(v.get("Shares") or [])}</span></li>
          <li><span class="k">Installations Vega détectées</span><span class="v">{len(v.get("Exes") or [])}</span></li>
        </ul>
      </div>

    </div>
  </div>

  <!-- Le footer A4 est gere par les regles @page @bottom-* (Paged Media)
       qui se repetent automatiquement sur chaque page imprimee.
       L'apercu ecran d'un rapide footer simule l'apparence print. -->
  <div class="screen-only doc-footer">
    <span>Vega Toolbox — {_h.escape(datetime.now().strftime('%d/%m/%Y %H:%M:%S'))}</span>
    <span>{hostname}</span>
  </div>
</div>

<script>
  function setOrient(land) {{
    // Rebuild the @page rule preserving page numbering + bottom labels.
    // Chrome/Edge respectent le @page courant a l'ouverture du dialog
    // d'impression - donc cette mise a jour suffit a switcher l'orientation
    // sur la sortie PDF.
    var style = document.getElementById('page-style');
    var size = land ? 'A4 landscape' : 'A4 portrait';
    style.textContent = (
      '@page {{ size: ' + size + '; margin: 15mm 15mm 18mm 15mm;' +
      '  @bottom-left {{ content: "Vega Toolbox · Rapport Info PC · {hostname}";' +
      '    font-family: \\'Segoe UI\\', Tahoma, sans-serif; font-size: 8pt; color: #6b7280; }}' +
      '  @bottom-right {{ content: "Page " counter(page) " / " counter(pages);' +
      '    font-family: \\'Segoe UI\\', Tahoma, sans-serif; font-size: 8pt; color: #6b7280; }}' +
      '}}'
    );
    document.body.classList.toggle('landscape', land);
    document.body.classList.toggle('portrait', !land);
    document.getElementById('btn-land').classList.toggle('active', land);
    document.getElementById('btn-port').classList.toggle('active', !land);
  }}
  function doExportPDF() {{
    // Ouvre le dialogue d'impression. L'utilisateur selectionne
    // "Microsoft Print to PDF" comme imprimante pour generer le PDF.
    window.print();
  }}
</script>
</body>
</html>
"""


# ---------- Tooltip helper (hover overlay style) ----------

# Indices GetSystemMetrics pour le virtual screen (multi-ecran).
_SM_XVIRTUALSCREEN  = 76
_SM_YVIRTUALSCREEN  = 77
_SM_CXVIRTUALSCREEN = 78
_SM_CYVIRTUALSCREEN = 79


def _virtual_screen_bounds():
    # Retourne (x_min, y_min, x_max, y_max) du virtual desktop Windows
    # (union de tous les ecrans). Permet de clamper les tooltips sur le bon
    # ecran meme si l'app a ete deplacee sur un moniteur secondaire.
    # Fallback : bornes de l'ecran primaire si l'API echoue.
    try:
        u32 = ctypes.windll.user32
        x = u32.GetSystemMetrics(_SM_XVIRTUALSCREEN)
        y = u32.GetSystemMetrics(_SM_YVIRTUALSCREEN)
        w = u32.GetSystemMetrics(_SM_CXVIRTUALSCREEN)
        h = u32.GetSystemMetrics(_SM_CYVIRTUALSCREEN)
        if w > 0 and h > 0:
            return (x, y, x + w, y + h)
    except Exception:
        pass
    return None


class _Tooltip:
    # Tooltip overlay leger : Toplevel sans decorations qui apparait au
    # survol du widget et disparait au depart de la souris. Style 'info-bubble'
    # jaune pale facon Windows. Le delai (200 ms) evite les apparitions
    # parasites quand on traverse rapidement.
    def __init__(self, widget, text, delay_ms=200, max_width=380):
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self.max_width = max_width
        self._tip = None
        self._after_id = None
        widget.bind("<Enter>", self._on_enter, add="+")
        widget.bind("<Leave>", self._on_leave, add="+")
        widget.bind("<ButtonPress>", self._on_leave, add="+")

    def _on_enter(self, _e=None):
        self._cancel()
        self._after_id = self.widget.after(self.delay_ms, self._show)

    def _on_leave(self, _e=None):
        self._cancel()
        self._hide()

    def _cancel(self):
        if self._after_id:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _show(self):
        if self._tip is not None:
            return
        try:
            x = self.widget.winfo_rootx() + self.widget.winfo_width() // 2
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        except Exception:
            return
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)  # pas de decoration
        try:
            tip.wm_attributes("-topmost", True)
        except Exception:
            pass
        # Cadre avec bordure 1 px facon tooltip Windows
        outer = tk.Frame(tip, bg="#7a7a7a", padx=1, pady=1)
        outer.pack()
        inner = tk.Frame(outer, bg="#fffbe6", padx=10, pady=8)
        inner.pack()
        tk.Label(
            inner, text=self.text, bg="#fffbe6", fg="#1f2937",
            font=("Segoe UI", 9), justify="left", anchor="w",
            wraplength=self.max_width,
        ).pack(anchor="w")
        # Ajuste position pour rester dans le VIRTUAL screen (multi-ecran).
        # Sans cette correction, sur un setup 2 ecrans avec l'app sur le
        # secondaire (x=2000+), le tooltip etait force sur l'ecran primaire
        # (clamp a winfo_screenwidth qui ne donne que le primaire).
        tip.update_idletasks()
        tw = tip.winfo_reqwidth()
        th = tip.winfo_reqheight()
        bounds = _virtual_screen_bounds()
        if bounds:
            vx, vy, vmax_x, vmax_y = bounds
            x = min(max(x - tw // 2, vx + 4), vmax_x - tw - 4)
            y = min(max(y, vy + 4), vmax_y - th - 4)
        else:
            # Fallback ecran primaire si Win32 indispo
            sw = tip.winfo_screenwidth()
            sh = tip.winfo_screenheight()
            x = min(max(x - tw // 2, 4), sw - tw - 4)
            y = min(y, sh - th - 4)
        tip.geometry(f"+{x}+{y}")
        self._tip = tip

    def _hide(self):
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None


def _build_tooltip_text(crit):
    # Texte multi-ligne affiche dans le tooltip pour un critere non conforme.
    parts = []
    msg = (crit.get("message") or "").strip()
    if msg:
        parts.append(msg)
    req_short = (crit.get("requirement_short") or "").strip()
    req_full = (crit.get("requirement_full") or "").strip()
    if req_short or req_full:
        parts.append("")  # ligne vide separator
        parts.append("Prérequis Vega :")
        if req_short:
            parts.append("  • " + req_short)
        if req_full:
            parts.append("")
            parts.append(req_full)
    return "\n".join(parts) if parts else "Aucun détail disponible."
