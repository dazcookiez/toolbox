import sys
import tkinter as tk
from pathlib import Path

try:
    from PIL import Image, ImageTk
except Exception:
    Image = None
    ImageTk = None


def resource_path(relative_path):
    # Résout les ressources de la même façon en source et en mode compilé.
    base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base_path / relative_path


def load_image(relative_path):
    path = resource_path(relative_path)
    if not path.exists():
        return None
    try:
        return tk.PhotoImage(file=str(path))
    except Exception:
        return None


def set_window_icon(window):
    # Icone unique, multi-tailles, identite Vega Toolbox.
    icon_path = resource_path("media/vega_toolbox.ico")
    if not icon_path.exists():
        return
    try:
        window.iconbitmap(str(icon_path))
    except Exception:
        pass


def load_scaled_logo(relative_path, target_height):
    # Redimensionne les logos au chargement pour garder un rendu propre.
    path = resource_path(relative_path)
    if not path.exists():
        return None

    target_height = max(1, int(target_height))
    if Image is None or ImageTk is None:
        return load_image(relative_path)
    try:
        with Image.open(path) as source:
            width, height = source.size
            if height <= 0:
                return None
            scale = target_height / height
            target_width = max(1, round(width * scale))
            resampling = getattr(Image, "Resampling", Image).LANCZOS
            image = source.convert("RGBA").resize((target_width, target_height), resampling)
        return ImageTk.PhotoImage(image)
    except Exception:
        return load_image(relative_path)
