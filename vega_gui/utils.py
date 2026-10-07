import ctypes
import os
import shutil
import subprocess
from pathlib import Path

from .theme import CREATE_NO_WINDOW


def enable_dpi_awareness():
    # Active le mode DPI-aware Per-Monitor-V2 : Windows ne scale plus les bitmaps,
    # on recoit le DPI reel et c'est Tk qui gere la mise a l'echelle.
    try:
        # -4 = PROCESS_PER_MONITOR_DPI_AWARE_V2 (Win10 1703+), fallback 1 = System-aware.
        ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)
        return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-Monitor
        return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # System-aware
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def get_system_dpi():
    # Retourne le DPI effectif du systeme. 96 = 100%, 120 = 125%, 144 = 150%, 192 = 200%.
    try:
        return int(ctypes.windll.user32.GetDpiForSystem())
    except Exception:
        pass
    try:
        hdc = ctypes.windll.user32.GetDC(0)
        dpi = int(ctypes.windll.gdi32.GetDeviceCaps(hdc, 88))  # LOGPIXELSX
        ctypes.windll.user32.ReleaseDC(0, hdc)
        if dpi > 0:
            return dpi
    except Exception:
        pass
    return 96


def dpi_scale(value, dpi=None):
    # Convertit une valeur en "pixels a 96 DPI" vers des pixels reels au DPI courant.
    if dpi is None:
        dpi = get_system_dpi()
    return int(round(value * dpi / 96.0))


def apply_adaptive_scaling(window):
    # Tk scaling = points-par-pixel. Defaut 96 DPI = 1.333.
    # On suit le DPI mais avec un facteur attenue (50%) pour ne pas que les boutons/polices
    # paraissent enormes a 125/150% : Windows scale deja la chrome (titlebar etc.) de son cote.
    dpi = get_system_dpi()
    base = 1.333
    factor = base * (1.0 + ((dpi - 96) / 96.0) * 0.5)
    try:
        window.tk.call("tk", "scaling", factor)
    except Exception:
        pass
    return dpi


def is_small_screen(window):
    # La notion de "petit ecran" depend de la surface utile, pas juste de la resolution :
    # a 150% DPI, un 1920x1080 devient visuellement ~1280x720 => on convertit en pixels-96.
    dpi = get_system_dpi()
    effective_w = window.winfo_screenwidth() * 96 / dpi
    effective_h = window.winfo_screenheight() * 96 / dpi
    return effective_h < 720 or effective_w < 1280


def fit_window_to_screen(window, preferred_width, preferred_height, min_width, min_height, zoom_if_small=False):
    # Les dimensions passees sont en pixels reels. Avec Tk scaling attenue, pas besoin
    # de tout scaler par DPI : Tk gonfle deja les polices, gonfler aussi la fenetre = double scaling.
    screen_w = window.winfo_screenwidth()
    screen_h = window.winfo_screenheight()
    width = min(preferred_width, max(420, screen_w - 70))
    height = min(preferred_height, max(520, screen_h - 90))
    min_width = min(min_width, width)
    min_height = min(min_height, height)
    pos_x = max((screen_w - width) // 2, 0)
    pos_y = max((screen_h - height) // 2, 0)
    window.geometry(f"{width}x{height}+{pos_x}+{pos_y}")
    window.minsize(min_width, min_height)

    if zoom_if_small and (screen_w < preferred_width + 40 or screen_h < preferred_height + 40):
        try:
            window.state("zoomed")
        except Exception:
            pass


def open_teams_application():
    try:
        os.startfile("msteams:")
        return True, "Microsoft Teams ouvert."
    except Exception:
        pass

    try:
        os.startfile("https://teams.microsoft.com")
        return True, "Microsoft Teams Web ouvert."
    except Exception as exc:
        return False, f"Impossible d'ouvrir Microsoft Teams : {exc}"


def find_outlook_executable():
    direct = shutil.which("outlook.exe")
    if direct:
        return direct

    roots = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
    ]
    patterns = [
        "Microsoft Office\\root\\Office*\\OUTLOOK.EXE",
        "Microsoft Office\\Office*\\OUTLOOK.EXE",
    ]
    for root in roots:
        if not root.exists():
            continue
        for pattern in patterns:
            matches = sorted(root.glob(pattern), reverse=True)
            if matches:
                return str(matches[0])
    return None


def open_outlook_mail(recipient):
    outlook = find_outlook_executable()
    if outlook:
        try:
            subprocess.Popen(
                [outlook, "/c", "ipm.note", "/m", recipient],
                creationflags=CREATE_NO_WINDOW,
            )
            return True, "Outlook ouvert."
        except Exception:
            pass

    try:
        os.startfile(f"mailto:{recipient}")
        return True, "Client de messagerie ouvert."
    except Exception as exc:
        return False, f"Impossible d'ouvrir la messagerie : {exc}"


def format_bytes(byte_count):
    if not byte_count:
        return "0 o"
    value = float(byte_count)
    units = ["o", "Ko", "Mo", "Go", "To"]
    unit_index = 0
    while value >= 1024 and unit_index < len(units) - 1:
        value /= 1024
        unit_index += 1
    if unit_index == 0:
        return f"{int(value)} {units[unit_index]}"
    return f"{value:.1f} {units[unit_index]}"


def format_speed(byte_count_per_second):
    return f"{format_bytes(byte_count_per_second)}/s"


def format_eta(seconds):
    if seconds is None:
        return "--:--"
    try:
        seconds = max(0, int(round(seconds)))
    except (OverflowError, ValueError):
        return "--:--"
    # Cap de sanite : au-dela de 99h59m59s, l'ETA n'a plus de sens
    # (vient typiquement d'un debit qui tend vers 0 sans atteindre 0).
    if seconds >= 99 * 3600:
        return "--:--"
    minutes, remaining_seconds = divmod(seconds, 60)
    hours, remaining_minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{remaining_minutes:02d}:{remaining_seconds:02d}"
    return f"{remaining_minutes:02d}:{remaining_seconds:02d}"
