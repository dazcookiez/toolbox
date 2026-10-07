import threading
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

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
from ..widgets import info_panel, tool_button


# Quatre modes de securite. "Auto" sonde successivement SSL/STARTTLS/Rien
# et utilise le premier qui repond (utile quand le serveur SMTP n'est pas
# documente cote conf Vega).
SECURITY_LABELS = (
    ("Auto", "auto", 0),
    ("Rien", "none", 25),
    ("SSL", "ssl", 465),
    ("TLS", "starttls", 587),
)


class SmtpTestTab(ttk.Frame):
    # Onglet "Envoi mail" : formulaire epuré façon Vega + envoi de test +
    # auto-detection de l'authentification (mot de passe puis OAuth2 si refus MFA).

    def __init__(self, parent, app):
        super().__init__(parent, padding=14)
        self.app = app

        self.from_var = tk.StringVar()
        self.account_var = tk.StringVar()
        self.password_var = tk.StringVar()
        self.server_var = tk.StringVar()
        self.port_var = tk.StringVar(value="465")
        self.security_var = tk.StringVar(value="ssl")
        self.to_var = tk.StringVar()
        self.subject_var = tk.StringVar(value="")

        self.body_text = None
        self.log_text = None
        self.test_button = None
        self.reset_button = None
        self._cancel_event = None
        self._oauth_window = None

        self.build_ui()
        self.security_var.trace_add("write", self._on_security_changed)

    # --------------------- UI ---------------------

    def build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        # ---- Carte 1 : configuration SMTP ----
        cfg_frame = tk.LabelFrame(
            self,
            text="Configuration",
            padx=14,
            pady=10,
            bg=WINDOW_BG,
            font=("Tahoma", 9, "bold"),
        )
        cfg_frame.grid(row=0, column=0, sticky="ew")
        cfg_frame.columnconfigure(1, weight=1)
        self.cfg_frame = cfg_frame

        rows = (
            ("Mail expéditeur", self.from_var),
            ("Compte", self.account_var),
            ("Mot de passe", self.password_var),
            ("Serveur SMTP", self.server_var),
        )
        self.password_entry = None
        self.password_visible = False
        self.password_toggle = None
        for index, (label, var) in enumerate(rows):
            tk.Label(cfg_frame, text=label, bg=WINDOW_BG, width=14, anchor="w").grid(
                row=index, column=0, sticky="w", pady=3,
            )
            entry = tk.Entry(
                cfg_frame,
                textvariable=var,
                bd=2,
                relief="sunken",
                font=("Tahoma", 9),
            )
            if label == "Mot de passe":
                entry.configure(show="*")
                # Le bouton oeil prend la derniere colonne ; l'entry n'occupe
                # plus que les colonnes 1-3 pour laisser la place au toggle.
                entry.grid(row=index, column=1, columnspan=3, sticky="ew", padx=(8, 0), pady=3)
                self.password_entry = entry
                self.password_toggle = tk.Button(
                    cfg_frame,
                    text="👁",
                    command=self._toggle_password_visibility,
                    bd=1,
                    relief="raised",
                    font=("Segoe UI Emoji", 9),
                    bg=WINDOW_BG,
                    width=3,
                    cursor="hand2",
                )
                self.password_toggle.grid(row=index, column=4, sticky="w", padx=(4, 0), pady=3)
                # Ctrl+clic gauche dans l'entry bascule aussi la visibilite
                # (raccourci pratique demande).
                entry.bind("<Control-Button-1>", lambda _e: self._toggle_password_visibility())
            else:
                entry.grid(row=index, column=1, columnspan=4, sticky="ew", padx=(8, 0), pady=3)

        # Ligne port + securite (3 cases facon Vega).
        port_row = len(rows)
        tk.Label(cfg_frame, text="Port SMTP", bg=WINDOW_BG, width=14, anchor="w").grid(
            row=port_row, column=0, sticky="w", pady=(8, 3),
        )
        port_entry = tk.Entry(
            cfg_frame,
            textvariable=self.port_var,
            bd=2,
            relief="sunken",
            width=8,
            font=("Tahoma", 9),
            justify="right",
        )
        port_entry.grid(row=port_row, column=1, sticky="w", padx=(8, 14), pady=(8, 3))

        for offset, (label, value, _port) in enumerate(SECURITY_LABELS):
            tk.Radiobutton(
                cfg_frame,
                text=label,
                value=value,
                variable=self.security_var,
                bg=WINDOW_BG,
                font=("Tahoma", 9),
            ).grid(row=port_row, column=2 + offset, sticky="w", padx=(0, 10), pady=(8, 3))

        # ---- Carte 2 : message ----
        msg_frame = tk.LabelFrame(
            self,
            text="Message de test",
            padx=14,
            pady=10,
            bg=WINDOW_BG,
            font=("Tahoma", 9, "bold"),
        )
        msg_frame.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        msg_frame.columnconfigure(1, weight=1)
        msg_frame.rowconfigure(2, weight=1)

        tk.Label(msg_frame, text="Destinataire", bg=WINDOW_BG, width=14, anchor="w").grid(
            row=0, column=0, sticky="w", pady=3,
        )
        tk.Entry(msg_frame, textvariable=self.to_var, bd=2, relief="sunken", font=("Tahoma", 9)).grid(
            row=0, column=1, sticky="ew", padx=(8, 0), pady=3,
        )

        tk.Label(msg_frame, text="Sujet", bg=WINDOW_BG, width=14, anchor="w").grid(
            row=1, column=0, sticky="w", pady=3,
        )
        tk.Entry(msg_frame, textvariable=self.subject_var, bd=2, relief="sunken", font=("Tahoma", 9)).grid(
            row=1, column=1, sticky="ew", padx=(8, 0), pady=3,
        )

        tk.Label(msg_frame, text="Corps", bg=WINDOW_BG, width=14, anchor="nw").grid(
            row=2, column=0, sticky="nw", pady=(6, 3),
        )
        body_box = tk.Frame(msg_frame, bg=WINDOW_BG)
        body_box.grid(row=2, column=1, sticky="ew", padx=(8, 0), pady=(6, 3))
        body_box.columnconfigure(0, weight=1)
        self.body_text = tk.Text(body_box, height=4, bd=2, relief="sunken", wrap="word", bg=WHITE_BG, font=("Tahoma", 9))
        self.body_text.insert("1.0", "test")
        self.body_text.grid(row=0, column=0, sticky="ew")
        body_scroll = ttk.Scrollbar(body_box, orient="vertical", command=self.body_text.yview)
        body_scroll.grid(row=0, column=1, sticky="ns")
        self.body_text.configure(yscrollcommand=body_scroll.set)

        # ---- Bandeau actions ----
        actions = tk.Frame(self, bg=WINDOW_BG)
        actions.grid(row=2, column=0, sticky="ew", pady=(10, 6))
        actions.columnconfigure(2, weight=1)

        self.test_button = tool_button(
            actions,
            text="Tester l'envoi",
            command=self.run_test,
            image=self.app.icons.get("lancer"),
        )
        self.test_button.grid(row=0, column=0, sticky="w")

        self.reset_button = tool_button(
            actions,
            text="Réinitialiser",
            command=self.reset_form,
            image=self.app.icons.get("retour"),
        )
        self.reset_button.grid(row=0, column=1, sticky="w", padx=(8, 0))

        tk.Label(
            actions,
            text=(
                "L'authentification est détectée automatiquement : mot de passe d'abord, puis OAuth2 "
                "Microsoft / Google si MFA. Aucune donnée n'est mémorisée."
            ),
            bg=WINDOW_BG,
            fg=MUTED_TEXT,
            font=("Tahoma", 8, "italic"),
            justify="left",
            wraplength=520,
        ).grid(row=0, column=2, sticky="e", padx=(12, 0))

        # ---- Diagnostic log ----
        log_frame = tk.LabelFrame(
            self,
            text="Diagnostic",
            padx=8,
            pady=6,
            bg=WINDOW_BG,
            font=("Tahoma", 9, "bold"),
        )
        log_frame.grid(row=3, column=0, sticky="nsew", pady=(0, 0))
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        self.log_text = tk.Text(
            log_frame,
            height=10,
            bd=2,
            relief="sunken",
            wrap="word",
            bg=WHITE_BG,
            font=("Consolas", 9),
            state="disabled",
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        log_scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.tag_configure("error", foreground=ERROR_COLOR)
        self.log_text.tag_configure("warn", foreground=WARN_COLOR)
        self.log_text.tag_configure("ok", foreground=OK_COLOR, font=("Consolas", 9, "bold"))

    # --------------------- Reactions UI ---------------------

    def _toggle_password_visibility(self):
        # Bascule entre mot de passe masque (***) et visible. Bouton oeil
        # ou Ctrl+clic gauche dans le champ.
        if self.password_entry is None:
            return
        self.password_visible = not self.password_visible
        if self.password_visible:
            self.password_entry.configure(show="")
            if self.password_toggle is not None:
                self.password_toggle.configure(text="🙈")
        else:
            self.password_entry.configure(show="*")
            if self.password_toggle is not None:
                self.password_toggle.configure(text="👁")

    def _on_security_changed(self, *_args):
        # Suggere le port standard quand l'utilisateur change la securite.
        # En mode Auto, on laisse le port libre : le backend va sonder
        # 465 (SSL) -> 587 (STARTTLS) -> 25 (Rien) et utiliser celui qui repond.
        selected = self.security_var.get()
        if selected == "auto":
            return
        current = self.port_var.get().strip()
        defaults = {str(p) for _label, _value, p in SECURITY_LABELS if p}
        if not current or current in defaults:
            for _label, value, port in SECURITY_LABELS:
                if value == selected:
                    self.port_var.set(str(port))
                    break

    # --------------------- Reset / log ---------------------

    def reset_form(self):
        self.from_var.set("")
        self.account_var.set("")
        self.password_var.set("")
        self.server_var.set("")
        self.port_var.set("465")
        self.security_var.set("ssl")
        self.to_var.set("")
        self.subject_var.set("")
        self.body_text.delete("1.0", "end")
        self.body_text.insert("1.0", "test")
        self._clear_log()
        self.app.set_status("Formulaire SMTP réinitialisé.", ACCENT_BLUE)

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _append_log(self, level, message):
        tag = ""
        if level == "ERROR":
            tag = "error"
        elif level == "WARN":
            tag = "warn"
        elif "REUSSI" in message or "REUSSIE" in message or "accepte" in message.lower():
            tag = "ok"
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{level}] {message}\n", tag)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    # --------------------- Test send ---------------------

    @staticmethod
    def _split_addresses(raw):
        return [piece.strip() for piece in (raw or "").replace(";", ",").split(",") if piece.strip()]

    def collect_config(self):
        # Compte vide => on retombe sur l'expediteur (cas le plus courant : compte = email).
        from_addr = self.from_var.get().strip()
        account = self.account_var.get().strip() or from_addr
        return {
            "server": self.server_var.get().strip(),
            "port": self.port_var.get().strip() or "0",
            "security": self.security_var.get(),
            "auth_kind": "auto",
            "username": account,
            "password": self.password_var.get(),
            "from_addr": from_addr,
            "to_addrs": self._split_addresses(self.to_var.get()),
            "cc_addrs": [],
            "bcc_addrs": [],
            "subject": self.subject_var.get(),
            "body": self.body_text.get("1.0", "end-1c") or "test",
            "attachments": [],
        }

    def run_test(self):
        config = self.collect_config()
        self._clear_log()
        self._append_log("INFO", "Préparation du test...")

        self._cancel_event = threading.Event()

        # Callback live : appele depuis le worker, on schedule sur Tk pour MAJ progressive du diag.
        def on_diag_line(level, message):
            self.after(0, lambda lvl=level, msg=message: self._append_log(lvl, msg))

        ui_callbacks = {
            "oauth_user_code": self._show_device_code,
            "oauth_open_url": self._on_oauth_open_url,
            "cancel_event": self._cancel_event,
            "on_diag_line": on_diag_line,
        }

        def task():
            return self.app.smtp.send_test_email(config, ui_callbacks=ui_callbacks)

        def on_success(_result):
            self._close_oauth_window()
            self.app.set_status("Test SMTP réussi : message accepté par le serveur.", OK_COLOR)
            self.app.audit.log_action(
                "smtp_test_send",
                status="success",
                details={
                    "host": config.get("host"),
                    "port": config.get("port"),
                    "from": config.get("sender"),
                    "to": config.get("recipient"),
                    "auth_method": config.get("auth_method"),
                },
            )

        def on_error(_error_message):
            self._close_oauth_window()
            cancelled = bool(self._cancel_event and self._cancel_event.is_set())
            if cancelled:
                self.app.set_status("Test SMTP annulé.", WARN_COLOR)
            else:
                self.app.set_status("Test SMTP échoué : voir le diagnostic dans l'onglet.", ERROR_COLOR)
            self.app.audit.log_action(
                "smtp_test_send",
                status="warning" if cancelled else "failure",
                details={
                    "host": config.get("host"),
                    "port": config.get("port"),
                    "to": config.get("recipient"),
                    "cancelled": cancelled,
                },
                error=None if cancelled else str(_error_message),
            )

        self.app.run_task(
            "Test SMTP",
            task,
            on_success=on_success,
            on_error=on_error,
            key="smtp_test",
        )

    # --------------------- OAuth callbacks ---------------------

    def _show_device_code(self, info):
        self.after(0, lambda: self._open_device_code_window(info))

    def _close_oauth_window(self):
        # Ferme proprement la popup OAuth si elle existe encore (succes ou erreur).
        window = self._oauth_window
        self._oauth_window = None
        if window is None:
            return
        try:
            window.destroy()
        except Exception:
            pass

    def _request_cancel(self, window):
        if not messagebox.askyesno(
            "Annuler l'opération ?",
            "Voulez-vous vraiment annuler l'opération en cours ?\n\nLe test SMTP sera interrompu.",
            parent=window,
            icon="warning",
        ):
            return
        if self._cancel_event:
            self._cancel_event.set()
        self._append_log("WARN", "Annulation demandée par l'utilisateur. Arrêt en cours...")
        try:
            window.destroy()
        except Exception:
            pass
        if self._oauth_window is window:
            self._oauth_window = None

    def _open_device_code_window(self, info):
        user_code = info.get("user_code", "")
        url = info.get("verification_uri", "https://microsoft.com/devicelogin")
        message = info.get("message", "")
        self._append_log("INFO", f"Code Microsoft : {user_code}  ->  {url}")

        window = tk.Toplevel(self)
        self._oauth_window = window
        window.title("Authentification Microsoft requise")
        window.configure(bg=WINDOW_BG)
        window.transient(self.winfo_toplevel())
        window.resizable(False, False)
        window.protocol("WM_DELETE_WINDOW", lambda: self._request_cancel(window))
        window.bind("<Escape>", lambda _e: self._request_cancel(window))

        tk.Label(
            window,
            text="Authentification Microsoft 365 / Outlook",
            bg=WINDOW_BG,
            fg=ACCENT_BLUE,
            font=("Tahoma", 11, "bold"),
            padx=14,
            pady=10,
        ).pack()

        tk.Label(
            window,
            text=(message or "Ouvrez l'URL ci-dessous et saisissez ce code :"),
            bg=WINDOW_BG,
            wraplength=460,
            justify="left",
            padx=14,
        ).pack()

        code_frame = tk.Frame(window, bg=PANEL_BG, bd=1, relief="sunken", padx=14, pady=10)
        code_frame.pack(padx=14, pady=10)
        tk.Label(
            code_frame,
            text=user_code,
            bg=PANEL_BG,
            fg=ACCENT_BLUE,
            font=("Consolas", 22, "bold"),
        ).pack()

        url_frame = tk.Frame(window, bg=WINDOW_BG, padx=14)
        url_frame.pack(fill="x")
        tk.Label(url_frame, text="URL :", bg=WINDOW_BG).pack(side="left")
        url_entry = tk.Entry(url_frame, bd=2, relief="sunken", width=44)
        url_entry.insert(0, url)
        url_entry.configure(state="readonly")
        url_entry.pack(side="left", padx=(6, 0))

        def copy_code():
            self.clipboard_clear()
            self.clipboard_append(user_code)

        def open_browser():
            try:
                webbrowser.open(url)
            except Exception:
                pass

        button_row = tk.Frame(window, bg=WINDOW_BG, padx=14, pady=10)
        button_row.pack(fill="x")
        tool_button(button_row, text="Copier le code", command=copy_code).pack(side="left")
        tool_button(button_row, text="Ouvrir le navigateur", command=open_browser).pack(side="left", padx=(8, 0))
        tool_button(button_row, text="Annuler", command=lambda: self._request_cancel(window)).pack(side="right")

        info_panel(
            window,
            "Cette fenêtre se ferme automatiquement quand Microsoft confirme votre identité. "
            "Pour interrompre le test, fermez cette fenêtre ou cliquez sur Annuler.",
            wraplength=460,
        ).pack(fill="x", padx=14, pady=(0, 12))

    def _on_oauth_open_url(self, url):
        self._append_log("INFO", f"Navigateur Google : {url}")
