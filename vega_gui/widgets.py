import tkinter as tk
from tkinter import ttk

from .theme import ACCENT_BLUE, ERROR_COLOR, MUTED_TEXT, PANEL_BG, WHITE_BG, WINDOW_BG

SIDEBAR_BG = "#ece9e1"
SIDEBAR_HOVER_BG = "#d8d4c8"
SIDEBAR_SELECTED_BG = ACCENT_BLUE
SIDEBAR_SELECTED_FG = "#ffffff"
SIDEBAR_FG = "#000000"


def tool_button(parent, text, command, image=None, state="normal", **kwargs):
    # Style de bouton partagé dans toute la toolbox.
    options = {
        "text": text,
        "command": command,
        "state": state,
        "font": ("Tahoma", 9),
        "padx": 10,
        "pady": 6,
        "anchor": "w",
        "highlightthickness": 0,
    }
    if image is not None:
        options["image"] = image
        options["compound"] = "left"
        # tk.Button interprete width en pixels quand une image est presente, ce qui tronque
        # le texte. On ignore donc width dans ce cas : le bouton se dimensionne sur son contenu.
        kwargs.pop("width", None)
    options.update(kwargs)
    button = tk.Button(parent, **options)
    if image is not None:
        button.image = image
    return button


def path_entry(parent, textvariable, width=None):
    # Entry avec indicateur live : vert si le chemin existe, rouge sinon.
    # Retourne un container packe tel quel.
    from pathlib import Path

    frame = tk.Frame(parent, bg=WINDOW_BG)
    frame.columnconfigure(0, weight=1)

    entry = tk.Entry(frame, textvariable=textvariable, bd=2, relief="sunken", font=("Tahoma", 9))
    if width:
        entry.configure(width=width)
    entry.grid(row=0, column=0, sticky="ew")

    indicator = tk.Label(frame, text="", bg=WINDOW_BG, font=("Tahoma", 11, "bold"), width=2)
    indicator.grid(row=0, column=1, padx=(4, 0))

    def refresh(*_args):
        value = textvariable.get().strip()
        if not value:
            indicator.configure(text="", fg=MUTED_TEXT)
            return
        try:
            ok = Path(value).is_dir()
        except Exception:
            ok = False
        if ok:
            indicator.configure(text="✓", fg="#0b6500")
        else:
            indicator.configure(text="✗", fg=ERROR_COLOR)

    textvariable.trace_add("write", refresh)
    refresh()
    frame.entry = entry
    frame.indicator = indicator
    return frame


def value_cell(parent, text="", textvariable=None, width=18, anchor="center", font=None):
    label = tk.Label(
        parent,
        text=text,
        textvariable=textvariable,
        width=width,
        anchor=anchor,
        bg=WHITE_BG,
        relief="sunken",
        bd=1,
        padx=6,
        pady=2,
        font=font or ("Tahoma", 9),
    )
    return label


def info_panel(parent, text, wraplength):
    panel = tk.Frame(parent, bg=PANEL_BG, bd=1, relief="sunken", padx=8, pady=7)
    tk.Label(
        panel,
        text=text,
        bg=PANEL_BG,
        justify="left",
        wraplength=wraplength,
        font=("Tahoma", 8),
    ).pack(anchor="w")
    return panel


class ScrollableHost(tk.Frame):
    # Conteneur scrollable + lazy : le content_cls n'est construit qu'au premier acces
    # (show_module ou attribut delegue). Reduit le temps de demarrage en differant les
    # onglets non vus au boot.
    def __init__(self, parent, content_cls, *content_args, lazy=True):
        super().__init__(parent, bg=WINDOW_BG)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.canvas = tk.Canvas(self, bg=WINDOW_BG, bd=0, highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")

        self.vscroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self._on_scroll_set)
        self._scroll_visible = False

        self.inner = tk.Frame(self.canvas, bg=WINDOW_BG)
        self.inner.columnconfigure(0, weight=1)
        self.inner.rowconfigure(0, weight=1)
        self.window_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")

        self._content_cls = content_cls
        self._content_args = content_args
        self._content_built = False
        self.content = None

        self.inner.bind("<Configure>", self.on_inner_configure)
        self.canvas.bind("<Configure>", self.on_canvas_configure)
        for widget in (self, self.canvas, self.inner):
            widget.bind("<Enter>", self.bind_mousewheel, add="+")
            widget.bind("<Leave>", self.unbind_mousewheel, add="+")

        if not lazy:
            self.ensure_content()

    def ensure_content(self):
        # Construit le contenu si pas deja fait. Idempotent.
        if self._content_built:
            return self.content
        self.content = self._content_cls(self.inner, *self._content_args)
        self.content.grid(row=0, column=0, sticky="nsew")
        self.content.bind("<Enter>", self.bind_mousewheel, add="+")
        self.content.bind("<Leave>", self.unbind_mousewheel, add="+")
        self._content_built = True
        return self.content

    def __getattr__(self, name):
        # Auto-build au premier acces d'attribut delegue. Filtre les noms internes pour
        # eviter la recursion pendant __init__ et les acces aux Tk widgets directs.
        if name.startswith("_"):
            raise AttributeError(name)
        if name in ("content", "canvas", "vscroll", "inner", "window_id"):
            raise AttributeError(name)
        if not self.__dict__.get("_content_built"):
            cls = self.__dict__.get("_content_cls")
            if cls is None:
                raise AttributeError(name)
            self.ensure_content()
        content = self.__dict__.get("content")
        if content is None:
            raise AttributeError(name)
        return getattr(content, name)

    def _on_scroll_set(self, first, last):
        # Masque la scrollbar tant que tout le contenu tient dans le canvas.
        try:
            first_f = float(first)
            last_f = float(last)
        except (TypeError, ValueError):
            first_f, last_f = 0.0, 1.0
        needs_scroll = (last_f - first_f) < 1.0
        if needs_scroll and not self._scroll_visible:
            self.vscroll.grid(row=0, column=1, sticky="ns")
            self._scroll_visible = True
        elif not needs_scroll and self._scroll_visible:
            self.vscroll.grid_remove()
            self._scroll_visible = False
        self.vscroll.set(first, last)

    def on_inner_configure(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def on_canvas_configure(self, event):
        self.canvas.itemconfigure(self.window_id, width=event.width)

    def bind_mousewheel(self, _event=None):
        self.canvas.bind_all("<MouseWheel>", self.on_mousewheel)

    def unbind_mousewheel(self, _event=None):
        self.canvas.unbind_all("<MouseWheel>")

    def on_mousewheel(self, event):
        if self.canvas.winfo_height() <= 1 or not self._scroll_visible:
            return
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")


class Sidebar(tk.Frame):
    # Barre latérale style Panneau de configuration XP : liste verticale d'entrées.
    def __init__(self, parent, width=184, on_select=None, compact=False):
        self.compact = compact
        if compact:
            width = 56
        super().__init__(parent, bg=SIDEBAR_BG, width=width, bd=1, relief="sunken")
        self.pack_propagate(False)
        self.on_select = on_select
        self.entries = {}
        self.selected_key = None

        self._top = tk.Frame(self, bg=SIDEBAR_BG)
        self._top.pack(side="top", fill="x")
        self._bottom = tk.Frame(self, bg=SIDEBAR_BG)
        self._bottom.pack(side="bottom", fill="x")

    def add_entry(self, key, text, icon=None, bottom=False):
        host = self._bottom if bottom else self._top
        row_padx = 6 if self.compact else 10
        row = tk.Frame(host, bg=SIDEBAR_BG, padx=row_padx, pady=5, cursor="hand2")
        row.pack(fill="x", padx=0, pady=0)

        icon_label = None
        if icon is not None:
            icon_label = tk.Label(row, image=icon, bg=SIDEBAR_BG)
            icon_label.image = icon
            side = "top" if self.compact else "left"
            padx = 0 if self.compact else (0, 8)
            icon_label.pack(side=side, padx=padx)

        text_label = tk.Label(
            row,
            text=text,
            bg=SIDEBAR_BG,
            fg=SIDEBAR_FG,
            font=("Tahoma", 9),
            anchor="w",
        )
        if not self.compact:
            text_label.pack(side="left", fill="x", expand=True)

        entry = {
            "row": row,
            "icon": icon_label,
            "text": text_label,
            "key": key,
        }
        self.entries[key] = entry

        widgets = [row, text_label]
        if icon_label is not None:
            widgets.append(icon_label)

        for widget in widgets:
            widget.bind("<Button-1>", lambda _e, k=key: self._handle_click(k))
            widget.bind("<Enter>", lambda _e, k=key: self._on_hover(k, True))
            widget.bind("<Leave>", lambda _e, k=key: self._on_hover(k, False))

        return entry

    def add_separator(self, bottom=False):
        host = self._bottom if bottom else self._top
        sep = tk.Frame(host, bg="#b0ada5", height=1)
        sep.pack(fill="x", padx=6, pady=5)
        return sep

    def add_caption(self, text, bottom=False):
        if self.compact:
            return self.add_separator(bottom=bottom)
        host = self._bottom if bottom else self._top
        label = tk.Label(
            host,
            text=text,
            bg=SIDEBAR_BG,
            fg=MUTED_TEXT,
            font=("Tahoma", 8, "bold"),
            anchor="w",
            padx=10,
            pady=4,
        )
        label.pack(fill="x")
        return label

    def add_widget(self, widget_cls, bottom=False, **kwargs):
        host = self._bottom if bottom else self._top
        widget = widget_cls(host, **kwargs)
        widget.pack(fill="x", padx=8, pady=4)
        return widget

    def select(self, key):
        if key not in self.entries:
            return
        self.selected_key = key
        for k, entry in self.entries.items():
            selected = k == key
            bg = SIDEBAR_SELECTED_BG if selected else SIDEBAR_BG
            fg = SIDEBAR_SELECTED_FG if selected else SIDEBAR_FG
            entry["row"].configure(bg=bg)
            entry["text"].configure(bg=bg, fg=fg)
            if entry["icon"] is not None:
                entry["icon"].configure(bg=bg)

    def _handle_click(self, key):
        self.select(key)
        if self.on_select:
            self.on_select(key)

    def _on_hover(self, key, entering):
        if self.selected_key == key:
            return
        entry = self.entries.get(key)
        if not entry:
            return
        bg = SIDEBAR_HOVER_BG if entering else SIDEBAR_BG
        entry["row"].configure(bg=bg)
        entry["text"].configure(bg=bg)
        if entry["icon"] is not None:
            entry["icon"].configure(bg=bg)


class LogDrawer(tk.Frame):
    # Tiroir journal bas repliable : ligne résumée repliée, panneau complet déplié.
    def __init__(self, parent, app, expanded_height=200):
        super().__init__(parent, bg=WINDOW_BG)
        self.app = app
        self.expanded = False
        self.expanded_height = expanded_height
        self.last_line_var = tk.StringVar(value="Prêt.")
        self.last_level = "info"

        self.header = tk.Frame(self, bg=PANEL_BG, bd=1, relief="raised", cursor="hand2")
        self.header.pack(side="top", fill="x")

        self.arrow_label = tk.Label(
            self.header,
            text="▲",
            bg=PANEL_BG,
            font=("Tahoma", 8, "bold"),
            padx=8,
            pady=2,
        )
        self.arrow_label.pack(side="left")

        tk.Label(
            self.header,
            text="Journal :",
            bg=PANEL_BG,
            font=("Tahoma", 8, "bold"),
            pady=2,
        ).pack(side="left")

        self.last_line_label = tk.Label(
            self.header,
            textvariable=self.last_line_var,
            bg=PANEL_BG,
            font=("Tahoma", 8),
            anchor="w",
            padx=6,
            pady=2,
        )
        self.last_line_label.pack(side="left", fill="x", expand=True)

        self.clear_button = tk.Label(
            self.header,
            text="Effacer",
            bg=PANEL_BG,
            fg=ACCENT_BLUE,
            font=("Tahoma", 8, "underline"),
            padx=8,
            pady=2,
            cursor="hand2",
        )
        self.clear_button.pack(side="right")
        self.clear_button.bind("<Button-1>", lambda _e: self.clear())

        for widget in (self.header, self.arrow_label, self.last_line_label):
            widget.bind("<Button-1>", lambda _e: self.toggle())

        self.body = tk.Frame(self, bg=WINDOW_BG, height=self.expanded_height)
        self.body.pack_propagate(False)
        self.log_panel = _DrawerLogBody(self.body, app)
        self.log_panel.pack(fill="both", expand=True)

    def toggle(self):
        if self.expanded:
            self.collapse()
        else:
            self.expand()

    def expand(self):
        if self.expanded:
            return
        self.body.pack(side="bottom", fill="both", expand=True)
        self.expanded = True
        self.arrow_label.configure(text="▼")

    def collapse(self):
        if not self.expanded:
            return
        self.body.pack_forget()
        self.expanded = False
        self.arrow_label.configure(text="▲")

    def append(self, line, level="info"):
        self.last_line_var.set(line[:140])
        self.last_level = level
        color = ERROR_COLOR if level == "error" else SIDEBAR_FG
        self.last_line_label.configure(fg=color)
        self.log_panel.append(line, level)

    def clear(self):
        self.last_line_var.set("")
        self.last_line_label.configure(fg=SIDEBAR_FG)
        self.log_panel.clear()


class _DrawerLogBody(tk.Frame):
    # Corps texte du drawer journal (sans LabelFrame, contrairement à LogPanel).
    def __init__(self, parent, app):
        super().__init__(parent, bg=WINDOW_BG, padx=6, pady=6)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        text_frame = tk.Frame(self, bd=1, relief="sunken", bg=WHITE_BG)
        text_frame.grid(row=0, column=0, sticky="nsew")
        text_frame.columnconfigure(0, weight=1)
        text_frame.rowconfigure(0, weight=1)

        self.text = tk.Text(
            text_frame,
            wrap="word",
            height=6,
            bg=WHITE_BG,
            state="disabled",
            font=("Consolas", 9),
            relief="flat",
        )
        self.text.grid(row=0, column=0, sticky="nsew")
        self.text.tag_configure("info", foreground="black")
        self.text.tag_configure("error", foreground=ERROR_COLOR)

        scrollbar = ttk.Scrollbar(text_frame, orient="vertical", command=self.text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.text.configure(yscrollcommand=scrollbar.set)

    def append(self, line, level="info"):
        self.text.configure(state="normal")
        self.text.insert("end", line + "\n", level)
        self.text.see("end")
        self.text.configure(state="disabled")

    def clear(self):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")


