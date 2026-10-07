import os
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from vega_backend import default_download_dir
from vega_backend._common import VEGA6_GROUPS

from ..theme import ACCENT_BLUE, ERROR_COLOR, MUTED_TEXT, OK_COLOR, WARN_COLOR, WHITE_BG, WINDOW_BG
from ..utils import format_bytes, format_eta, format_speed
from ..widgets import info_panel, tool_button, value_cell


class DownloadTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=8)
        self.app = app
        self.target_var = tk.StringVar(value=str(default_download_dir()))
        self.vega_root_var = tk.StringVar(value="")
        self.auto_extract_var = tk.BooleanVar(value=True)
        self.selection_var = tk.StringVar(value="Aucun fichier actif.")
        self.checked_var = tk.StringVar(value="0 fichier coché")
        self.url_var = tk.StringVar(value="")
        self.progress_var = tk.DoubleVar(value=0.0)
        self.progress_text_var = tk.StringVar(value="")
        self.queue_var = tk.StringVar(value="Aucun téléchargement en cours.")
        self.eta_var = tk.StringVar(value="Temps restant estimé : --:--")
        self.speed_var = tk.StringVar(value="Vitesse : --")
        self.detail_var = tk.StringVar(value="")
        self.rows = {}
        self.item_vars = {}
        self.item_cards = {}
        self.checked_ids = set()
        self.selected_id = None
        self.dynamic_loaded = False
        self.build_ui()
        self.populate_items(self.app.downloads.list_static_items())

    def set_vega_root(self, value):
        if value:
            self.vega_root_var.set(value)

    def browse_vega_root(self):
        initial_dir = self.vega_root_var.get().strip() or str(Path.cwd())
        selected = filedialog.askdirectory(title="Sélectionnez la racine Vega", initialdir=initial_dir)
        if selected:
            self.vega_root_var.set(selected)

    def wants_vega6_extract(self, items):
        if not self.auto_extract_var.get():
            return False
        return any(item.get("group") in VEGA6_GROUPS and item.get("filename", "").lower().endswith(".zip") for item in items)

    def build_ui(self):
        self.columnconfigure(0, weight=3)
        self.columnconfigure(1, weight=2)
        self.rowconfigure(1, weight=1)

        target_frame = tk.LabelFrame(self, text="Dossier de destination", padx=8, pady=6, bg=WINDOW_BG)
        target_frame.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        target_frame.columnconfigure(1, weight=1)
        self.target_frame = target_frame

        tk.Label(target_frame, text="Dossier cible", bg=WINDOW_BG).grid(row=0, column=0, sticky="w")
        tk.Entry(target_frame, textvariable=self.target_var, bd=2, relief="sunken").grid(
            row=0,
            column=1,
            sticky="ew",
            padx=8,
        )
        tool_button(
            target_frame,
            text="Parcourir...",
            command=self.browse_target,
            image=self.app.icons.get("parcourir"),
            width=13,
        ).grid(row=0, column=2)

        list_frame = tk.LabelFrame(self, text="Catalogue de téléchargement", padx=8, pady=6, bg=WINDOW_BG)
        list_frame.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        list_frame.rowconfigure(1, weight=1)
        list_frame.columnconfigure(0, weight=1)

        header = tk.Frame(list_frame, bg=WINDOW_BG)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        header.columnconfigure(0, weight=1)
        tk.Label(
            header,
            text="Cochez les éléments à télécharger. Cliquez sur une ligne pour voir son détail.",
            bg=WINDOW_BG,
            fg=MUTED_TEXT,
            anchor="w",
        ).grid(row=0, column=0, sticky="w")

        canvas_host = tk.Frame(list_frame, bg=WINDOW_BG, bd=1, relief="sunken")
        canvas_host.grid(row=1, column=0, sticky="nsew")
        canvas_host.columnconfigure(0, weight=1)
        canvas_host.rowconfigure(0, weight=1)

        self.catalog_canvas = tk.Canvas(
            canvas_host,
            bg=WHITE_BG,
            highlightthickness=0,
            bd=0,
        )
        self.catalog_canvas.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(canvas_host, orient="vertical", command=self.catalog_canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.catalog_canvas.configure(yscrollcommand=scrollbar.set)

        self.catalog_inner = tk.Frame(self.catalog_canvas, bg=WHITE_BG)
        self.catalog_window = self.catalog_canvas.create_window((0, 0), window=self.catalog_inner, anchor="nw")
        self.catalog_inner.bind("<Configure>", self.on_catalog_configure)
        self.catalog_canvas.bind("<Configure>", self.on_canvas_configure)

        right_panel = tk.Frame(self, bg=WINDOW_BG)
        right_panel.grid(row=1, column=1, sticky="nsew")
        right_panel.columnconfigure(0, weight=1)

        action_frame = tk.LabelFrame(right_panel, text="Actions téléchargement", padx=8, pady=6, bg=WINDOW_BG)
        action_frame.grid(row=0, column=0, sticky="ew")
        action_frame.columnconfigure(0, weight=1)

        tool_button(
            action_frame,
            text="Actualiser VEGA6",
            command=self.refresh_catalog,
            image=self.app.icons.get("actualiser"),
            width=24,
        ).grid(row=0, column=0, sticky="ew", pady=(0, 8))

        tool_button(
            action_frame,
            text="Tout cocher",
            command=self.check_all,
            image=self.app.icons.get("verifier"),
            width=24,
        ).grid(row=1, column=0, sticky="ew", pady=(0, 8))

        tool_button(
            action_frame,
            text="Tout décocher",
            command=self.uncheck_all,
            image=self.app.icons.get("retour"),
            width=24,
        ).grid(row=2, column=0, sticky="ew", pady=(0, 8))

        self.download_button = tool_button(
            action_frame,
            text="Télécharger les fichiers cochés",
            command=self.download_selected,
            image=self.app.icons.get("lancer"),
            width=24,
            state="disabled",
        )
        self.download_button.grid(row=3, column=0, sticky="ew", pady=(0, 8))

        tool_button(
            action_frame,
            text="Ouvrir le dossier",
            command=self.open_target_folder,
            image=self.app.icons.get("parcourir"),
            width=24,
        ).grid(row=4, column=0, sticky="ew")

        info_panel(
            action_frame,
            "Les liens fixes sont intégrés au logiciel. Les listes VEGA6 PROD et BETA sont relues sur le serveur lors de l'actualisation.",
            wraplength=280,
        ).grid(row=5, column=0, sticky="ew", pady=(12, 0))

        summary_frame = tk.LabelFrame(right_panel, text="Sélection et progression", padx=8, pady=6, bg=WINDOW_BG)
        summary_frame.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        summary_frame.columnconfigure(0, weight=1)

        tk.Label(summary_frame, text="Fichier actif", bg=WINDOW_BG, font=("Tahoma", 8, "bold")).grid(
            row=0,
            column=0,
            sticky="w",
        )
        value_cell(summary_frame, textvariable=self.selection_var, width=34, anchor="w").grid(
            row=1,
            column=0,
            sticky="ew",
            pady=(4, 10),
        )

        tk.Label(summary_frame, text="Éléments cochés", bg=WINDOW_BG, font=("Tahoma", 8, "bold")).grid(
            row=2,
            column=0,
            sticky="w",
        )
        value_cell(summary_frame, textvariable=self.checked_var, width=34, anchor="w").grid(
            row=3,
            column=0,
            sticky="ew",
            pady=(4, 10),
        )

        tk.Label(summary_frame, text="Lien source", bg=WINDOW_BG, font=("Tahoma", 8, "bold")).grid(
            row=4,
            column=0,
            sticky="w",
        )
        tk.Label(
            summary_frame,
            textvariable=self.url_var,
            bg=WHITE_BG,
            relief="sunken",
            bd=1,
            justify="left",
            anchor="w",
            wraplength=310,
            padx=6,
            pady=6,
        ).grid(row=5, column=0, sticky="ew", pady=(4, 10))

        tk.Label(summary_frame, text="Progression", bg=WINDOW_BG, font=("Tahoma", 8, "bold")).grid(
            row=6,
            column=0,
            sticky="w",
        )
        self.progressbar = ttk.Progressbar(
            summary_frame,
            orient="horizontal",
            mode="determinate",
            maximum=100.0,
            variable=self.progress_var,
        )
        self.progressbar.grid(row=7, column=0, sticky="ew", pady=(4, 6))
        tk.Label(summary_frame, textvariable=self.progress_text_var, bg=WINDOW_BG, font=("Tahoma", 18, "bold")).grid(
            row=8,
            column=0,
            sticky="w",
        )
        tk.Label(summary_frame, textvariable=self.queue_var, bg=WINDOW_BG, font=("Tahoma", 9, "bold")).grid(
            row=9,
            column=0,
            sticky="w",
            pady=(4, 0),
        )
        tk.Label(summary_frame, textvariable=self.eta_var, bg=WINDOW_BG, font=("Tahoma", 9)).grid(
            row=10,
            column=0,
            sticky="w",
            pady=(2, 0),
        )
        tk.Label(summary_frame, textvariable=self.speed_var, bg=WINDOW_BG, font=("Tahoma", 9)).grid(
            row=11,
            column=0,
            sticky="w",
            pady=(2, 0),
        )
        tk.Label(
            summary_frame,
            textvariable=self.detail_var,
            bg=WINDOW_BG,
            justify="left",
            wraplength=310,
        ).grid(row=12, column=0, sticky="w", pady=(8, 0))

    def set_progress_value_visible(self, visible):
        widget = getattr(self, "progress_value_label", None)
        if not widget:
            return
        if visible:
            widget.grid()
        else:
            widget.grid_remove()

    def browse_target(self):
        initial_dir = self.target_var.get().strip() or str(default_download_dir())
        selected = filedialog.askdirectory(title="Sélectionnez le dossier de destination", initialdir=initial_dir)
        if selected:
            self.target_var.set(selected)

    def open_target_folder(self):
        target = Path(self.target_var.get().strip() or default_download_dir())
        target.mkdir(parents=True, exist_ok=True)
        os.startfile(str(target))

    def populate_items(self, items):
        available_ids = {item["id"] for item in items}
        self.checked_ids &= available_ids
        self.rows.clear()
        self.item_vars.clear()
        self.item_cards.clear()
        self.catalog_canvas.yview_moveto(0)

        for child in self.catalog_inner.winfo_children():
            child.destroy()

        grouped = {}
        for item in items:
            grouped.setdefault(item["group"], []).append(item)
            self.rows[item["id"]] = item

        first_id = None
        for group_name in sorted(grouped.keys()):
            group_frame = tk.LabelFrame(self.catalog_inner, text=group_name, bg=WHITE_BG, padx=8, pady=8)
            group_frame.pack(fill="x", expand=True, padx=8, pady=(0, 8))
            group_frame.columnconfigure(0, weight=1)

            for item in grouped[group_name]:
                if first_id is None:
                    first_id = item["id"]
                self.create_item_card(group_frame, item)

        if self.selected_id not in self.rows:
            self.selected_id = first_id
        self.update_checked_state()
        self.select_item(self.selected_id)

    def refresh_catalog(self):
        # Les éléments fixes sont locaux, la liste VEGA6 est chargée à la demande.
        def task():
            items = self.app.downloads.list_static_items()
            items.extend(self.app.downloads.list_vega6_items())
            return items

        def on_success(items):
            self.dynamic_loaded = True
            self.populate_items(items)
            self.app.set_status("Liste de téléchargement actualisée.", OK_COLOR)

        self.app.run_task(
            "Actualisation de la liste de téléchargement",
            task,
            on_success=on_success,
            clear_display=False,
        )

    def create_item_card(self, parent, item):
        item_id = item["id"]
        variable = tk.BooleanVar(value=item_id in self.checked_ids)
        self.item_vars[item_id] = variable

        card = tk.Frame(parent, bg=WHITE_BG, bd=1, relief="groove", padx=6, pady=5, cursor="hand2")
        card.pack(fill="x", expand=True, pady=(0, 6))
        card.columnconfigure(1, weight=1)

        check = tk.Checkbutton(
            card,
            variable=variable,
            bg=WHITE_BG,
            activebackground=WHITE_BG,
            command=lambda iid=item_id: self.on_check_changed(iid),
        )
        check.grid(row=0, column=0, rowspan=2, sticky="nw", padx=(0, 8))

        name_label = tk.Label(card, text=item["name"], bg=WHITE_BG, font=("Tahoma", 9, "bold"), anchor="w")
        name_label.grid(row=0, column=1, sticky="ew")

        file_label = tk.Label(card, text=item["filename"], bg=WHITE_BG, fg=MUTED_TEXT, anchor="w")
        file_label.grid(row=1, column=1, sticky="ew", pady=(2, 0))

        url_label = tk.Label(card, text=item["url"], bg=WHITE_BG, fg=ACCENT_BLUE, anchor="w")
        url_label.grid(row=2, column=1, sticky="ew", pady=(4, 0))

        for widget in (card, name_label, file_label, url_label):
            widget.bind("<Button-1>", lambda _event, iid=item_id: self.select_item(iid))

        self.item_cards[item_id] = {
            "frame": card,
            "check": check,
            "labels": [name_label, file_label, url_label],
        }
        self.paint_item_card(item_id)

    def paint_item_card(self, item_id):
        card_info = self.item_cards.get(item_id)
        if not card_info:
            return
        selected = item_id == self.selected_id
        bg = "#dbe7f6" if selected else WHITE_BG
        for widget in [card_info["frame"], card_info["check"], *card_info["labels"]]:
            widget.configure(bg=bg)
        card_info["check"].configure(activebackground=bg)

    def on_catalog_configure(self, _event=None):
        self.catalog_canvas.configure(scrollregion=self.catalog_canvas.bbox("all"))

    def on_canvas_configure(self, event):
        self.catalog_canvas.itemconfigure(self.catalog_window, width=event.width)

    def select_item(self, item_id):
        self.selected_id = item_id
        for known_id in self.item_cards:
            self.paint_item_card(known_id)
        self.on_selection_changed()

    def on_selection_changed(self, _event=None):
        item = self.rows.get(self.selected_id)
        if not item:
            self.selection_var.set("Aucun fichier actif.")
            self.url_var.set("")
            return

        self.selection_var.set(item["name"])
        self.url_var.set(item["url"])

    def selected_items(self):
        return [item for item_id, item in self.rows.items() if item_id in self.checked_ids]

    def update_checked_state(self):
        count = len(self.checked_ids)
        suffix = "s" if count != 1 else ""
        self.checked_var.set(f"{count} fichier{suffix} coché{suffix}")
        self.download_button.configure(state=("normal" if count else "disabled"))
        for item_id, variable in self.item_vars.items():
            should_be_checked = item_id in self.checked_ids
            if variable.get() != should_be_checked:
                variable.set(should_be_checked)

    def on_check_changed(self, item_id):
        variable = self.item_vars.get(item_id)
        if not variable:
            return
        if variable.get():
            self.checked_ids.add(item_id)
        else:
            self.checked_ids.discard(item_id)
        self.select_item(item_id)
        self.update_checked_state()

    def check_all(self):
        self.checked_ids = {item["id"] for item in self.rows.values()}
        self.update_checked_state()

    def uncheck_all(self):
        self.checked_ids.clear()
        self.update_checked_state()

    def download_selected(self):
        # Envoie les éléments cochés dans la file de téléchargement backend.
        items = self.selected_items()
        if not items:
            self.app.set_status("Cochez au moins un fichier à télécharger.", ERROR_COLOR)
            return

        target = self.target_var.get().strip() or str(default_download_dir())
        extract_vega6 = self.wants_vega6_extract(items)
        vega_root = self.vega_root_var.get().strip()

        if extract_vega6 and not vega_root:
            self.app.set_status("Renseignez la racine Vega pour extraire automatiquement le paquet V6.", ERROR_COLOR)
            return
        if extract_vega6 and not Path(vega_root).expanduser().exists():
            self.app.set_status("La racine Vega indiquée est introuvable.", ERROR_COLOR)
            return

        self.progress_var.set(0.0)
        self.progress_text_var.set("0 %")
        self.detail_var.set(f"Téléchargement en préparation pour {len(items)} fichier(s)")
        self.queue_var.set(f"File d'attente : {len(items)} fichier(s)")
        self.eta_var.set("Temps restant estimé : --:--")
        self.speed_var.set("Vitesse : --")
        self.set_progress_value_visible(True)
        # Active le bouton Annuler, desactive le bouton Telecharger pendant le run.
        if hasattr(self, "cancel_button") and self.cancel_button:
            self.cancel_button.configure(state="normal")
        if self.download_button:
            self.download_button.configure(state="disabled")

        def on_success(payload):
            results = payload["downloads"]
            extracted = payload["extracted"]
            last_result = results[-1]
            self.progress_var.set(100.0)
            self.progress_text_var.set("")
            if extracted:
                target_v6 = Path(extracted[-1]["target_path"])
                self.detail_var.set(
                    f"Terminé : {len(results)} fichier(s). Paquet VEGA6 extrait dans {target_v6}"
                )
                self.app.set_status(
                    f"Téléchargement terminé : {len(results)} fichier(s), extraction VEGA6 effectuée.",
                    OK_COLOR,
                )
            else:
                self.detail_var.set(f"Terminé : {len(results)} fichier(s) dans {Path(last_result['target_path']).parent}")
                self.app.set_status(f"Téléchargement terminé : {len(results)} fichier(s).", OK_COLOR)
            self.queue_var.set(f"File d'attente : {len(results)}/{len(results)}")
            self.eta_var.set("Temps restant estimé : 00:00")
            self.speed_var.set("Vitesse : --")
            self.set_progress_value_visible(False)

            # Decoche auto les fichiers qui viennent d'etre telecharges : evite
            # de re-telecharger la meme chose sur un clic accidentel.
            done_ids = {item["id"] for item in items}
            self.checked_ids -= done_ids
            self.update_checked_state()

            # Audit
            self.app.audit.log_action(
                "downloads_completed",
                status="success",
                details={
                    "count": len(results),
                    "target_dir": target,
                    "files": [Path(r.get("target_path", "")).name for r in results],
                    "vega6_extracted": bool(extracted),
                    "vega_root": vega_root if extract_vega6 else None,
                },
            )
            self._end_download_ui_state()

        def on_error(err):
            # Distingue annulation utilisateur (warning) vs erreur reelle (failure)
            msg = str(err)
            cancelled = "annule" in msg.lower() or "cancel" in msg.lower()
            if cancelled:
                self.detail_var.set("Téléchargement annulé.")
                self.app.set_status("Téléchargement annulé par l'utilisateur.", WARN_COLOR)
            else:
                self.detail_var.set(f"Erreur : {msg}")
                self.app.set_status(f"Téléchargement échoué : {msg}", ERROR_COLOR)
            self.set_progress_value_visible(False)
            self._end_download_ui_state()
            self.app.audit.log_action(
                "downloads_failed" if not cancelled else "downloads_cancelled",
                status="warning" if cancelled else "failure",
                details={"count": len(items), "target_dir": target},
                error=None if cancelled else msg,
            )

        def task():
            results = self.app.downloads.download_files(items, target)
            extracted = self.app.downloads.extract_vega6_packages(results, vega_root) if extract_vega6 else []
            return {
                "downloads": results,
                "extracted": extracted,
            }

        self.app.run_task(
            "Téléchargement des fichiers cochés",
            task,
            on_success=on_success,
            on_error=on_error,
            clear_display=False,
        )

    def cancel_download(self):
        # Signale au DownloadManager d'interrompre le batch. La boucle de
        # download check l'event entre chaque chunk (~max 250ms de latence).
        if not hasattr(self.app, "downloads"):
            return
        self.app.downloads.request_cancel()
        if hasattr(self, "cancel_button") and self.cancel_button:
            self.cancel_button.configure(state="disabled")
        self.detail_var.set("Annulation en cours...")

    def _end_download_ui_state(self):
        # Restaure l'UI a son etat 'prêt' : disable cancel, re-enable download
        # si des items sont coches.
        if hasattr(self, "cancel_button") and self.cancel_button:
            self.cancel_button.configure(state="disabled")
        # update_checked_state re-active/desactive le download selon checked_ids
        try:
            self.update_checked_state()
        except Exception:
            pass

    def update_progress(self, payload):
        # Maintient le bloc de progression aligné avec les remontées backend.
        total = int(payload.get("total") or 0)
        downloaded = int(payload.get("downloaded") or 0)
        speed = float(payload.get("speed") or 0.0)
        eta_seconds = payload.get("eta_seconds")
        filename = payload.get("filename", "")
        finished = bool(payload.get("finished"))
        batch_index = int(payload.get("batch_index") or 1)
        batch_count = int(payload.get("batch_count") or 1)
        prefix = f"Fichier {batch_index}/{batch_count}"

        if total > 0:
            percent = min(100.0, (downloaded / total) * 100.0)
            self.progress_var.set(percent)
            self.progress_text_var.set(f"{percent:.1f} %")
            self.detail_var.set(
                f"{format_bytes(downloaded)} / {format_bytes(total)}"
            )
        else:
            self.progress_var.set(100.0 if finished else 0.0)
            self.progress_text_var.set("")
            self.detail_var.set(f"{format_bytes(downloaded)} téléchargés")

        if filename:
            self.selection_var.set(payload.get("name", filename))
        self.queue_var.set(prefix)
        self.eta_var.set(f"Temps restant estimé : {format_eta(eta_seconds)}")
        self.speed_var.set(f"Vitesse : {format_speed(speed)}")
        self.set_progress_value_visible(bool(self.progress_text_var.get()))

        if finished and batch_index < batch_count:
            self.detail_var.set(f"Fichier {batch_index}/{batch_count} terminé. Préparation du suivant...")

    def mark_failed(self, message):
        self.progress_var.set(0.0)
        self.progress_text_var.set("")
        self.detail_var.set(str(message))
        self.eta_var.set("Temps restant estimé : --:--")
        self.speed_var.set("Vitesse : --")
        self.set_progress_value_visible(False)


class CompactDownloadTab(DownloadTab):
    # Variante compacte pensée pour tenir sur un seul écran.
    GROUP_ORDER = {
        "HFSQL": 0,
        "SysPay": 1,
        "Visual C++": 2,
        "VEGA": 3,
        "VEGA6 PROD": 4,
        "VEGA6 BETA": 5,
    }

    def build_ui(self):
        self.columnconfigure(0, weight=3)
        self.columnconfigure(1, weight=2)
        self.rowconfigure(1, weight=1)

        target_frame = tk.LabelFrame(self, text="Téléchargement et extraction VEGA6", padx=8, pady=6, bg=WINDOW_BG)
        target_frame.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        target_frame.columnconfigure(1, weight=1)
        target_frame.columnconfigure(4, weight=1)

        tk.Label(target_frame, text="Dossier cible", bg=WINDOW_BG).grid(row=0, column=0, sticky="w")
        tk.Entry(target_frame, textvariable=self.target_var, bd=2, relief="sunken").grid(
            row=0,
            column=1,
            sticky="ew",
            padx=8,
        )
        tool_button(
            target_frame,
            text="Parcourir...",
            command=self.browse_target,
            image=self.app.icons.get("parcourir"),
            width=13,
        ).grid(row=0, column=2)

        tk.Label(target_frame, text="Racine Vega", bg=WINDOW_BG).grid(row=1, column=0, sticky="w", pady=(8, 0))
        tk.Entry(target_frame, textvariable=self.vega_root_var, bd=2, relief="sunken").grid(
            row=1,
            column=1,
            sticky="ew",
            padx=8,
            pady=(8, 0),
        )
        tool_button(
            target_frame,
            text="Parcourir...",
            command=self.browse_vega_root,
            image=self.app.icons.get("parcourir"),
            width=13,
        ).grid(row=1, column=2, pady=(8, 0))

        tk.Checkbutton(
            target_frame,
            text="Extraire automatiquement les paquets VEGA6 dans vega.dos\\V6",
            variable=self.auto_extract_var,
            bg=WINDOW_BG,
            activebackground=WINDOW_BG,
        ).grid(row=0, column=3, columnspan=2, sticky="w", padx=(16, 0))

        info_panel(
            target_frame,
            "Si un paquet VEGA6 (PROD ou BETA) est téléchargé, il peut être décompressé automatiquement dans la racine Vega choisie. "
            "Le dossier vega.dos\\V6 est créé si besoin.",
            wraplength=360,
        ).grid(row=1, column=3, columnspan=2, sticky="ew", padx=(16, 0), pady=(8, 0))

        catalog_frame = tk.LabelFrame(self, text="Catalogue de téléchargement", padx=8, pady=6, bg=WINDOW_BG)
        catalog_frame.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        catalog_frame.columnconfigure(0, weight=1)
        catalog_frame.rowconfigure(1, weight=1)

        header = tk.Frame(catalog_frame, bg=WINDOW_BG)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        header.columnconfigure(3, weight=1)

        tool_button(
            header,
            text="Actualiser VEGA6",
            command=self.refresh_catalog,
            image=self.app.icons.get("actualiser"),
        ).grid(row=0, column=0, padx=(0, 8), sticky="w")
        tool_button(
            header,
            text="Tout cocher",
            command=self.check_all,
            image=self.app.icons.get("verifier"),
        ).grid(row=0, column=1, padx=(0, 8), sticky="w")
        tool_button(
            header,
            text="Tout décocher",
            command=self.uncheck_all,
            image=self.app.icons.get("retour"),
        ).grid(row=0, column=2, padx=(0, 12), sticky="w")
        tk.Label(
            header,
            textvariable=self.checked_var,
            bg=WINDOW_BG,
            fg=ACCENT_BLUE,
            font=("Tahoma", 9, "bold"),
        ).grid(row=0, column=3, sticky="w")

        columns = ("checked", "name", "group", "filename")
        self.tree = ttk.Treeview(catalog_frame, columns=columns, show="headings", height=11, selectmode="browse")
        self.tree.heading("checked", text="Choix")
        self.tree.heading("name", text="Nom")
        self.tree.heading("group", text="Groupe")
        self.tree.heading("filename", text="Fichier")
        self.tree.column("checked", width=58, minwidth=58, anchor="center")
        self.tree.column("name", width=210, minwidth=160, anchor="w")
        self.tree.column("group", width=110, minwidth=90, anchor="w")
        self.tree.column("filename", width=210, minwidth=150, anchor="w")
        self.tree.grid(row=1, column=0, sticky="nsew")
        self.tree.bind("<<TreeviewSelect>>", self.on_selection_changed)
        self.tree.bind("<ButtonRelease-1>", self.on_tree_click)

        tree_scroll = ttk.Scrollbar(catalog_frame, orient="vertical", command=self.tree.yview)
        tree_scroll.grid(row=1, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=tree_scroll.set)

        right_panel = tk.Frame(self, bg=WINDOW_BG)
        right_panel.grid(row=1, column=1, sticky="nsew")
        right_panel.columnconfigure(0, weight=1)
        right_panel.rowconfigure(1, weight=1)

        action_frame = tk.LabelFrame(right_panel, text="Actions téléchargement", padx=8, pady=6, bg=WINDOW_BG)
        action_frame.grid(row=0, column=0, sticky="ew")
        action_frame.columnconfigure(0, weight=1)

        self.download_button = tool_button(
            action_frame,
            text="Télécharger les fichiers cochés",
            command=self.download_selected,
            image=self.app.icons.get("lancer"),
            width=26,
            state="disabled",
        )
        self.download_button.grid(row=0, column=0, sticky="ew", pady=(0, 8))

        # Bouton annulation : visible et actif seulement pendant un download.
        # On le cree disabled - l'activation se fait dans download_selected().
        self.cancel_button = tool_button(
            action_frame,
            text="Annuler le téléchargement",
            command=self.cancel_download,
            image=self.app.icons.get("retour"),
            width=26,
            state="disabled",
        )
        self.cancel_button.grid(row=1, column=0, sticky="ew", pady=(0, 8))

        tool_button(
            action_frame,
            text="Ouvrir le dossier",
            command=self.open_target_folder,
            image=self.app.icons.get("parcourir"),
            width=26,
        ).grid(row=2, column=0, sticky="ew")

        summary_frame = tk.LabelFrame(right_panel, text="Progression", padx=8, pady=6, bg=WINDOW_BG)
        summary_frame.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        summary_frame.columnconfigure(0, weight=1)

        meter_frame = tk.Frame(summary_frame, bg=WINDOW_BG)
        meter_frame.grid(row=0, column=0, sticky="ew")
        meter_frame.columnconfigure(0, weight=1)

        self.progressbar = ttk.Progressbar(
            meter_frame,
            orient="horizontal",
            mode="determinate",
            maximum=100.0,
            variable=self.progress_var,
        )
        self.progressbar.grid(row=0, column=0, sticky="ew")
        self.progress_value_label = tk.Label(
            meter_frame,
            textvariable=self.progress_text_var,
            bg=WINDOW_BG,
            fg=ACCENT_BLUE,
            font=("Tahoma", 18, "bold"),
        )
        self.progress_value_label.grid(row=0, column=1, sticky="e", padx=(10, 0))

        tk.Label(
            summary_frame,
            textvariable=self.detail_var,
            bg=WINDOW_BG,
            fg=MUTED_TEXT,
            font=("Tahoma", 9),
            anchor="w",
            justify="left",
            wraplength=320,
        ).grid(row=1, column=0, sticky="ew", pady=(8, 0))

        tk.Label(summary_frame, textvariable=self.queue_var, bg=WINDOW_BG, font=("Tahoma", 9, "bold")).grid(
            row=2, column=0, sticky="w", pady=(8, 0)
        )

        stats_frame = tk.Frame(summary_frame, bg=WINDOW_BG)
        stats_frame.grid(row=3, column=0, sticky="ew", pady=(4, 0))
        stats_frame.columnconfigure(0, weight=1)
        stats_frame.columnconfigure(1, weight=1)
        tk.Label(stats_frame, textvariable=self.eta_var, bg=WINDOW_BG, font=("Tahoma", 9)).grid(
            row=0, column=0, sticky="w"
        )
        tk.Label(stats_frame, textvariable=self.speed_var, bg=WINDOW_BG, font=("Tahoma", 9)).grid(
            row=0, column=1, sticky="e"
        )

        self.set_progress_value_visible(False)

    def checked_mark(self, item_id):
        return "☑" if item_id in self.checked_ids else "☐"

    def populate_items(self, items):
        previous_selected = None
        current = self.tree.selection()
        if current:
            selected_item = self.rows.get(current[0])
            if selected_item:
                previous_selected = selected_item["id"]

        available_ids = {item["id"] for item in items}
        self.checked_ids &= available_ids
        self.rows.clear()
        self.tree.delete(*self.tree.get_children())

        ordered = sorted(
            items,
            key=lambda item: (self.GROUP_ORDER.get(item["group"], 99), item["name"].lower()),
        )
        for item in ordered:
            row_id = self.tree.insert(
                "",
                "end",
                values=(self.checked_mark(item["id"]), item["name"], item["group"], item["filename"]),
            )
            self.rows[row_id] = item
            if previous_selected and item["id"] == previous_selected:
                self.tree.selection_set(row_id)

        if not self.tree.selection():
            children = self.tree.get_children()
            if children:
                self.tree.selection_set(children[0])

        self.update_checked_state()
        self.on_selection_changed()

    def on_selection_changed(self, _event=None):
        selection = self.tree.selection()
        if not selection:
            self.selection_var.set("Aucun fichier actif.")
            self.url_var.set("")
            return
        item = self.rows.get(selection[0])
        if not item:
            self.selection_var.set("Aucun fichier actif.")
            self.url_var.set("")
            return
        self.selection_var.set(item["name"])
        self.url_var.set(item["url"])

    def selected_items(self):
        items = []
        for row_id in self.tree.get_children():
            item = self.rows.get(row_id)
            if item and item["id"] in self.checked_ids:
                items.append(item)
        return items

    def update_checked_state(self):
        count = len(self.checked_ids)
        suffix = "s" if count != 1 else ""
        self.checked_var.set(f"{count} fichier{suffix} coché{suffix}")
        self.download_button.configure(state=("normal" if count else "disabled"))
        for row_id, item in self.rows.items():
            self.tree.set(row_id, "checked", self.checked_mark(item["id"]))

    def toggle_checked(self, row_id):
        item = self.rows.get(row_id)
        if not item:
            return
        item_id = item["id"]
        if item_id in self.checked_ids:
            self.checked_ids.remove(item_id)
        else:
            self.checked_ids.add(item_id)
        self.update_checked_state()

    def on_tree_click(self, event):
        row_id = self.tree.identify_row(event.y)
        column_id = self.tree.identify_column(event.x)
        if not row_id:
            return
        self.tree.selection_set(row_id)
        if column_id == "#1":
            self.toggle_checked(row_id)
        self.on_selection_changed()

    def check_all(self):
        self.checked_ids = {item["id"] for item in self.rows.values()}
        self.update_checked_state()

    def uncheck_all(self):
        self.checked_ids.clear()
        self.update_checked_state()
