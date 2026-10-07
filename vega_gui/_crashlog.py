"""Journal de crash : toute erreur non rattrapee est ecrite sur disque.

Sans cela, un plantage du build fenetre (console=False) ne laisse AUCUNE trace :
l'application disparait et il faut recompiler une variante console pour voir la
moindre pile d'appels. Ici, deux filets :

  - sys.excepthook          : erreurs du thread principal hors boucle Tk
    (typiquement l'initialisation, avant que la fenetre existe) ;
  - Tk.report_callback_exception : erreurs levees DANS un callback Tk, qui ne
    remontent jamais a sys.excepthook — c'est le cas le plus frequent une fois
    l'interface lancee.

Le fichier est ecrit a cote de l'executable s'il est accessible en ecriture,
sinon dans %TEMP% (cas d'un exe pose dans C:\\Program Files).
"""
import datetime
import os
import sys
import traceback
from pathlib import Path

CRASH_FILE_NAME = "vega_toolbox_crash.log"


def crash_log_path():
    """Emplacement du journal : a cote de l'exe, sinon %TEMP%."""
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent)
    else:
        candidates.append(Path(__file__).resolve().parent.parent)
    temp = os.environ.get("TEMP") or os.environ.get("TMP")
    if temp:
        candidates.append(Path(temp))
    for directory in candidates:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            probe = directory / ".vega_write_test"
            probe.touch()
            probe.unlink()
            return directory / CRASH_FILE_NAME
        except Exception:
            continue
    return Path(CRASH_FILE_NAME)


def write_crash(exc_type, exc_value, exc_tb, context=""):
    """Ajoute une entree horodatee au journal. Retourne le chemin ou None."""
    try:
        path = crash_log_path()
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        details = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        header = (
            f"\n{'=' * 78}\n"
            f"[{stamp}] {context or 'Erreur non rattrapee'}\n"
            f"  version Python : {sys.version.split()[0]}\n"
            f"  executable     : {sys.executable}\n"
            f"{'=' * 78}\n"
        )
        with open(path, "a", encoding="utf-8", errors="replace") as handle:
            handle.write(header + details)
        return path
    except Exception:
        return None


def _notify(path, exc_value):
    # Message a l'utilisateur : sans console, c'est le seul retour visible.
    try:
        from tkinter import messagebox
        where = f"\n\nDétails enregistrés dans :\n{path}" if path else ""
        messagebox.showerror(
            "Vega Toolbox — erreur inattendue",
            f"Une erreur inattendue est survenue :\n\n{exc_value}{where}",
        )
    except Exception:
        pass


def install():
    """Installe le filet global (a appeler avant de creer la fenetre)."""
    previous = sys.excepthook

    def _hook(exc_type, exc_value, exc_tb):
        path = write_crash(exc_type, exc_value, exc_tb, "Erreur non rattrapee (hors Tk)")
        _notify(path, exc_value)
        try:
            previous(exc_type, exc_value, exc_tb)
        except Exception:
            pass

    sys.excepthook = _hook


def install_tk(widget):
    """Redirige les exceptions des callbacks Tk vers le journal.

    Sans cela, Tk se contente d'afficher la pile sur stderr — invisible pour un
    build fenetre — et l'application peut rester dans un etat incoherent.
    """
    def _report(_exc, value, tb, _widget=widget):
        path = write_crash(type(value), value, tb, "Erreur dans un callback Tk")
        _notify(path, value)

    try:
        widget.report_callback_exception = _report
    except Exception:
        pass
