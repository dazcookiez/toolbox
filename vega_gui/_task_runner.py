"""Mixin TaskRunner : gestion des taches asynchrones et de la file d'evenements.

Extrait de VegaToolApp pour clarifier la responsabilite. Toutes les taches lancees
depuis l'UI passent par run_task() qui spawn un thread daemon, retourne via une
queue thread-safe, et marshalle le callback sur le thread Tk.

Pre-requis cote classe parente (mixin) :
    self.event_queue       (queue.Queue)
    self.busy_keys         (set[str])
    self.logger            (ToolLogger)
    self.log_drawer        (LogDrawer ou None)
    self.modules           (dict[str, ScrollableHost])
    set_busy / set_status  (de StatusBarMixin)
"""
import queue
import threading

from .theme import ACCENT_BLUE, ERROR_COLOR, OK_COLOR


class TaskRunnerMixin:
    def enqueue_log(self, line, level):
        self.event_queue.put(("log", line, level))

    def enqueue_download_progress(self, payload):
        self.event_queue.put(("download_progress", payload))

    def process_events(self):
        # Reinjecte dans la boucle Tk les evenements produits par les threads.
        while True:
            try:
                event = self.event_queue.get_nowait()
            except queue.Empty:
                break

            kind = event[0]
            if kind == "log":
                _, line, level = event
                if self.log_drawer:
                    self.log_drawer.append(line, level)
                continue

            if kind == "download_progress":
                _, payload = event
                download = self.modules.get("download") if self.modules else None
                if download:
                    download.update_progress(payload)
                continue

            if kind == "task_done":
                _, title, result, error, on_success, success_message, key, on_error = event
                self.busy_keys.discard(key)
                if not self.busy_keys:
                    self.set_busy(False)
                # Snapshot du statut AVANT le callback pour detecter si le callback
                # l'a modifie. Si pas modifie ET pas de success_message, on reset a
                # "Prêt." pour eviter de laisser "<task> en cours..." colle.
                pre_status = self.status_var.get()
                if error is None:
                    if on_success:
                        try:
                            on_success(result)
                        except Exception:
                            pass
                    if success_message:
                        self.set_status(success_message, OK_COLOR)
                    elif self.status_var.get() == pre_status:
                        # Aucun set_status pendant le callback : on reset.
                        self.set_status("Prêt.", OK_COLOR)
                    # Audit : log toutes les fins de task (succes)
                    self._audit_task_done(title, "success", result=result)
                else:
                    fixed_ip = self.modules.get("fixed_ip") if self.modules else None
                    download = self.modules.get("download") if self.modules else None
                    if title == "Lecture des cartes réseau" and fixed_ip:
                        fixed_ip.auto_loading = False
                    if title.startswith("Téléchargement") and download:
                        download.mark_failed(error)
                    if on_error:
                        try:
                            on_error(error)
                        except Exception:
                            pass
                        if self.status_var.get() == pre_status:
                            self.set_status(f"{title} en échec : {error}", ERROR_COLOR)
                    else:
                        self.set_status(f"{title} en échec : {error}", ERROR_COLOR)
                    # Audit : log toutes les fins de task (echec)
                    self._audit_task_done(title, "failure", error=error)

        self.after(40, self.process_events)

    def run_task(self, title, func, on_success=None, success_message=None, clear_display=False, key=None, on_error=None):
        # Un worker par "key" en parallele : chaque onglet a sa propre file.
        # key par defaut = title, donc deux lancements du meme titre restent serialises.
        # on_error : callback (Exception) -> None appele sur le thread Tk si la tache leve ;
        #            si fourni, remplace l'affichage d'erreur par defaut dans la statusbar.
        task_key = key or title
        if task_key in self.busy_keys:
            self.set_status("Une opération de ce type est déjà en cours.", ERROR_COLOR)
            return False

        self.busy_keys.add(task_key)
        self.set_busy(True)
        if clear_display:
            self.logger.clear()
            if self.log_drawer:
                self.log_drawer.clear()
        self.set_status(f"{title} en cours...", ACCENT_BLUE)
        # Audit : log toutes les taches lancees
        self._audit_task_start(title, key=task_key)

        worker = threading.Thread(
            target=self._task_worker,
            args=(title, func, on_success, success_message, task_key, on_error),
            daemon=True,
        )
        worker.start()
        return True

    def _audit_task_start(self, title, key=None):
        # Helper safe : audit peut ne pas etre disponible (avant init complete)
        audit = getattr(self, "audit", None)
        if audit is None:
            return
        try:
            audit.log_action(
                "task_start",
                status="info",
                details={"title": title, "key": key},
            )
        except Exception:
            pass

    def _audit_task_done(self, title, status, result=None, error=None):
        audit = getattr(self, "audit", None)
        if audit is None:
            return
        try:
            details = {"title": title}
            if result is not None and isinstance(result, (dict, list, str, int, float, bool)):
                # On evite de serialiser des objets complexes : juste un resume
                if isinstance(result, dict):
                    details["result_keys"] = list(result.keys())[:10]
                elif isinstance(result, (list, tuple)):
                    details["result_count"] = len(result)
            audit.log_action(
                "task_done",
                status=status,
                details=details,
                error=str(error) if error else None,
            )
        except Exception:
            pass

    def _task_worker(self, title, func, on_success, success_message, key, on_error):
        try:
            result = func()
        except Exception as exc:
            self.event_queue.put(("task_done", title, None, exc, on_success, success_message, key, on_error))
        else:
            self.event_queue.put(("task_done", title, result, None, on_success, success_message, key, on_error))
