"""Mixin StatusBar : gestion de la barre de statut + progressbar.

Erreurs "epinglees" : un statut ERROR reste affiche jusqu'au clic sur la croix.
Tout statut non-erreur recu pendant ce temps est mis en file d'attente et
restaure au premier dismiss.

Pre-requis cote classe parente (mixin) :
    self.status_var        (tk.StringVar)
    self.status_label      (tk.Label, optionnel)
    self.status_dismiss    (tk.Button, optionnel)
    self.progressbar       (ttk.Progressbar, optionnel)
    self._sticky_error     (bool)
    self._pending_status   (tuple ou None)
"""
from .theme import ERROR_COLOR, OK_COLOR


class StatusBarMixin:
    def set_status(self, message, color="black"):
        # Les erreurs affichent un X pour acquittement manuel. Mais elles sont
        # AUSSI ecrasees automatiquement par tout nouveau set_status non-erreur
        # (ex: une autre action lancee par l'utilisateur) : le user veut voir
        # le statut de l'action en cours, pas une vieille erreur figee.
        if color == ERROR_COLOR:
            self._sticky_error = True
            self._pending_status = None
            self.status_var.set(message)
            if hasattr(self, "status_label"):
                self.status_label.configure(fg=color)
            self._show_status_dismiss(True)
            return
        # Statut non-erreur : si une erreur etait epinglee, on la chasse.
        if self._sticky_error:
            self._sticky_error = False
            self._pending_status = None
            self._show_status_dismiss(False)
        self.status_var.set(message)
        if hasattr(self, "status_label"):
            self.status_label.configure(fg=color)

    def dismiss_status_error(self):
        self._sticky_error = False
        self._show_status_dismiss(False)
        if self._pending_status is not None:
            message, color = self._pending_status
            self._pending_status = None
            self.set_status(message, color)
        else:
            self.set_status("Prêt.", OK_COLOR)

    def _show_status_dismiss(self, visible):
        if not hasattr(self, "status_dismiss"):
            return
        if visible:
            self.status_dismiss.grid(row=0, column=2, sticky="e", padx=(0, 4))
        else:
            self.status_dismiss.grid_forget()

    def set_busy(self, busy):
        # Pas de curseur watch : seule la progressbar dans la statusbar indique l'activite.
        if hasattr(self, "progressbar"):
            if busy:
                self.progressbar.grid(row=0, column=3, sticky="e", padx=(4, 8), pady=2)
                self.progressbar.start(12)
            else:
                self.progressbar.stop()
                self.progressbar.grid_forget()
        self.update_idletasks()
