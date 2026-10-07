"""Mode Tutoriel : visite guidée interactive de Vega Toolbox.

Architecture (3.8.2-dev) : 2 Toplevel (overlay + callout) pour exploiter
l'alpha transparency native de Windows + boucle de polling 30 ms qui
repositionne tout en continu (au lieu du debounce Configure qui ne fire
qu'a la FIN du drag, causant le decalage visuel pendant le mouvement).

Composants :
  - Overlay Toplevel : fond sombre semi-transparent (-alpha 0.55) qui
    couvre toute l'app, Canvas dessine la bordure jaune autour du target
    et une fleche jaune vers le callout
  - Callout Toplevel : fenetre sans decoration avec titre + dot
    progression + texte + action_hint + boutons Precedent/Suivant/Quitter
  - Polling loop 30 ms (~33 fps) : compare la position/taille app
    courante au dernier snapshot, repositionne overlay + callout +
    redraw spotlight/fleche si different. Cheap : winfo_rootx/y/width/
    height + 2-3 geometry() calls = imperceptible.

Mode interactif (option B) :
  - TutorialStep accepte wait_for=callable(app)->bool et action_hint=str
  - Si wait_for fourni : bouton Suivant disabled, action_hint affiche
    en bleu italique. Le polling check toutes les 300ms : quand le
    callable retourne True, le bouton se debloque et l'action_hint
    devient '✓ Action effectuee'.
"""
import tkinter as tk

from .theme import ACCENT_BLUE, PANEL_BG
from .widgets import tool_button


SPOTLIGHT_COLOR = "#ffd700"
ARROW_COLOR = "#ffd700"
ARROW_WIDTH = 3
SPOTLIGHT_PAD = 6
OVERLAY_COLOR = "#000000"
OVERLAY_ALPHA = 0.55
CALLOUT_WRAPLENGTH = 380
CALLOUT_DEFAULT_W = 440
CALLOUT_DEFAULT_H = 220
POLL_GEOM_MS = 30                # 30 ms ≈ 33 fps - suit le drag en temps reel
POLL_WAIT_FOR_MS = 300           # check wait_for moins souvent (logique app)


class TutorialStep:
    """Une etape de tutoriel.

    Args:
        title: titre de la fenetre callout
        body: texte explicatif
        module: cle de l'onglet a activer (ex: 'migration', 'pc_info')
        target: callable(app) -> tk.Widget OU None (callout centre, pas
            de spotlight)
        wait_for: callable(app) -> bool. Si fourni, bouton Suivant
            disabled jusqu'a True (poll 300 ms).
        action_hint: str affiche en italique bleu pour guider l'user
            ('Cliquez sur Verifier pour continuer').
    """

    def __init__(self, title, body, module=None, target=None,
                 wait_for=None, action_hint=None):
        self.title = title
        self.body = body
        self.module = module
        self.target = target
        self.wait_for = wait_for
        self.action_hint = action_hint


class TutorialOverlay:
    def __init__(self, app, steps):
        self.app = app
        self.steps = list(steps)
        self.idx = 0
        self.overlay = None
        self.canvas = None
        self.callout = None
        self.callout_title_var = None
        self.callout_body_var = None
        self.callout_step_var = None
        self.action_hint_var = None
        self.btn_prev = None
        self.btn_next = None
        self.btn_quit = None
        self.dots_frame = None
        self._escape_binding_id = None
        self._poll_geom_after_id = None
        self._wait_for_after_id = None
        # Snapshot pour detecter les changements (eviter redraw inutiles).
        self._last_geom = None
        self._last_target_rect = None

    # ---------- Cycle de vie ----------

    def start(self):
        if not self.steps:
            return
        self.idx = 0
        self._build_overlay()
        self._build_callout()
        self._escape_binding_id = self.app.bind(
            "<Escape>", lambda _e: self.stop(), add="+",
        )
        self._show_step()
        # Demarre la boucle de polling pour suivre l'app en temps reel
        self._poll_geom()

    def stop(self):
        if self._escape_binding_id is not None:
            try:
                self.app.unbind("<Escape>", self._escape_binding_id)
            except Exception:
                pass
            self._escape_binding_id = None
        for attr_id in ("_poll_geom_after_id", "_wait_for_after_id"):
            after_id = getattr(self, attr_id, None)
            if after_id is not None:
                try:
                    self.app.after_cancel(after_id)
                except Exception:
                    pass
                setattr(self, attr_id, None)
        for attr in ("callout", "overlay"):
            widget = getattr(self, attr, None)
            if widget is not None:
                try:
                    widget.destroy()
                except Exception:
                    pass
            setattr(self, attr, None)
        self.canvas = None

    # ---------- Construction UI ----------

    def _build_overlay(self):
        self.app.update_idletasks()
        x, y, w, h = self._app_geometry()
        self.overlay = tk.Toplevel(self.app)
        self.overlay.overrideredirect(True)
        self.overlay.attributes("-topmost", True)
        self.overlay.attributes("-alpha", OVERLAY_ALPHA)
        self.overlay.geometry(f"{w}x{h}+{x}+{y}")
        self.overlay.configure(bg=OVERLAY_COLOR)
        self.overlay.bind("<Escape>", lambda _e: self.stop())

        self.canvas = tk.Canvas(
            self.overlay, bg=OVERLAY_COLOR,
            highlightthickness=0, bd=0, takefocus=0,
        )
        self.canvas.pack(fill="both", expand=True)
        # Bloque les clics sur l'overlay (evite manips parasites)
        self.canvas.bind("<Button-1>", lambda _e: "break")

    def _build_callout(self):
        self.callout = tk.Toplevel(self.app)
        self.callout.overrideredirect(True)
        self.callout.attributes("-topmost", True)
        self.callout.configure(bg=PANEL_BG, bd=2, relief="raised")
        self.callout.bind("<Escape>", lambda _e: self.stop())

        title_bar = tk.Frame(self.callout, bg=ACCENT_BLUE, padx=10, pady=5)
        title_bar.pack(fill="x")
        self.callout_title_var = tk.StringVar(value="")
        tk.Label(
            title_bar, textvariable=self.callout_title_var,
            bg=ACCENT_BLUE, fg="white", font=("Tahoma", 10, "bold"),
            anchor="w",
        ).pack(side="left", fill="x", expand=True)
        self.callout_step_var = tk.StringVar(value="")
        tk.Label(
            title_bar, textvariable=self.callout_step_var,
            bg=ACCENT_BLUE, fg="white", font=("Tahoma", 8),
        ).pack(side="right")

        # Dot indicator (progression)
        self.dots_frame = tk.Frame(self.callout, bg=PANEL_BG, pady=4)
        self.dots_frame.pack(fill="x")

        body_frame = tk.Frame(self.callout, bg=PANEL_BG, padx=14, pady=10)
        body_frame.pack(fill="both", expand=True)
        self.callout_body_var = tk.StringVar(value="")
        tk.Label(
            body_frame, textvariable=self.callout_body_var,
            bg=PANEL_BG, font=("Tahoma", 9), justify="left",
            wraplength=CALLOUT_WRAPLENGTH, anchor="w",
        ).pack(anchor="w")

        # Action hint (mode wait_for)
        self.action_hint_var = tk.StringVar(value="")
        tk.Label(
            body_frame, textvariable=self.action_hint_var,
            bg=PANEL_BG, fg=ACCENT_BLUE, font=("Tahoma", 9, "italic"),
            justify="left", wraplength=CALLOUT_WRAPLENGTH, anchor="w",
        ).pack(anchor="w", pady=(8, 0))

        btn_frame = tk.Frame(self.callout, bg=PANEL_BG, padx=12, pady=10)
        btn_frame.pack(fill="x")
        self.btn_quit = tool_button(
            btn_frame, text="Quitter", command=self.stop,
            anchor="center", width=11,
        )
        self.btn_quit.pack(side="left")
        self.btn_next = tool_button(
            btn_frame, text="Suivant", command=self._next,
            anchor="center", width=11,
        )
        self.btn_next.pack(side="right")
        self.btn_prev = tool_button(
            btn_frame, text="Précédent", command=self._prev,
            anchor="center", width=11,
        )
        self.btn_prev.pack(side="right", padx=(0, 6))

    def _rebuild_dots(self):
        for w in self.dots_frame.winfo_children():
            w.destroy()
        for i in range(len(self.steps)):
            if i == self.idx:
                color, size = ACCENT_BLUE, 12
            elif i < self.idx:
                color, size = "#666", 10
            else:
                color, size = "#ccc", 10
            tk.Label(
                self.dots_frame, text="●", fg=color, bg=PANEL_BG,
                font=("Tahoma", size),
            ).pack(side="left", padx=2)

    # ---------- Affichage etape ----------

    def _show_step(self):
        if self._wait_for_after_id is not None:
            try:
                self.app.after_cancel(self._wait_for_after_id)
            except Exception:
                pass
            self._wait_for_after_id = None

        step = self.steps[self.idx]
        if step.module:
            try:
                self.app.show_module(step.module)
            except Exception:
                pass

        self.callout_title_var.set(step.title)
        self.callout_step_var.set(f"Étape {self.idx + 1} / {len(self.steps)}")
        self.callout_body_var.set(step.body)
        self.action_hint_var.set("")
        self.btn_prev.configure(state="normal" if self.idx > 0 else "disabled")
        next_label = "Terminer" if self.idx == len(self.steps) - 1 else "Suivant"
        self.btn_next.configure(text=next_label)
        self._rebuild_dots()

        if step.wait_for is not None:
            self.btn_next.configure(state="disabled")
            if step.action_hint:
                self.action_hint_var.set("► " + step.action_hint)
            self._poll_wait_for()
        else:
            self.btn_next.configure(state="normal")

        # Force redraw immediat (sans attendre le prochain tick polling)
        # Tk a besoin de quelques ms pour terminer le layout apres show_module
        self.app.after(80, lambda: self._refresh_visuals(force=True))

    def _poll_wait_for(self):
        if self.overlay is None or 0 > self.idx or self.idx >= len(self.steps):
            return
        step = self.steps[self.idx]
        if step.wait_for is None:
            return
        try:
            if step.wait_for(self.app):
                self.btn_next.configure(state="normal")
                self.action_hint_var.set("✓ Action effectuée. Cliquez Suivant pour continuer.")
                return
        except Exception:
            pass
        self._wait_for_after_id = self.app.after(POLL_WAIT_FOR_MS, self._poll_wait_for)

    # ---------- Polling de la position app (suivi drag/resize realtime) ----------

    def _poll_geom(self):
        if self.overlay is None:
            return  # tutoriel stoppe
        try:
            self._refresh_visuals(force=False)
        except Exception:
            pass
        self._poll_geom_after_id = self.app.after(POLL_GEOM_MS, self._poll_geom)

    def _refresh_visuals(self, force=False):
        if self.overlay is None or self.canvas is None or self.callout is None:
            return
        try:
            geom = self._app_geometry()
        except Exception:
            return
        target_rect = None
        step = self.steps[self.idx] if 0 <= self.idx < len(self.steps) else None
        if step is not None:
            target_rect = self._resolve_target_rect(step, geom[0], geom[1])

        # Skip si rien n'a change (optim : evite redraw 33x/s pour rien)
        if not force and geom == self._last_geom and target_rect == self._last_target_rect:
            return
        self._last_geom = geom
        self._last_target_rect = target_rect

        x, y, w, h = geom
        # Repositionne overlay
        try:
            self.overlay.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:
            return

        # Redraw spotlight + arrow
        self.canvas.delete("all")
        if target_rect is not None:
            tx, ty, tw, th = target_rect
            self.canvas.create_rectangle(
                tx - SPOTLIGHT_PAD, ty - SPOTLIGHT_PAD,
                tx + tw + SPOTLIGHT_PAD, ty + th + SPOTLIGHT_PAD,
                outline=SPOTLIGHT_COLOR, width=4, tags="spotlight",
            )

        # Repositionne callout
        cx, cy = self._compute_callout_position(target_rect, x, y, w, h)
        try:
            self.callout.geometry(f"+{cx}+{cy}")
            self.callout.lift()
        except Exception:
            return

        # Fleche du callout vers le target (en coords overlay = relatives a l'app)
        if target_rect is not None:
            self.callout.update_idletasks()
            cw = self.callout.winfo_width() or CALLOUT_DEFAULT_W
            ch = self.callout.winfo_height() or CALLOUT_DEFAULT_H
            # cx,cy sont en coords ecran absolu ; on passe en coords overlay
            self._draw_arrow(cx - x, cy - y, cw, ch, target_rect)

    def _resolve_target_rect(self, step, app_rx, app_ry):
        if step.target is None:
            return None
        try:
            widget = step.target(self.app)
        except Exception:
            widget = None
        if widget is None:
            return None
        try:
            tx = widget.winfo_rootx() - app_rx
            ty = widget.winfo_rooty() - app_ry
            tw = widget.winfo_width()
            th = widget.winfo_height()
        except Exception:
            return None
        if tw <= 1 or th <= 1:
            return None
        return (tx, ty, tw, th)

    def _compute_callout_position(self, target_rect, app_x, app_y, app_w, app_h):
        # Retourne (cx, cy) en coords ECRAN (pour positionner le Toplevel).
        cw, ch = CALLOUT_DEFAULT_W, CALLOUT_DEFAULT_H
        if self.callout is not None:
            try:
                self.callout.update_idletasks()
                cw = self.callout.winfo_reqwidth() or cw
                ch = self.callout.winfo_reqheight() or ch
            except Exception:
                pass

        if target_rect is None:
            cx = app_x + max(10, (app_w - cw) // 2)
            cy = app_y + max(10, (app_h - ch) // 2)
            return cx, cy

        tx, ty, tw, th = target_rect
        # Droite > Bas > Haut > Gauche
        candidates = [
            (app_x + tx + tw + 24, app_y + ty),
            (app_x + tx, app_y + ty + th + 24),
            (app_x + tx, app_y + ty - ch - 24),
            (app_x + tx - cw - 24, app_y + ty),
        ]
        for cx, cy in candidates:
            if (app_x + 10 <= cx <= app_x + app_w - cw - 10
                    and app_y + 10 <= cy <= app_y + app_h - ch - 10):
                return cx, cy
        # Aucune position parfaite : clamp dans les bornes app
        cx, cy = candidates[0]
        cx = max(app_x + 10, min(cx, app_x + app_w - cw - 10))
        cy = max(app_y + 10, min(cy, app_y + app_h - ch - 10))
        return cx, cy

    def _draw_arrow(self, cx, cy, cw, ch, target_rect):
        # cx,cy : coords du callout RELATIVES a l'overlay (= a l'app)
        tx, ty, tw, th = target_rect
        tcx = tx + tw // 2
        tcy = ty + th // 2
        callout_cx = cx + cw // 2
        callout_cy = cy + ch // 2

        # Bord du callout le plus proche du target
        if tcx < cx:
            start_x = cx
            start_y = callout_cy
        elif tcx > cx + cw:
            start_x = cx + cw
            start_y = callout_cy
        elif tcy < cy:
            start_x = callout_cx
            start_y = cy
        else:
            start_x = callout_cx
            start_y = cy + ch

        # Bord du target le plus proche du callout
        if start_x < tx:
            end_x = tx - SPOTLIGHT_PAD
        elif start_x > tx + tw:
            end_x = tx + tw + SPOTLIGHT_PAD
        else:
            end_x = tcx
        if start_y < ty:
            end_y = ty - SPOTLIGHT_PAD
        elif start_y > ty + th:
            end_y = ty + th + SPOTLIGHT_PAD
        else:
            end_y = tcy

        self.canvas.create_line(
            start_x, start_y, end_x, end_y,
            fill=ARROW_COLOR, width=ARROW_WIDTH,
            arrow="last", arrowshape=(14, 16, 6),
        )

    # ---------- Helpers ----------

    def _app_geometry(self):
        return (
            self.app.winfo_rootx(),
            self.app.winfo_rooty(),
            self.app.winfo_width(),
            self.app.winfo_height(),
        )

    def _next(self):
        if self.idx >= len(self.steps) - 1:
            self.stop()
            return
        self.idx += 1
        self._show_step()

    def _prev(self):
        if self.idx <= 0:
            return
        self.idx -= 1
        self._show_step()


# -------------------- Resolveurs et liste d'etapes --------------------

def _home_content(app):
    home = app.modules.get("home")
    return home.content if home is not None else None


def _module(app, key):
    return app.modules.get(key)
def get_default_steps():
    """Retourne la liste des étapes par défaut du tutoriel."""
    return [
        # ---------- Section : Généralités ----------
        TutorialStep(
            title="Bienvenue dans Vega Toolbox",
            body=(
                "Vega Toolbox centralise les opérations de support et de "
                "migration VEGA6 : vérifications du poste, gestion du "
                "pare-feu, configuration IP fixe, nettoyage de la racine "
                "Vega, test d'envoi de mail SMTP et catalogue de "
                "téléchargements.\n\n"
                "Cette visite guidée présente chaque module et chaque bouton. "
                "Utilisez les boutons Précédent et Suivant pour naviguer, "
                "ou la touche Échap pour quitter à tout moment."
            ),
            module="home",
            target=None,
        ),
        TutorialStep(
            title="En-tête de l'application",
            body=(
                "Deux boutons en haut à droite de la fenêtre :\n\n"
                "  • Tutoriel — relance cette visite guidée à tout moment.\n"
                "  • Infos — informations sur le logiciel."
            ),
            module="home",
            target=lambda app: getattr(app, "header_actions", None),
        ),
        TutorialStep(
            title="Barre latérale de navigation",
            body=(
                "La sidebar regroupe l'ensemble des modules :\n\n"
                "  • Accueil — tableau de bord du poste.\n"
                "  • Migration — bascule VEGA5 vers VEGA6.\n"
                "  • Impressions Vega — gestion du partage réseau.\n"
                "  • Pare-feu — règles RetailForce et HFSQL.\n"
                "  • IP fixe — configuration des cartes réseau.\n"
                "  • Nettoyage Vega — rangement de la racine.\n"
                "  • Envoi mail — test SMTP avec OAuth2.\n"
                "  • Téléchargement — catalogue d'installeurs.\n\n"
                "Cliquer sur une entrée affiche le module correspondant. "
                "Les raccourcis Ctrl+1 à Ctrl+8 sélectionnent les modules "
                "dans l'ordre d'affichage."
            ),
            module="home",
            target=lambda app: app.sidebar,
        ),
        TutorialStep(
            title="Tiroir journal",
            body=(
                "Le journal trace en direct toutes les actions exécutées "
                "par l'outil : vérifications, installations, erreurs.\n\n"
                "Replié par défaut, il n'affiche que la dernière ligne. "
                "Un clic sur l'en-tête (ou le raccourci Ctrl+L) déplie le "
                "panneau complet et donne accès à l'historique. Le bouton "
                "Effacer remet le journal à zéro."
            ),
            module="home",
            target=lambda app: app.log_drawer,
        ),

        # ---------- Section : Accueil ----------
        TutorialStep(
            title="Racine Vega détectée",
            body=(
                "Vega Toolbox auto-détecte le dossier d'installation Vega "
                "au démarrage (typiquement C:\\Vega ou D:\\Vega). Cette "
                "racine est partagée entre les modules Migration, Nettoyage "
                "et Téléchargement, qui l'utilisent comme cible par "
                "défaut.\n\n"
                "Pour la modifier, ouvrez l'onglet Migration : le nouveau "
                "chemin est automatiquement répercuté dans tous les "
                "modules concernés."
            ),
            module="home",
            target=lambda app: getattr(_home_content(app), "root_panel_frame", None),
        ),
        TutorialStep(
            title="État du poste",
            body=(
                "Synthèse de la configuration du poste, mise à jour à la "
                "demande :\n\n"
                "  • Administrateur — indique si l'outil tourne avec les "
                "droits élevés.\n"
                "  • Pare-feu TCP 7678 — règle nécessaire au service "
                "fiscal RetailForce.\n"
                "  • C:\\ImpressionsVega — partage réseau utilisé par les "
                "impressions Vega.\n"
                "  • Structure vega.dos\\V6 — présence de l'arborescence "
                "VEGA6 sur la racine.\n"
                "  • Service HFSQL — état du service HFSQL Server "
                "(démarré, arrêté ou absent).\n"
                "  • JSON RetailForce — présence du fichier de "
                "configuration RetailForce. Marqué « À réinitialiser » "
                "uniquement s'il contient les données de DÉMO RetailForce "
                "(un vrai client, même en TSE de test, reste « Configuré »).\n\n"
                "Le bouton Tout vérifier en bas à droite déclenche les six "
                "contrôles simultanément et scanne les bases Vega du poste. "
                "Aucun contrôle automatique au démarrage : l'utilisateur "
                "garde la main.\n\n"
                "Le bouton Bases, à gauche de Tout vérifier, est grisé tant "
                "qu'aucun scan n'a eu lieu. Une fois Tout vérifier lancé, il "
                "se dégrise et permet de choisir la base Vega ciblée pour "
                "l'ensemble des vérifications (la sélection est répercutée "
                "dans tout l'outil)."
            ),
            module="home",
            target=lambda app: getattr(_home_content(app), "status_panel_frame", None),
        ),
        TutorialStep(
            title="Démarrage rapide",
            body=(
                "Quatre raccourcis directs vers les modules les plus "
                "utilisés :\n\n"
                "  • Migration Vega — ouvre l'onglet Migration.\n"
                "  • Téléchargement — ouvre le catalogue d'installeurs.\n"
                "  • Nettoyage Vega — ouvre l'analyse de la racine Vega.\n"
                "  • IP fixe — ouvre la configuration réseau.\n\n"
                "Ces boutons doublent les entrées de la sidebar : ils "
                "améliorent simplement leur visibilité sur l'écran "
                "d'accueil."
            ),
            module="home",
            target=lambda app: getattr(_home_content(app), "shortcuts_panel_frame", None),
        ),
        TutorialStep(
            title="Outils complémentaires",
            body=(
                "Deux installations en un clic pour préparer rapidement "
                "un poste :\n\n"
                "  • Installer Notepad++ + JsonTools — récupère la "
                "dernière version de Notepad++ via l'API GitHub, "
                "l'installe en mode silencieux, puis ajoute le plugin "
                "JsonTools.\n"
                "  • Installer RetailForce — télécharge la version "
                "cible, désinstalle la version présente le cas échéant, "
                "supprime les dossiers C:\\Program Files\\RetailForce et "
                "C:\\ProgramData\\RetailForce, puis lance l'installation "
                "de la nouvelle version.\n"
                "  • Mettre à jour RetailForce — met à jour les composants "
                "du service fiscal en conservant la configuration existante "
                "(après confirmation), sans repartir d'une installation "
                "complète.\n\n"
                "Ces opérations nécessitent les droits administrateur. Le "
                "statut de l'installation s'affiche sous chaque bouton et "
                "reflète l'avancement en temps réel."
            ),
            module="home",
            target=lambda app: getattr(_home_content(app), "tools_panel_frame", None),
        ),

        # ---------- Section : Module Migration ----------
        TutorialStep(
            title="Module Migration — sélection du dossier cible",
            body=(
                "Le module Migration assure le passage d'une installation "
                "VEGA5 vers VEGA6.\n\n"
                "Première zone, en haut de l'écran :\n\n"
                "  • Racine Vega — chemin du dossier d'installation. "
                "Modifiable manuellement ou via Parcourir.\n"
                "  • Bases ▾ — menu déroulant listant toutes les bases "
                "Vega détectées sur les disques du poste ; un clic "
                "sélectionne directement la bonne base.\n"
                "  • Parcourir — ouvre une boîte de dialogue pour "
                "sélectionner un autre dossier.\n"
                "  • Vérifier — relance les contrôles sur la racine "
                "courante (structure et pare-feu).\n\n"
                "Les étapes suivantes détaillent les actions disponibles "
                "plus bas dans le module."
            ),
            module="migration",
            target=lambda app: getattr(_module(app, "migration"), "target_frame", None),
        ),
        TutorialStep(
            title="Migration — boutons d'action",
            body=(
                "Trois boutons d'action :\n\n"
                "  • Lancer la migration — reste grisé tant que la "
                "structure vega.dos\\V6 n'est pas détectée, que le port "
                "7678 n'est pas ouvert et que les deux prérequis "
                "(JSON RetailForce et service HFSQL) ne sont pas validés. "
                "Une fois actif, le bouton crée une sauvegarde de l'état "
                "courant puis bascule l'installation en V6.\n\n"
                "    Si une anomalie survient pendant la migration — un "
                "fichier verrouillé par un autre programme, ou un élément "
                "attendu manquant (ex. un dossier du paquet V6) — une "
                "fenêtre s'ouvre et propose : continuer en ignorant "
                "l'élément (avec une case « ignorer aussi les suivants »), "
                "ou tout annuler, ce qui déclenche un retour arrière "
                "complet de la migration.\n"
                "  • Actualiser les sauvegardes — recharge la liste des "
                "sauvegardes existantes, utile en cas de manipulation "
                "manuelle dans l'explorateur Windows.\n"
                "  • Retour arrière sélectionné — restaure la sauvegarde "
                "sélectionnée dans la liste à droite, en cas de besoin "
                "de revenir à l'état antérieur."
            ),
            module="migration",
            target=lambda app: getattr(_module(app, "migration"), "run_button", None),
        ),
        TutorialStep(
            title="Migration — pilotage du service HFSQL",
            body=(
                "Le bouton à droite de la case « Le service HFSQL est "
                "arrêté » est contextuel :\n\n"
                "  • Lorsque le service est démarré, le bouton est "
                "libellé Arrêter et permet de le stopper.\n"
                "  • Lorsque le service est arrêté, le bouton est "
                "libellé Relancer et permet de le redémarrer.\n\n"
                "La migration ne peut être lancée que lorsque le service "
                "est arrêté, afin d'éviter tout verrou sur les fichiers "
                "HFSQL pendant la copie."
            ),
            module="migration",
            target=lambda app: getattr(_module(app, "migration"), "hfsql_button", None),
        ),

        # ---------- Section : Impressions ----------
        TutorialStep(
            title="Module Impressions Vega",
            body=(
                "Crée et partage le dossier C:\\ImpressionsVega utilisé "
                "par les impressions réseau de Vega.\n\n"
                "  • Vérifier — contrôle l'existence du dossier, du "
                "partage ImpressionsVega$ et des droits NTFS associés.\n"
                "  • Créer / réparer — crée les éléments manquants et "
                "applique l'autorisation « Tout le monde — contrôle "
                "total », à la fois sur le partage et sur les permissions "
                "NTFS.\n\n"
                "Le panneau de droite résume l'état global : Conforme "
                "ou À corriger."
            ),
            module="impressions",
            target=lambda app: getattr(_module(app, "impressions"), "toolbar", None),
        ),

        # ---------- Section : Pare-feu ----------
        TutorialStep(
            title="Module Pare-feu",
            body=(
                "Gère les deux règles de pare-feu Windows nécessaires au "
                "fonctionnement de Vega.\n\n"
                "Deux boutons en haut :\n\n"
                "  • Vérifier — lit l'état des règles directement dans "
                "le registre Windows. Rapide et indépendant de la langue "
                "de l'OS.\n"
                "  • Créer / réparer — applique les règles manquantes "
                "via netsh advfirewall.\n\n"
                "Deux règles surveillées :\n\n"
                "  • Service Fiscal RetailForce — TCP 7678, sur les trois "
                "profils Domaine, Privé et Public.\n"
                "  • HFSQL Server — TCP 4900, sur les trois mêmes "
                "profils."
            ),
            module="firewall",
            target=lambda app: getattr(_module(app, "firewall"), "toolbar", None),
        ),

        # ---------- Section : IP fixe ----------
        TutorialStep(
            title="Module IP fixe",
            body=(
                "Bascule une carte réseau de DHCP vers IP fixe, en "
                "conservant l'adresse actuellement attribuée. Utile sur "
                "les bornes de caisse Vega, qui doivent disposer d'une IP "
                "stable.\n\n"
                "  • Sélecteur Carte réseau — liste les interfaces "
                "détectées sur le poste.\n"
                "  • Actualiser les cartes — relit la liste si une "
                "interface vient d'être branchée ou activée.\n"
                "  • Lire la configuration — récupère l'IP, le masque, "
                "la passerelle et les DNS de la carte sélectionnée.\n"
                "  • Appliquer en IP fixe — fige la configuration "
                "actuelle (IP, masque, passerelle, DNS) directement sur "
                "la carte.\n"
                "  • Restaurer DHCP — repasse la carte en attribution "
                "automatique."
            ),
            module="fixed_ip",
            target=lambda app: getattr(_module(app, "fixed_ip"), "selector", None),
        ),

        # ---------- Section : Nettoyage ----------
        TutorialStep(
            title="Module Nettoyage Vega",
            body=(
                "Range les fichiers obsolètes de la racine Vega dans des "
                "sous-dossiers dédiés à l'intérieur de vega.dos. Aucune "
                "suppression définitive : tout est déplacé.\n\n"
                "  • Bases ▾ — menu déroulant des bases Vega détectées "
                "sur le poste (comme dans Migration), pour cibler "
                "directement la bonne base.\n"
                "  • Parcourir — sélectionne la racine Vega à nettoyer "
                "(héritée par défaut de l'onglet Migration).\n"
                "  • Analyser — parcourt la racine et liste les actions "
                "prévues par catégorie : anciens .exe, anciennes .dll, "
                "PDF, documents Word, fichiers Excel, archives, copies, "
                "images et résultats FDJ.\n"
                "  • Lancer le nettoyage — grisé tant que l'analyse n'a "
                "pas été exécutée. Effectue les déplacements et "
                "génère un fichier journal dans vega.dos.\n\n"
                "Le module n'agit que sur les fichiers situés à la "
                "racine : aucun sous-dossier métier n'est parcouru."
            ),
            module="clean",
            target=lambda app: getattr(_module(app, "clean"), "target_frame", None),
        ),

        # ---------- Section : SMTP ----------
        TutorialStep(
            title="Module Envoi mail",
            body=(
                "Test SMTP : envoie un mail de test depuis le poste pour "
                "valider la configuration serveur de Vega.\n\n"
                "  • Mail expéditeur, Compte, Mot de passe, Serveur SMTP "
                "— paramètres de connexion au serveur de messagerie.\n"
                "  • Sécurité — Rien (port 25), SSL (port 465) ou TLS "
                "(port 587). Le port se met à jour automatiquement selon "
                "le mode sélectionné.\n"
                "  • Destinataire, Sujet, Corps — contenu du mail de "
                "test.\n"
                "  • Tester — tente d'abord une authentification "
                "basique ; en cas de refus lié à la MFA Microsoft 365, "
                "l'outil bascule automatiquement sur OAuth2 et ouvre une "
                "popup de connexion.\n"
                "  • Réinitialiser — efface l'ensemble des champs.\n\n"
                "Le journal en bas affiche le diagnostic ligne par ligne : "
                "handshake TLS, AUTH, MAIL FROM, RCPT TO, DATA."
            ),
            module="smtp_test",
            target=lambda app: getattr(_module(app, "smtp_test"), "cfg_frame", None),
        ),

        # ---------- Section : Téléchargement ----------
        TutorialStep(
            title="Module Téléchargement",
            body=(
                "Catalogue d'installeurs régulièrement nécessaires sur "
                "les postes de support.\n\n"
                "  • Dossier cible + Parcourir — emplacement où "
                "enregistrer les fichiers téléchargés.\n"
                "  • Catalogue (à gauche) — cocher les éléments "
                "souhaités. Un clic sur une ligne affiche son détail "
                "dans le panneau de droite.\n"
                "  • Télécharger — lance les téléchargements cochés en "
                "parallèle, avec barre de progression, débit et temps "
                "restant estimé.\n\n"
                "Catalogue intégré : HFSQL Client/Serveur (versions 28 "
                "et 31), Visual C++ redistribuables (2010, v14 x86, "
                "v14 x64), modèles de mail Syspay, builds VEGA4, VEGA5 "
                "et VEGA6 PROD. Si l'option Auto-extract est cochée, les "
                "archives ZIP VEGA6 sont automatiquement décompressées "
                "dans la racine Vega."
            ),
            module="download",
            target=lambda app: getattr(_module(app, "download"), "target_frame", None),
        ),

        # ---------- Info PC : présentation générale ----------
        TutorialStep(
            title="Info PC — vue d'ensemble",
            body=(
                "Cet onglet affiche un instantané matériel et logiciel du "
                "poste à la façon CPU-Z ou Gestionnaire des tâches.\n\n"
                "Il se compose de trois zones :\n"
                "  • un bandeau hero avec les indicateurs temps réel ;\n"
                "  • une grille de cartes d'identité matérielle ;\n"
                "  • un panneau réseau et un panneau contexte Vega."
            ),
            module="pc_info",
            target=None,
        ),

        # ---------- Info PC : hero monitoring ----------
        TutorialStep(
            title="Info PC — moniteur temps réel",
            body=(
                "Le hero affiche en continu (rafraîchi 2 fois par seconde) :\n\n"
                "  • le pourcentage d'utilisation du processeur, avec un "
                "mini-graphique d'historique sur 30 secondes ;\n"
                "  • le pourcentage et la valeur en Go de la mémoire RAM "
                "utilisée ;\n"
                "  • le pourcentage et l'espace libre du disque système.\n\n"
                "Les gauges changent de couleur quand on franchit "
                "75 % (orange) ou 90 % (rouge)."
            ),
            module="pc_info",
            target=lambda app: getattr(_module(app, "pc_info"), "cpu_gauge", None),
        ),

        # ---------- Info PC : bouton compatibilité ----------
        TutorialStep(
            title="Info PC — vérification de compatibilité Vega",
            body=(
                "Ce bouton confronte la configuration du poste aux "
                "prérequis matériels Vega (processeur, RAM, OS, disque).\n\n"
                "Une popup demande d'abord le profil : Serveur (4+ "
                "postes clients) ou Poste de travail (mono-poste / "
                "client). L'OS détecté préselectionne le bon profil.\n\n"
                "Le tableau résultat colorise chaque critère : conforme, "
                "acceptable (au minimum), ou non conforme. Une icône ⓘ "
                "au survol affiche les détails techniques d'un critère."
            ),
            module="pc_info",
            target=lambda app: getattr(_module(app, "pc_info"), "_compat_btn", None),
        ),

        # ---------- Info PC : export rapport ----------
        TutorialStep(
            title="Info PC — export du rapport",
            body=(
                "Génère un fichier HTML autonome à côté de l'exécutable "
                "(rapport_info_pc_AAAAMMJJ_hhmmss.html), automatiquement "
                "ouvert dans le navigateur par défaut.\n\n"
                "Le rapport contient TOUTES les informations PC affichées "
                "ici : système, OS, processeur, RAM, carte mère, BIOS, "
                "GPU, disques, adaptateurs réseau, contexte Vega.\n\n"
                "Idéal pour archivage, transmission au support, ou "
                "impression PDF via Ctrl+P du navigateur."
            ),
            module="pc_info",
            target=lambda app: getattr(_module(app, "pc_info"), "_export_btn", None),
        ),

        # ---------- HFSQL Serveur : présentation ----------
        TutorialStep(
            title="HFSQL Serveur — présentation",
            body=(
                "Cet onglet pilote l'installation, la désinstallation et "
                "la réinstallation du moteur HFSQL Client/Serveur de "
                "PCSoft, en mode silencieux.\n\n"
                "En haut, le panneau « État de l'installation HFSQL » "
                "indique si le moteur est installé et sa version. Le bouton "
                "Vérifier, à sa droite, effectue un contrôle ciblé de la "
                "présence d'un moteur HFSQL sur le poste (service Windows "
                "Hyper File Server + port en écoute), et signale le cas où "
                "plusieurs instances HFSQL coexistent.\n\n"
                "L'installeur (≈500 Mo) est téléchargé automatiquement "
                "depuis le serveur Zucchetti la première fois, puis "
                "mis en cache dans %TEMP% pour les opérations suivantes."
            ),
            module="hfsql",
            target=None,
        ),

        # ---------- HFSQL : sous-onglet standard ----------
        TutorialStep(
            title="HFSQL — Installation standard",
            body=(
                "Onglet recommandé pour 99 % des cas : tous les "
                "paramètres sont définis automatiquement.\n\n"
                "  • Port : 4900 par défaut (port standard HFSQL).\n"
                "  • Version : WinDev 31 (la plus récente, recommandée).\n"
                "  • Répertoire serveur : VEGAHF ou VEGACS détecté au-"
                "dessus. Le bouton Bases ▾ liste les racines Vega du "
                "poste pour en choisir une ; « Changer… » permet aussi "
                "de sélectionner un dossier libre.\n"
                "  • Centre de Contrôle HFSQL : toujours installé.\n\n"
                "Les boutons Installer et Désinstaller + Réinstaller "
                "s'activent selon l'état détecté en haut."
            ),
            module="hfsql",
            target=lambda app: getattr(_module(app, "hfsql"), "_notebook", None),
        ),

        # ---------- HFSQL : bouton install ----------
        TutorialStep(
            title="HFSQL — Installer HFSQL",
            body=(
                "Lance le pipeline d'installation silencieuse :\n\n"
                "  1. Téléchargement de l'installeur PCSoft (~500 Mo, "
                "cache si déjà téléchargé) ;\n"
                "  2. Génération du fichier .INI avec les paramètres "
                "(port, répertoire, hostname comme nom serveur) ;\n"
                "  3. Lancement de l'installeur en silencieux ;\n"
                "  4. Vérification cascade : service Windows actif + "
                "port 4900 accessible + parsing du log d'install pour "
                "détecter les erreurs.\n\n"
                "Compter 3 à 8 minutes selon le débit réseau et la "
                "vitesse du disque."
            ),
            module="hfsql",
            target=lambda app: getattr(_module(app, "hfsql"), "_install_btn", None),
        ),

        # ---------- HFSQL : bouton reinstall ----------
        TutorialStep(
            title="HFSQL — Désinstaller + Réinstaller",
            body=(
                "À utiliser quand un HFSQL est déjà présent mais "
                "défectueux (mauvais port, install corrompue, "
                "downgrade volontaire…).\n\n"
                "Le pipeline :\n"
                "  1. Arrêt du service ;\n"
                "  2. Désinstallation silencieuse + nettoyage des "
                "DLL résiduelles et de la clé registre ;\n"
                "  3. Installation neuve avec les paramètres saisis.\n\n"
                "Les bases de données dans VEGAHF\\BDD sont CONSERVÉES "
                "(seul le moteur est remplacé). Les connexions clients "
                "en cours seront coupées."
            ),
            module="hfsql",
            target=lambda app: getattr(_module(app, "hfsql"), "_reinstall_btn", None),
        ),

        # ---------- Conclusion ----------
        TutorialStep(
            title="Fin de la visite",
            body=(
                "La visite guidée est terminée. Tous les modules de "
                "Vega Toolbox ont été présentés.\n\n"
                "Raccourcis clavier disponibles :\n\n"
                "  • F1 — ouvre l'écran Infos / support.\n"
                "  • F5 — rafraîchit le module courant.\n"
                "  • F12 — maximise ou restaure la fenêtre.\n"
                "  • Ctrl+L — déplie ou replie le journal.\n"
                "  • Ctrl+1 à Ctrl+9 — navigation directe entre les "
                "principaux modules.\n\n"
                "L'outil se met à jour automatiquement lorsqu'une "
                "nouvelle version est disponible : une fenêtre de "
                "confirmation s'affiche quelques secondes après "
                "l'ouverture."
            ),
            module="home",
            target=None,
        ),
    ]
