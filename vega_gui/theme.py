import subprocess

APP_TITLE = "Vega Toolbox"
APP_VERSION = "5.0.2"
APP_VERSION_LABEL = APP_VERSION
APP_FOOTER = f"{APP_TITLE} version {APP_VERSION_LABEL}"
SUPPORT_EMAIL = "bastien.tillier@zucchetti.com"
SUPPORT_MESSAGE = (
    "Si vous rencontrez un quelconque problème avec cet outil, merci de contacter directement "
    "Bastien TILLIER, David CHALENGEAS ou David VIARD."
)

WINDOW_BG = "#d4d0c8"
PANEL_BG = "#ece9e1"
WHITE_BG = "#ffffff"
ACCENT_BLUE = "#0a246a"
OK_COLOR = "#0b6500"
WARN_COLOR = "#9a6700"
ERROR_COLOR = "#b00000"
MUTED_TEXT = "#4f4f4f"

ICON_FILES = {
    # Toolbar / actions communes (toutes les conserves pour ne rien casser dans les onglets).
    "parcourir": "media/icons/parcourir.png",
    "verifier": "media/icons/verifier.png",
    "lancer": "media/icons/lancer.png",
    "actualiser": "media/icons/actualiser.png",
    "retour": "media/icons/retour.png",
    "reparer": "media/icons/reparer.png",
    "impression": "media/icons/impression.png",
    "journal": "media/icons/journal.png",
    "info": "media/icons/info.png",
    "notepad": "media/icons/notepad.png",
    # Sidebar : un icone distinct par module pour eviter la repetition visuelle.
    "house": "media/icons/house.png",
    "arrow_switch": "media/icons/arrow_switch.png",
    "shield": "media/icons/shield.png",
    "computer": "media/icons/computer.png",
    "bin": "media/icons/bin.png",
    "email_go": "media/icons/email_go.png",
    "arrow_down": "media/icons/arrow_down.png",
    "money_euro": "media/icons/money_euro.png",
    "hfsql": "media/icons/hfsql.png",
    "database": "media/icons/database.png",
}

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
