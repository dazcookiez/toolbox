import ctypes
import ctypes.wintypes
import getpass
import random
import tkinter as tk
from tkinter import ttk

from vega_security import load_snake_highscore, save_snake_highscore

from ..resources import load_scaled_logo, set_window_icon
from ..theme import (
    ACCENT_BLUE,
    APP_TITLE,
    APP_VERSION_LABEL,
    ERROR_COLOR,
    MUTED_TEXT,
    OK_COLOR,
    PANEL_BG,
    SUPPORT_EMAIL,
    SUPPORT_MESSAGE,
    WINDOW_BG,
)
from ..utils import open_outlook_mail, open_teams_application
from ..widgets import tool_button, value_cell


def _windows_display_name():
    # Recupere le nom complet AD (ex: "Tillier Bastien") via secur32.GetUserNameExW
    # avec NameDisplay=3. Fallback sur getpass.getuser() (login court style TILBAS).
    try:
        secur32 = ctypes.WinDLL("secur32.dll")
        get_user = secur32.GetUserNameExW
        get_user.argtypes = [ctypes.c_int, ctypes.c_wchar_p, ctypes.POINTER(ctypes.wintypes.ULONG)]
        get_user.restype = ctypes.wintypes.BOOL
        size = ctypes.wintypes.ULONG(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if get_user(3, buf, ctypes.byref(size)) and buf.value.strip():
            return buf.value.strip()
    except Exception:
        pass
    try:
        return getpass.getuser()
    except Exception:
        return "Anon"


class _SnakeGame(tk.Toplevel):
    # Easter egg : lance depuis la vue Infos en tapant G R Y V (pseudo GitLab
    # de l'auteur). Souvenir laisse par Bastien Tillier en quittant Zucchetti.
    CELL = 24
    W = 22
    H = 18
    # Vitesse : demarre doux, accelere a chaque pomme, plancher arcade.
    TICK_START_MS = 130
    TICK_MIN_MS = 65
    TICK_STEP_MS = 3

    # Palette assets.
    BOARD_BG = "#eaf2fa"
    BOARD_BG_ALT = "#dfebf7"
    GRID_LINE = "#cadcef"
    BORDER_COLOR = "#0050c2"
    SNAKE_HEAD = "#0050c2"
    SNAKE_BODY = "#1a6ad8"
    SNAKE_BODY_ALT = "#0d5fd0"
    SNAKE_EYE = "#ffffff"
    SNAKE_PUPIL = "#0a1e3a"
    APPLE_RED = "#c8271f"
    APPLE_SHINE = "#f28c8c"
    APPLE_LEAF = "#3aa63a"
    APPLE_STEM = "#5a3a1c"
    HEADER_BG = "#0050c2"
    HEADER_ACCENT = "#0d65d2"
    RECORD_GOLD = "#ffd400"

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Snake")
        try:
            set_window_icon(self)
        except Exception:
            pass
        self.resizable(False, False)
        self.configure(bg=self.BORDER_COLOR)

        board_w = self.W * self.CELL
        board_h = self.H * self.CELL

        self.highscore, self.highscore_holder = load_snake_highscore()

        # --- Bandeau superieur : score + record + hints ---
        header = tk.Frame(self, bg=self.HEADER_BG, height=44)
        header.pack(side="top", fill="x")
        header.pack_propagate(False)
        self.score_var = tk.StringVar(value="0")
        tk.Label(
            header, text="SCORE",
            bg=self.HEADER_BG, fg="#a5c9ed",
            font=("Segoe UI", 8, "bold"),
        ).pack(side="left", padx=(14, 4))
        tk.Label(
            header, textvariable=self.score_var,
            bg=self.HEADER_BG, fg="#ffffff",
            font=("Consolas", 18, "bold"),
        ).pack(side="left")
        self.record_var = tk.StringVar(value=str(self.highscore) if self.highscore else "—")
        tk.Label(
            header, text="RECORD",
            bg=self.HEADER_BG, fg="#a5c9ed",
            font=("Segoe UI", 8, "bold"),
        ).pack(side="left", padx=(18, 4))
        tk.Label(
            header, textvariable=self.record_var,
            bg=self.HEADER_BG, fg=self.RECORD_GOLD,
            font=("Consolas", 14, "bold"),
        ).pack(side="left")
        tk.Label(
            header,
            text="ZQSD / Fleches  ·  Espace = pause  ·  Echap = quitter",
            bg=self.HEADER_BG, fg="#b3d7ff",
            font=("Segoe UI", 8),
        ).pack(side="right", padx=14)

        # --- Plateau ---
        board_frame = tk.Frame(self, bg=self.BORDER_COLOR)
        board_frame.pack(side="top", padx=6, pady=(0, 6))
        self.canvas = tk.Canvas(
            board_frame, width=board_w, height=board_h,
            bg=self.BOARD_BG, highlightthickness=0, bd=0,
        )
        self.canvas.pack()

        self._tick_after = None
        # Fond statique (damier + grille) dessine une seule fois : chaque frame
        # ne redessine que les items tagges 'dyn' (serpent, pomme, overlays).
        self._draw_background()
        self._reset_state()
        self._draw()

        self.bind("<Key>", self._on_key)
        self.protocol("WM_DELETE_WINDOW", self._close)

        self.transient(parent.winfo_toplevel())
        self.update_idletasks()
        # Centre sur la fenetre parent.
        try:
            top = parent.winfo_toplevel()
            px, py = top.winfo_rootx(), top.winfo_rooty()
            pw, ph = top.winfo_width(), top.winfo_height()
            dw, dh = self.winfo_width(), self.winfo_height()
            self.geometry(f"+{px + (pw - dw) // 2}+{py + (ph - dh) // 2}")
        except Exception:
            pass
        self.grab_set()
        self.focus_set()

        self._tick_after = self.after(self.tick_ms, self._tick)

    def _reset_state(self):
        self.snake = [(self.W // 2 - i, self.H // 2) for i in range(3)]
        self.direction = (1, 0)
        # File d'inputs (2 max) : permet les virages serres sans perte de
        # touche. Chaque entree est validee contre la precedente en file,
        # pas contre la direction courante.
        self.input_queue = []
        self.food = self._new_food_pos()
        self.game_over = False
        self.victory = False
        self.paused = False
        self.score = 0
        self.tick_ms = self.TICK_START_MS
        self._eat_flash = None
        self.score_var.set("0")

    def _new_food_pos(self):
        # Le caller garantit qu'il reste au moins une case libre (victoire
        # detectee avant l'appel quand le serpent remplit le plateau).
        while True:
            pos = (random.randint(0, self.W - 1), random.randint(0, self.H - 1))
            if pos not in self.snake:
                return pos

    def _on_key(self, event):
        key = event.keysym.lower()
        dirs = {
            "up": (0, -1), "z": (0, -1), "w": (0, -1),
            "down": (0, 1), "s": (0, 1),
            "left": (-1, 0), "q": (-1, 0), "a": (-1, 0),
            "right": (1, 0), "d": (1, 0),
        }
        if key in dirs and not self.game_over and not self.paused:
            nd = dirs[key]
            ref = self.input_queue[-1] if self.input_queue else self.direction
            # Rejette demi-tour et doublon par rapport au dernier input en file.
            if nd != ref and (nd[0] + ref[0], nd[1] + ref[1]) != (0, 0) and len(self.input_queue) < 2:
                self.input_queue.append(nd)
        elif key in ("space", "p") and not self.game_over:
            self._toggle_pause()
        elif key == "escape":
            self._close()
        elif key == "return" and self.game_over:
            self.canvas.delete("pause")
            self._reset_state()
            self._draw()
            self._tick_after = self.after(self.tick_ms, self._tick)

    def _toggle_pause(self):
        self.paused = not self.paused
        w = self.W * self.CELL
        h = self.H * self.CELL
        if self.paused:
            if self._tick_after is not None:
                try:
                    self.after_cancel(self._tick_after)
                except Exception:
                    pass
                self._tick_after = None
            self.canvas.create_rectangle(0, 0, w, h,
                                         fill="#000000", stipple="gray25",
                                         outline="", tags="pause")
            self.canvas.create_text(w / 2, h / 2 - 10, text="PAUSE",
                                    fill="#ffffff", font=("Segoe UI", 22, "bold"),
                                    tags="pause")
            self.canvas.create_text(w / 2, h / 2 + 18, text="Espace pour reprendre",
                                    fill="#b3d7ff", font=("Segoe UI", 9),
                                    tags="pause")
        else:
            self.canvas.delete("pause")
            self._tick_after = self.after(self.tick_ms, self._tick)

    def _tick(self):
        self._tick_after = None
        if self.game_over or self.paused:
            return
        if self.input_queue:
            self.direction = self.input_queue.pop(0)
        head = self.snake[0]
        new_head = (head[0] + self.direction[0], head[1] + self.direction[1])
        eating = new_head == self.food
        # Collision : la case de queue se libere ce tick (sauf si on mange,
        # ou la queue reste en place) -> suivre sa queue de pres est legal.
        body = self.snake if eating else self.snake[:-1]
        if (new_head[0] < 0 or new_head[0] >= self.W
                or new_head[1] < 0 or new_head[1] >= self.H
                or new_head in body):
            self._end_game(victory=False)
            return
        self.snake.insert(0, new_head)
        if eating:
            self.score += 10
            self.score_var.set(str(self.score))
            self._eat_flash = new_head
            self.tick_ms = max(self.TICK_MIN_MS, self.tick_ms - self.TICK_STEP_MS)
            if len(self.snake) == self.W * self.H:
                self._draw()
                self._end_game(victory=True)
                return
            self.food = self._new_food_pos()
        else:
            self.snake.pop()
        self._draw()
        self._tick_after = self.after(self.tick_ms, self._tick)

    def _draw_background(self):
        # Damier subtil + grille fine, dessines une seule fois (tag 'bg').
        c = self.CELL
        board_w = self.W * c
        board_h = self.H * c
        for gy in range(self.H):
            for gx in range(self.W):
                if (gx + gy) % 2 == 0:
                    self.canvas.create_rectangle(
                        gx * c, gy * c, (gx + 1) * c, (gy + 1) * c,
                        fill=self.BOARD_BG_ALT, outline="", tags="bg",
                    )
        for gx in range(1, self.W):
            self.canvas.create_line(gx * c, 0, gx * c, board_h,
                                    fill=self.GRID_LINE, width=1, tags="bg")
        for gy in range(1, self.H):
            self.canvas.create_line(0, gy * c, board_w, gy * c,
                                    fill=self.GRID_LINE, width=1, tags="bg")

    def _draw(self):
        self.canvas.delete("dyn")
        # Flash 1 tick a l'endroit de la pomme mangee (anneau clair sous la tete).
        if self._eat_flash is not None:
            fx, fy = self._eat_flash
            c = self.CELL
            self.canvas.create_oval(
                fx * c - 3, fy * c - 3, (fx + 1) * c + 3, (fy + 1) * c + 3,
                outline="#7db8f7", width=3, tags="dyn",
            )
            self._eat_flash = None

        # Serpent : corps degrade, tete avec yeux directionnels.
        for i, (x, y) in enumerate(self.snake):
            if i == 0:
                self._draw_head(x, y)
            else:
                color = self.SNAKE_BODY if i % 2 == 0 else self.SNAKE_BODY_ALT
                self._draw_body_segment(x, y, color)

        # Pomme.
        self._draw_apple(*self.food)

    def _draw_body_segment(self, gx, gy, color):
        c = self.CELL
        pad = 2
        x0, y0 = gx * c + pad, gy * c + pad
        x1, y1 = (gx + 1) * c - pad, (gy + 1) * c - pad
        # Rectangle avec coins ronds simule via oval aux 4 coins.
        r = 5
        self.canvas.create_rectangle(x0 + r, y0, x1 - r, y1, fill=color, outline="", tags="dyn")
        self.canvas.create_rectangle(x0, y0 + r, x1, y1 - r, fill=color, outline="", tags="dyn")
        self.canvas.create_oval(x0, y0, x0 + 2 * r, y0 + 2 * r, fill=color, outline="", tags="dyn")
        self.canvas.create_oval(x1 - 2 * r, y0, x1, y0 + 2 * r, fill=color, outline="", tags="dyn")
        self.canvas.create_oval(x0, y1 - 2 * r, x0 + 2 * r, y1, fill=color, outline="", tags="dyn")
        self.canvas.create_oval(x1 - 2 * r, y1 - 2 * r, x1, y1, fill=color, outline="", tags="dyn")

    def _draw_head(self, gx, gy):
        c = self.CELL
        pad = 1
        x0, y0 = gx * c + pad, gy * c + pad
        x1, y1 = (gx + 1) * c - pad, (gy + 1) * c - pad
        # Tete = ovale plein.
        self.canvas.create_oval(x0, y0, x1, y1, fill=self.SNAKE_HEAD, outline="", tags="dyn")
        # Yeux positionnes en fonction de la direction (2 blancs + pupilles).
        dx, dy = self.direction
        cx = (x0 + x1) / 2
        cy = (y0 + y1) / 2
        r = c / 2 - pad
        # Deux yeux perpendiculaires a la direction, decales vers l'avant.
        if dx != 0:  # horizontal
            eye_off_x = dx * r * 0.35
            eye_off_y1, eye_off_y2 = -r * 0.35, r * 0.35
            ex1, ey1 = cx + eye_off_x, cy + eye_off_y1
            ex2, ey2 = cx + eye_off_x, cy + eye_off_y2
        else:  # vertical
            eye_off_y = dy * r * 0.35
            eye_off_x1, eye_off_x2 = -r * 0.35, r * 0.35
            ex1, ey1 = cx + eye_off_x1, cy + eye_off_y
            ex2, ey2 = cx + eye_off_x2, cy + eye_off_y
        er = 3
        pr = 1.5
        for (ex, ey) in ((ex1, ey1), (ex2, ey2)):
            self.canvas.create_oval(ex - er, ey - er, ex + er, ey + er,
                                    fill=self.SNAKE_EYE, outline="", tags="dyn")
            # Pupille legerement decalee vers la direction pour le regard.
            px = ex + dx * 1.2
            py = ey + dy * 1.2
            self.canvas.create_oval(px - pr, py - pr, px + pr, py + pr,
                                    fill=self.SNAKE_PUPIL, outline="", tags="dyn")

    def _draw_apple(self, gx, gy):
        c = self.CELL
        cx = gx * c + c / 2
        cy = gy * c + c / 2 + 1
        r = c * 0.36
        # Corps pomme.
        self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r,
                                fill=self.APPLE_RED, outline="", tags="dyn")
        # Petit reflet.
        self.canvas.create_oval(cx - r * 0.5, cy - r * 0.6,
                                cx - r * 0.15, cy - r * 0.25,
                                fill=self.APPLE_SHINE, outline="", tags="dyn")
        # Tige.
        self.canvas.create_line(cx, cy - r + 1, cx + 2, cy - r - 4,
                                fill=self.APPLE_STEM, width=2, tags="dyn")
        # Feuille.
        self.canvas.create_polygon(
            cx + 2, cy - r - 3,
            cx + 8, cy - r - 6,
            cx + 5, cy - r,
            fill=self.APPLE_LEAF, outline="", tags="dyn",
        )

    def _end_game(self, victory=False):
        self.game_over = True
        self.victory = victory
        new_record = self.score > self.highscore and self.score > 0
        if new_record:
            self.highscore = self.score
            self.highscore_holder = _windows_display_name()
            save_snake_highscore(self.highscore, self.highscore_holder)
            self.record_var.set(str(self.highscore))
        self._draw_game_over(new_record)

    def _draw_game_over(self, new_record=False):
        w = self.W * self.CELL
        h = self.H * self.CELL
        # Voile sombre semi-transparent (stipple).
        self.canvas.create_rectangle(0, 0, w, h,
                                     fill="#000000", stipple="gray50",
                                     outline="", tags="dyn")
        # Panneau central.
        panel_pad = 40
        self.canvas.create_rectangle(
            panel_pad, h / 2 - 100, w - panel_pad, h / 2 + 100,
            fill=self.HEADER_BG, outline=self.HEADER_ACCENT, width=2, tags="dyn",
        )
        title = "VICTOIRE !" if self.victory else "GAME OVER"
        self.canvas.create_text(w / 2, h / 2 - 68, text=title,
                                fill="#ffffff", font=("Segoe UI", 24, "bold"),
                                tags="dyn")
        self.canvas.create_text(w / 2, h / 2 - 32, text="Score final",
                                fill="#b3d7ff", font=("Segoe UI", 9), tags="dyn")
        self.canvas.create_text(w / 2, h / 2 - 6, text=str(self.score),
                                fill="#ffffff", font=("Consolas", 24, "bold"),
                                tags="dyn")
        if new_record:
            self.canvas.create_text(w / 2, h / 2 + 28, text="★ NOUVEAU RECORD ★",
                                    fill=self.RECORD_GOLD,
                                    font=("Segoe UI", 11, "bold"), tags="dyn")
        elif self.highscore > 0:
            holder = f" — {self.highscore_holder}" if self.highscore_holder else ""
            self.canvas.create_text(w / 2, h / 2 + 28,
                                    text=f"Record : {self.highscore}{holder}",
                                    fill="#b3d7ff", font=("Segoe UI", 9), tags="dyn")
        self.canvas.create_text(w / 2, h / 2 + 60,
                                text="Entrer = rejouer   ·   Echap = quitter",
                                fill="#a5c9ed", font=("Segoe UI", 9), tags="dyn")
        # Signature d'adieu : nom complet AD (Tillier Bastien) + reference
        # Undertale ("Stay Determined"). Chaque decouvreur voit son propre nom.
        user = _windows_display_name()
        self.canvas.create_text(w / 2, h - 18,
                                text=f"— {user}, Stay Determined —",
                                fill=self.BORDER_COLOR,
                                font=("Segoe UI", 9, "italic"), tags="dyn")

    def _close(self):
        if self._tick_after is not None:
            try:
                self.after_cancel(self._tick_after)
            except Exception:
                pass
            self._tick_after = None
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()


class SupportTab(ttk.Frame):
    # Sequence secrete a taper sur la vue Infos pour lancer Snake (pseudo
    # GitLab de l'auteur). Souvenir laisse en quittant l'entreprise.
    _EASTER_EGG_KEYS = "gryv"

    def __init__(self, parent, app):
        super().__init__(parent, padding=12)
        self.app = app
        self.teams_icon = load_scaled_logo("media/teams.png", 40)
        self.mail_icon = load_scaled_logo("media/outlook.png", 40)
        self._egg_buffer = ""
        self.build_ui()
        # Bind clavier au toplevel de l'app : le buffer n'est actif que
        # quand la vue Infos est le module courant, sinon reset.
        self.winfo_toplevel().bind("<KeyPress>", self._on_key_egg, add="+")

    def build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        about_frame = tk.LabelFrame(self, text="À propos de l'outil", padx=14, pady=12, bg=WINDOW_BG)
        about_frame.grid(row=0, column=0, sticky="ew")
        about_frame.columnconfigure(1, weight=1)

        tk.Label(about_frame, text="Éditeur", bg=WINDOW_BG, font=("Tahoma", 8, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 6)
        )
        value_cell(about_frame, text="Bastien TILLIER", width=38, anchor="w").grid(
            row=0, column=1, sticky="ew", padx=(12, 0), pady=(0, 6)
        )

        tk.Label(about_frame, text="Langage", bg=WINDOW_BG, font=("Tahoma", 8, "bold")).grid(
            row=1, column=0, sticky="w", pady=(0, 6)
        )
        value_cell(about_frame, text="Python", width=38, anchor="w").grid(
            row=1, column=1, sticky="ew", padx=(12, 0), pady=(0, 6)
        )

        tk.Label(about_frame, text="Version", bg=WINDOW_BG, font=("Tahoma", 8, "bold")).grid(
            row=2, column=0, sticky="w"
        )
        version_row = tk.Frame(about_frame, bg=WINDOW_BG)
        version_row.grid(row=2, column=1, sticky="ew", padx=(12, 0))
        version_row.columnconfigure(0, weight=1)
        value_cell(version_row, text=APP_VERSION_LABEL, width=24, anchor="w").grid(
            row=0, column=0, sticky="ew"
        )
        tool_button(
            version_row,
            text="Vérifier les mises à jour",
            command=self.check_for_update,
            anchor="center",
        ).grid(row=0, column=1, padx=(8, 0))

        support_frame = tk.LabelFrame(self, text="Support", padx=14, pady=14, bg=WINDOW_BG)
        support_frame.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        support_frame.columnconfigure(0, weight=1)

        tk.Label(
            support_frame,
            text=f"Besoin d'aide sur {APP_TITLE} ?",
            bg=WINDOW_BG,
            fg=ACCENT_BLUE,
            font=("Tahoma", 10, "bold"),
        ).grid(row=0, column=0, sticky="w")

        message_frame = tk.Frame(support_frame, bg=PANEL_BG, bd=1, relief="sunken", padx=10, pady=9)
        message_frame.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        tk.Label(
            message_frame,
            text=SUPPORT_MESSAGE,
            bg=PANEL_BG,
            justify="left",
            fg=MUTED_TEXT,
            wraplength=620,
            font=("Tahoma", 8),
        ).pack(anchor="w")

        button_row = tk.Frame(support_frame, bg=WINDOW_BG)
        button_row.grid(row=2, column=0, sticky="ew", pady=(18, 0))
        button_row.columnconfigure(0, weight=1)
        button_row.columnconfigure(1, weight=1)

        teams_button = tool_button(
            button_row,
            text="Ouvrir Teams",
            command=self.open_teams,
            image=self.teams_icon,
            width=18,
            padx=18,
            pady=12,
            font=("Tahoma", 9, "bold"),
            anchor="center",
        )
        teams_button.grid(row=0, column=0, padx=(0, 10), sticky="ew")

        mail_button = tool_button(
            button_row,
            text="Envoyer un mail",
            command=self.open_mail,
            image=self.mail_icon,
            width=18,
            padx=18,
            pady=12,
            font=("Tahoma", 9, "bold"),
            anchor="center",
        )
        mail_button.grid(row=0, column=1, sticky="ew")

    def open_teams(self):
        ok, message = open_teams_application()
        self.app.set_status(message, OK_COLOR if ok else ERROR_COLOR)

    def open_mail(self):
        ok, message = open_outlook_mail(SUPPORT_EMAIL)
        self.app.set_status(message, OK_COLOR if ok else ERROR_COLOR)

    def check_for_update(self):
        self.app.check_for_update_manual()

    def _on_key_egg(self, event):
        # Actif uniquement quand la vue Infos est la vue en cours d'affichage.
        if getattr(self.app, "current_module_key", None) != "support":
            self._egg_buffer = ""
            return
        # Ignore les touches modificatrices / navigation.
        key = (event.keysym or "").lower()
        if len(key) != 1 or not key.isalpha():
            return
        self._egg_buffer = (self._egg_buffer + key)[-len(self._EASTER_EGG_KEYS):]
        if self._egg_buffer == self._EASTER_EGG_KEYS:
            self._egg_buffer = ""
            self._launch_snake()

    def _launch_snake(self):
        # Une seule instance a la fois.
        existing = getattr(self, "_snake_window", None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            existing.focus_force()
            return
        self._snake_window = _SnakeGame(self)
