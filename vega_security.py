"""Persistance de la configuration utilisateur de Vega Toolbox.

Stocke les preferences locales dans %APPDATA%\\VegaToolbox\\vega_tool_config.json.

Migration automatique : si une ancienne config existe a cote de l'.exe (ou des
sources), elle est copiee une fois vers le nouvel emplacement et conservee la-bas.

Cles supportees :
  - last_vega_root      : dernier dossier Vega utilise (partage entre modules)
  - window_geometry     : x/y/w/h/zoomed de la fenetre
  - screen_mode         : "small" / "large" / None (override manuel)
  - skip_login_once     : flag d'usage unique pour les redemarrages internes

Login : le mot de passe doit commencer et finir par '!' et faire au minimum
4 caracteres (ex : !xx!, !1234!, !moncode!). Aucun secret stocke cote Toolbox.
"""
import json
import os
import shutil
import sys
from pathlib import Path

CONFIG_NAME = "vega_tool_config.json"


def crypto_init_error():
    # Conserve pour compatibilite UI — toujours None avec la nouvelle logique.
    return None


def _appdata_dir():
    appdata = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
    if appdata:
        return Path(appdata) / "VegaToolbox"
    # Fallback (Windows sans APPDATA defini, ou autre OS) : a cote de l'exe / sources.
    return _legacy_dir()


def _legacy_dir():
    # Ancien emplacement : a cote de l'exe une fois compile, sinon a cote des sources.
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def app_dir():
    target = _appdata_dir()
    try:
        target.mkdir(parents=True, exist_ok=True)
    except Exception:
        # Si la creation echoue, on retombe sur le legacy directory.
        return _legacy_dir()
    # Migration one-shot depuis l'ancien emplacement.
    legacy_path = _legacy_dir() / CONFIG_NAME
    new_path = target / CONFIG_NAME
    if legacy_path.exists() and not new_path.exists() and legacy_path.resolve() != new_path.resolve():
        try:
            shutil.copy2(legacy_path, new_path)
        except Exception:
            pass
    return target


def config_path():
    return app_dir() / CONFIG_NAME


def is_valid_password_format(password):
    # Le mot de passe doit commencer et finir par '!' et faire >= 4 caracteres.
    # Utilise pour l'auto-submit cote UI (on soumet des que le format est valide).
    return (
        isinstance(password, str)
        and len(password) >= 4
        and password.startswith("!")
        and password.endswith("!")
    )


def verify_password(password):
    ok, _diag = verify_password_with_diag(password)
    return ok


def verify_password_with_diag(password):
    diag = {
        "input_length": len(password) if isinstance(password, str) else 0,
        "dll_loaded": False,
        "raw_result": None,
        "error": None,
        "init_error": None,
    }
    ok = is_valid_password_format(password)
    diag["raw_result"] = "1" if ok else "0"
    return ok, diag


def load_config():
    path = config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save_config(patch):
    # Merge incremental : conserve les cles existantes en dehors du patch.
    path = config_path()
    data = load_config()
    if isinstance(patch, dict):
        data.update(patch)
    try:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def load_last_vega_root():
    value = load_config().get("last_vega_root")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def save_last_vega_root(root_path):
    save_config({"last_vega_root": str(root_path)})


def load_screen_mode_override():
    # Override manuel du mode petit / grand ecran. None = auto.
    value = load_config().get("screen_mode")
    if value in ("small", "large"):
        return value
    return None


def save_screen_mode_override(value):
    save_config({"screen_mode": value if value in ("small", "large") else None})


def consume_skip_login_once():
    # Drapeau usage unique : pose par le toggle ecran (ou autres redemarrages
    # internes), consomme au prochain demarrage pour eviter de redemander le code.
    data = load_config()
    if data.get("skip_login_once"):
        save_config({"skip_login_once": False})
        return True
    return False


def set_skip_login_once():
    save_config({"skip_login_once": True})


def load_snake_highscore():
    # Easter egg Snake (vue Infos) : meilleur score local + nom du detenteur.
    raw = load_config().get("snake_highscore")
    if not isinstance(raw, dict):
        return 0, ""
    try:
        score = int(raw.get("score", 0))
    except (TypeError, ValueError):
        return 0, ""
    holder = raw.get("holder")
    return max(score, 0), holder if isinstance(holder, str) else ""


def save_snake_highscore(score, holder):
    save_config({"snake_highscore": {"score": int(score), "holder": str(holder)}})


def load_window_geometry():
    # Renvoie {x, y, w, h, zoomed} ou None si rien de stocke / format invalide.
    raw = load_config().get("window_geometry")
    if not isinstance(raw, dict):
        return None
    try:
        geom = {
            "x": int(raw.get("x", 0)),
            "y": int(raw.get("y", 0)),
            "w": int(raw.get("w", 0)),
            "h": int(raw.get("h", 0)),
            "zoomed": bool(raw.get("zoomed", False)),
        }
    except (TypeError, ValueError):
        return None
    if geom["w"] <= 100 or geom["h"] <= 100:
        return None
    return geom


def save_window_geometry(x, y, w, h, zoomed=False):
    save_config({
        "window_geometry": {
            "x": int(x),
            "y": int(y),
            "w": int(w),
            "h": int(h),
            "zoomed": bool(zoomed),
        },
    })
