"""Publication d'une release GitLab pour Vega Toolbox.

Usage :
  set GITLAB_TOKEN=glpat-xxxxx
  python publish_release.py

Pipeline :
  1. Lit la version depuis vega_gui/theme.py (APP_VERSION).
  2. Verifie que dist/vega_toolbox.exe existe et a une taille raisonnable.
  3. Upload l'exe dans le package registry generique :
     PUT /api/v4/projects/:id/packages/generic/vega-toolbox/:version/vega_toolbox.exe
  4. Cree un tag git "vX.Y.Z" sur le HEAD de master (via l'API GitLab,
     pas en local, pour eviter les pushes git supplementaires).
  5. Cree la release "Vega Toolbox X.Y.Z" attachee au tag, avec le lien
     vers l'exe du package registry comme asset.

Si la release existe deja, le script abort (pour eviter d'ecraser).
"""
import json
import os
import re
import sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError


PROJECT_PATH = "Gryvernn/Vega-Migration-tool"
PROJECT_ID = PROJECT_PATH.replace("/", "%2F")
API_BASE = f"https://gitlab.com/api/v4/projects/{PROJECT_ID}"
PACKAGE_NAME = "vega-toolbox"
THEME_PATH = Path("vega_gui/theme.py")

# Binaires publies. Le nom de l'asset EST le canal de mise a jour : chaque
# saveur ne se met a jour qu'avec le sien (cf. vega_backend/updater.py).
#   - vega_toolbox.exe            : Windows 8.1+ / Server 2012 R2+  (Python 3.12)
#   - vega_toolbox_legacy_x64.exe : Windows 7 SP1 -> Server 2012     (Python 3.8, 64 bits)
#   - vega_toolbox_legacy_x86.exe : idem en 32 bits
# Le build moderne est obligatoire ; les builds legacy sont publies s'ils
# existent, ce qui permet de sortir une release sans les regenerer.
EXE_PATH = Path("dist/vega_toolbox.exe")
LEGACY_EXES = [
    Path("dist/vega_toolbox_legacy_x64.exe"),
    Path("dist/vega_toolbox_legacy_x86.exe"),
]


def die(msg, code=1):
    print(f"[ERREUR] {msg}", file=sys.stderr)
    sys.exit(code)


def read_version():
    if not THEME_PATH.exists():
        die(f"{THEME_PATH} introuvable. Lancer depuis la racine du projet.")
    body = THEME_PATH.read_text(encoding="utf-8")
    m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', body)
    if not m:
        die("APP_VERSION introuvable dans theme.py")
    version = m.group(1).strip()
    if not re.match(r"^\d+\.\d+\.\d+$", version):
        die(f"Version inattendue : '{version}' (format X.Y.Z attendu)")
    return version


def api(method, path, body=None, content_type="application/json", token=None, raw_body=False):
    url = path if path.startswith("http") else f"{API_BASE}/{path.lstrip('/')}"
    req = Request(url, method=method)
    req.add_header("PRIVATE-TOKEN", token)
    if body is not None:
        if not raw_body:
            data = json.dumps(body).encode("utf-8")
            req.add_header("Content-Type", "application/json")
        else:
            data = body
            req.add_header("Content-Type", content_type)
    else:
        data = None
    try:
        with urlopen(req, data=data, timeout=300) as resp:
            payload = resp.read()
            return resp.status, json.loads(payload) if payload else None
    except HTTPError as e:
        try:
            err = json.loads(e.read())
        except Exception:
            err = {"raw": e.reason}
        return e.code, err


def release_exists(tag, token):
    status, _ = api("GET", f"releases/{tag}", token=token)
    return status == 200


def upload_one(path, version, token):
    size = path.stat().st_size
    if size < 1_000_000:
        die(f"{path} fait moins de 1 Mo ({size} octets), suspect.")
    print(f"        {path.name} ({size // 1024} Ko)...")
    url = f"{API_BASE}/packages/generic/{PACKAGE_NAME}/{version}/{path.name}"
    with open(path, "rb") as f:
        data = f.read()
    status, payload = api(
        "PUT", url, body=data,
        content_type="application/octet-stream",
        token=token, raw_body=True,
    )
    if status not in (200, 201):
        die(f"Upload de {path.name} echoue (status {status}) : {payload}")
    print(f"        -> OK ({status})")
    # URL publique de telechargement = meme URL (GET sans auth si repo public)
    return url


def upload_exe(version, token):
    """Televerse le build moderne + les builds legacy presents.

    Retourne [(nom_asset, url)] dans l'ordre de publication.
    """
    if not EXE_PATH.exists():
        die(f"{EXE_PATH} introuvable. Build avec pyinstaller d'abord.")
    print("  [1/3] Upload des binaires -> package registry...")
    assets = [(EXE_PATH.name, upload_one(EXE_PATH, version, token))]
    for legacy in LEGACY_EXES:
        if legacy.exists():
            assets.append((legacy.name, upload_one(legacy, version, token)))
        else:
            print(f"        (absent, ignore) {legacy.name}")
    return assets


def create_release(version, assets, token):
    tag = f"v{version}"
    if release_exists(tag, token):
        die(f"La release {tag} existe deja. Bump la version avant de republier.")
    print(f"  [2/3] Creation du tag + release {tag}...")
    lines = [f"Build automatique de la version {version}.", ""]
    for name, url in assets:
        if "legacy_x64" in name:
            label = "Windows 7 SP1 / Server 2008 R2 -> Server 2012 (64 bits)"
        elif "legacy_x86" in name:
            label = "Windows 7 SP1 / Server 2008 R2 -> Server 2012 (32 bits)"
        else:
            label = "Windows 8.1+ / Server 2012 R2+"
        lines.append(f"- **{name}** — {label} : {url}")
    body = {
        "name": f"Vega Toolbox {version}",
        "tag_name": tag,
        "ref": "master",
        "description": "\n".join(lines),
        "assets": {
            "links": [
                {"name": name, "url": url, "link_type": "package"}
                for name, url in assets
            ]
        },
    }
    status, payload = api("POST", "releases", body=body, token=token)
    if status not in (200, 201):
        die(f"Creation release echouee (status {status}) : {payload}")
    print(f"  -> OK release : {payload.get('_links', {}).get('self', '?')}")


def verify_latest(version, token):
    print(f"  [3/3] Verification /releases/permalink/latest...")
    url = f"{API_BASE}/releases/permalink/latest"
    status, payload = api("GET", url, token=token)
    if status != 200:
        print(f"  [WARN] permalink/latest a renvoye {status} : {payload}")
        return
    latest_tag = payload.get("tag_name", "")
    if latest_tag != f"v{version}":
        print(f"  [WARN] permalink/latest pointe sur {latest_tag} et non v{version}")
    else:
        print(f"  -> OK : {latest_tag} est la latest.")


def main():
    token = os.environ.get("GITLAB_TOKEN")
    if not token:
        die("GITLAB_TOKEN non defini.\n"
            "  Bash : export GITLAB_TOKEN=glpat-...\n"
            "  PowerShell : $env:GITLAB_TOKEN='glpat-...'\n"
            "  CMD : set GITLAB_TOKEN=glpat-...")
    version = read_version()
    print(f"Publication de la release Vega Toolbox v{version}\n")
    assets = upload_exe(version, token)
    create_release(version, assets, token)
    verify_latest(version, token)
    print(f"\n[OK] Release v{version} publiee ({len(assets)} binaire(s)).")
    for name, url in assets:
        print(f"     {name} : {url}")


if __name__ == "__main__":
    main()
