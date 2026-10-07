import sys
import tkinter as tk
from pathlib import Path

from vega_security import is_valid_password_format, verify_password_with_diag

from ..resources import load_scaled_logo
from ..theme import (
    ACCENT_BLUE,
    APP_FOOTER,
    ERROR_COLOR,
    MUTED_TEXT,
    PANEL_BG,
    WHITE_BG,
    WINDOW_BG,
)


def _is_dev_build():
    # True si on tourne depuis source OU si l'exe contient '_dev' dans son nom.
    # Permet d'afficher le panneau de log debug du login uniquement sur les
    # builds dev (vega_toolbox_dev.exe), pas sur les builds prod.
    if not getattr(sys, "frozen", False):
        return True
    return "_dev" in Path(sys.executable).stem.lower()


class LoginView(tk.Frame):
    def __init__(self, parent, on_success, on_cancel):
        super().__init__(parent, bg=WINDOW_BG)
        self.on_success = on_success
        self.on_cancel = on_cancel
        self.attempts = 0
        self.submit_scheduled = False

        # Logos charges une seule fois pour eviter les recharges Tk.
        # Hauteur similaire pour que les deux logos s'alignent visuellement cote a cote.
        self.logo_zucchetti = load_scaled_logo("media/Logo_Zucchetti.png", 48)
        self.logo_vega = load_scaled_logo("media/logo_vega.png", 48)

        self.pack(fill="both", expand=True)

        # Layout compact : padding minimal, pas de zone vide etalee.
        outer = tk.Frame(self, bg=WINDOW_BG)
        outer.pack(padx=14, pady=12)
        outer.columnconfigure(0, weight=1)

        # Bandeau logos cote a cote : Vega (produit) a gauche, Zucchetti (editeur) a droite.
        header = tk.Frame(outer, bg=WINDOW_BG)
        header.grid(row=0, column=0, sticky="ew")
        logos_box = tk.Frame(header, bg=WINDOW_BG)
        logos_box.pack(anchor="center")
        if self.logo_vega:
            tk.Label(logos_box, image=self.logo_vega, bg=WINDOW_BG).pack(side="left", padx=(0, 14))
        if self.logo_zucchetti:
            tk.Label(logos_box, image=self.logo_zucchetti, bg=WINDOW_BG).pack(side="left")

        # Carte centrale.
        card = tk.Frame(outer, bg=PANEL_BG, bd=1, relief="solid", padx=14, pady=12)
        card.grid(row=1, column=0, sticky="n", pady=(10, 4))
        card.columnconfigure(0, weight=1)

        # Affichage mot de passe : champ Entry classique Win95 sunken, gros
        # asterisques pour la deception visuelle (utilisateur penche pour PIN
        # alors que la DLL CryptoTools accepte alphanumerique).
        self.password_var = tk.StringVar()
        self.entry = tk.Entry(
            card,
            textvariable=self.password_var,
            show="*",
            justify="center",
            font=("Tahoma", 18, "bold"),
            bd=2,
            relief="sunken",
            bg=WHITE_BG,
            fg=ACCENT_BLUE,
        )
        self.entry.grid(row=1, column=0, sticky="ew", ipady=6, pady=(0, 10))
        # Saisie clavier autorisee (UX login standard) + numpad en alternative.
        # Return / Enter valide. Echap annule.
        self.entry.bind("<Return>", lambda _e: self.submit())
        self.entry.bind("<KP_Enter>", lambda _e: self.submit())
        self.entry.bind("<Escape>", lambda _e: self.cancel())
        # Effacer le message d'erreur des qu'on edite (visuel : nouvelle tentative)
        self.password_var.trace_add("write", self._on_password_changed)

        # Numpad 3x4 Win95 : 1-9 / clear-0-enter. Boutons raised classiques.
        keypad = tk.Frame(card, bg=PANEL_BG)
        keypad.grid(row=2, column=0, sticky="ew")
        for col in range(3):
            keypad.columnconfigure(col, weight=1, uniform="keypad")

        digits = [
            ("1", lambda: self._append("1")),
            ("2", lambda: self._append("2")),
            ("3", lambda: self._append("3")),
            ("4", lambda: self._append("4")),
            ("5", lambda: self._append("5")),
            ("6", lambda: self._append("6")),
            ("7", lambda: self._append("7")),
            ("8", lambda: self._append("8")),
            ("9", lambda: self._append("9")),
            ("Effacer", self._clear),
            ("0", lambda: self._append("0")),
            ("Entrer", self.submit),
        ]
        for index, (label, command) in enumerate(digits):
            is_action = label in {"Effacer", "Entrer"}
            tk.Button(
                keypad,
                text=label,
                font=("Tahoma", 9, "bold") if is_action else ("Tahoma", 14, "bold"),
                command=command,
                bg=WINDOW_BG if is_action else WHITE_BG,
                fg=MUTED_TEXT if is_action else ACCENT_BLUE,
                activebackground=PANEL_BG,
                activeforeground=ACCENT_BLUE,
                bd=1,
                relief="raised",
                highlightthickness=0,
                cursor="hand2",
            ).grid(
                row=index // 3,
                column=index % 3,
                sticky="nsew",
                padx=3,
                pady=3,
                ipady=6,
            )

        self.info_var = tk.StringVar(value="")
        tk.Label(
            card,
            textvariable=self.info_var,
            fg=ERROR_COLOR,
            bg=PANEL_BG,
            font=("Tahoma", 9, "bold"),
            height=1,
        ).grid(row=3, column=0, sticky="ew", pady=(8, 0))

        # --- Panneau debug DLL (build dev uniquement) ---
        self._dev_mode = _is_dev_build()
        if self._dev_mode:
            debug_frame = tk.LabelFrame(
                card, text="DEBUG DLL (build dev)",
                bg=PANEL_BG, fg=MUTED_TEXT,
                font=("Tahoma", 8, "bold"), padx=6, pady=4,
            )
            debug_frame.grid(row=4, column=0, sticky="ew", pady=(8, 0))
            self.debug_var = tk.StringVar(value="(en attente d'une saisie)")
            tk.Label(
                debug_frame,
                textvariable=self.debug_var,
                bg=PANEL_BG, fg=ACCENT_BLUE,
                font=("Consolas", 9),
                justify="left", anchor="w",
                wraplength=320,
            ).pack(fill="x")

        # Pied : version + footer simple.
        tk.Label(
            outer,
            text=APP_FOOTER,
            bg=WINDOW_BG,
            fg=MUTED_TEXT,
            font=("Tahoma", 8),
        ).grid(row=2, column=0, sticky="ew", pady=(6, 0))

        self.entry.focus_set()

    def _on_password_changed(self, *_args):
        if self.info_var.get():
            self.info_var.set("")

    def _append(self, value):
        # Insertion via numpad : on respecte la position du curseur si l'utilisateur
        # a cliqué au milieu du mot de passe, plutôt que de toujours append en fin.
        try:
            pos = self.entry.index(tk.INSERT)
            self.entry.insert(pos, value)
        except Exception:
            self.password_var.set(self.password_var.get() + value)
        self.entry.focus_set()

    def _clear(self):
        self.password_var.set("")
        self.entry.focus_set()

    def submit(self):
        value = self.password_var.get()
        if not is_valid_password_format(value):
            return

        ok, diag = verify_password_with_diag(value)

        # Update du panneau debug (build dev uniquement)
        if self._dev_mode:
            lines = [
                f"input_length : {diag.get('input_length')}",
                f"dll_loaded   : {diag.get('dll_loaded')}",
                f"raw_result   : {diag.get('raw_result')}",
            ]
            if diag.get("init_error"):
                lines.append(f"init_error   : {diag['init_error']}")
            if diag.get("error"):
                lines.append(f"error        : {diag['error']}")
            lines.append(f"verdict      : {'OK (>0)' if ok else 'REFUSE (==0)'}")
            self.debug_var.set("\n".join(lines))

        if ok:
            self.on_success()
            return

        # Echec d'initialisation du decodeur (.NET absent, DLL manquante) :
        # ce n'est pas un mauvais mot de passe, on le dit clairement au lieu
        # d'afficher "Mot de passe invalide" et de decompter les tentatives.
        if not diag.get("dll_loaded") and diag.get("init_error"):
            self.info_var.set("Validation impossible — voir le message ci-dessous.")
            self._show_init_error(diag["init_error"])
            self.password_var.set("")
            return

        self.attempts += 1
        self.password_var.set("")
        if self.attempts >= 3:
            self.info_var.set("Trop de tentatives.")
            self.after(500, self.cancel)
            return
        self.info_var.set(f"Mot de passe invalide. Tentative {self.attempts}/3.")
        self.entry.focus_set()

    def _show_init_error(self, message):
        # Fenetre d'explication quand la validation ne peut pas s'initialiser.
        # Cas typique : Windows 7 nu, livre avec .NET 3.5.1 seulement.
        try:
            from tkinter import messagebox
            messagebox.showerror("Validation indisponible", message, parent=self)
        except Exception:
            # Repli : au moins afficher le texte dans la zone d'information.
            self.info_var.set(message[:120])

    def cancel(self):
        self.on_cancel()
