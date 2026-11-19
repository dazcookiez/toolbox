"""Tests 5.0.3 sur Windows, contre une COPIE d'une instance Vega.

Ne jamais pointer ce script sur une vraie base en production : il ecrit des
fichiers (nomserve.txt), cree un partage SMB temporaire et ferme de force les
fichiers ouverts SOUS le dossier donne.

Usage (depuis la racine du repo, PowerShell en administrateur) :
    python tests\\windows_smoke_503.py --vega "D:\\TestVega\\VEGACS" [--smb]

--vega : dossier VegaCS/VegaHF copie (contenant BDD\\<base>), ou une base.
--smb  : teste aussi la fermeture forcee via un partage temporaire
         (\\\\localhost\\VegaToolboxTest$ sur la copie). Administrateur requis.

Code retour 0 si tout passe. Rien n'est modifie hors du dossier --vega,
sauf le partage temporaire (supprime a la fin) et un APPDATA temporaire.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

RESULTS = []


def check(name, func):
    try:
        detail = func()
        RESULTS.append((name, True, detail or ""))
        print(f"[OK]   {name} {detail or ''}")
    except Exception as exc:  # noqa: BLE001 - on veut tout remonter
        RESULTS.append((name, False, f"{type(exc).__name__}: {exc}"))
        print(f"[FAIL] {name} : {type(exc).__name__}: {exc}")
        traceback.print_exc()


class Logger:
    def info(self, m):
        print("   log:", m)

    warn = error = info


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--vega", required=True)
    parser.add_argument("--smb", action="store_true")
    args = parser.parse_args()

    # Config de la toolbox isolee (vega_security ecrit dans %APPDATA%).
    os.environ["APPDATA"] = tempfile.mkdtemp(prefix="vtb_appdata_")

    from vega_backend import is_admin
    from vega_backend.migration import (
        MigrationManager, install_root_for_base, list_vega_bases, resolve_vega_folder,
    )
    from vega_backend.nomserve import NomserveManager, parse_nomserve
    from vega_backend.open_files import OpenFilesManager
    import vega_security

    vega = Path(args.vega)
    info = resolve_vega_folder(vega)
    if info["kind"] == "install":
        bases = info["bases"]
        if not bases:
            raise SystemExit(f"Aucune base trouvee dans {vega}\\BDD")
        base = Path(bases[0]["path"])
    else:
        base = Path(info["base"])
    print(f"Base testee : {base}  (admin={is_admin()})")

    # --- Choix manuel de la base ---
    def t_resolve():
        out = []
        for p in (vega, base):
            r = resolve_vega_folder(p)
            out.append(f"{p.name}->{r['kind']}({len(r['bases'])})")
        if info["kind"] == "install":
            r = resolve_vega_folder(vega / "BDD")
            assert r["kind"] == "install", r
            names = [b["name"] for b in list_vega_bases(vega)]
            assert base.name in names, names
            assert install_root_for_base(base) == vega, install_root_for_base(base)
        try:
            resolve_vega_folder(Path(tempfile.mkdtemp()))
        except Exception:
            pass
        else:
            raise AssertionError("un dossier vide ne doit pas etre accepte")
        return ", ".join(out)
    check("resolve_vega_folder / list_vega_bases", t_resolve)

    def t_pinned():
        assert vega_security.load_pinned_vega_root() is None
        vega_security.save_pinned_vega_root(str(base))
        assert vega_security.load_pinned_vega_root() == str(base)
        assert vega_security.load_last_vega_root() == str(base)
        vega_security.save_pinned_vega_root(None)
        assert vega_security.load_pinned_vega_root() is None
        return "save/load/reset"
    check("base memorisee (config)", t_pinned)

    def t_validate():
        try:
            v6 = MigrationManager(Logger()).validate_root(base)
            return f"vega.dos\\V6 present ({v6})"
        except Exception as exc:
            return f"structure non V6 (attendu sur une copie V5) : {exc}"
    check("validate_root sur la copie", t_validate)

    # --- Nomserve ---
    ns = NomserveManager(Logger())
    original = None
    ns_path = base / "nomserve.txt"
    if ns_path.is_file():
        original = ns_path.read_bytes()

    def t_ns_read():
        st = ns.read(base)
        sug = st["suggested"]
        assert sug["base"], sug
        assert sug["port"], sug
        return (f"exists={st['exists']} v6={st['is_v6']} v5={bool(st['v5_path'])} "
                f"ips={st['local_ips']} host={sug['host']!r} ({sug['host_source']}) base={sug['base']!r}")
    check("nomserve : lecture + suggestions", t_ns_read)

    def t_ns_write():
        st = ns.read(base)
        sug = st["suggested"]
        host = sug["host"] or "192.168.1.1"
        values = {
            "serveur": f"{host}:{sug['port']}", "base": sug["base"], "utilisateur": "Admin",
            "mdp": "", "rep": f"\\\\{host}\\ImpressionsVega$",
        }
        res = ns.write(base, values)
        data = ns_path.read_bytes()
        assert b"\r\n" in data and b"\n" not in data.replace(b"\r\n", b""), "fins de ligne CRLF attendues"
        parsed = parse_nomserve(data.decode("cp1252"))
        assert parsed[("BDD HFCS", "SERVEUR")] == values["serveur"], parsed
        assert parsed[("BDD HFCS", "BASE")] == values["base"], parsed
        assert parsed[("REPIMPRESSION", "REP")] == values["rep"], parsed
        if original is not None:
            assert res["backup"] and Path(res["backup"]).read_bytes() == original, "sauvegarde attendue"
        st2 = ns.read(base)
        assert st2["is_v6"], "le fichier ecrit doit etre lu comme V6"
        # Re-ecriture : pas de doublons de cles.
        ns.write(base, values)
        text = ns_path.read_bytes().decode("cp1252")
        assert text.upper().count("SERVEUR=") == 1, text
        return f"backup={res['backup']}"
    check("nomserve : ecriture V6 + sauvegarde + relecture", t_ns_write)

    # --- Fichiers ouverts ---
    of = OpenFilesManager(Logger())

    def t_of_list():
        if not is_admin():
            return "ignore (pas administrateur)"
        st = of.list_open(base)
        return f"methode={st['method']} fichiers={len(st['files'])} processus={len(st['processes'])}"
    check("fichiers ouverts : liste", t_of_list)

    if args.smb:
        def t_of_smb():
            if not is_admin():
                raise AssertionError("--smb demande un PowerShell administrateur")
            share = "VegaToolboxTest$"
            subprocess.run(["net", "share", share, "/delete", "/y"], capture_output=True)
            r = subprocess.run(["net", "share", f"{share}={base}", "/grant:Everyone,FULL"],
                               capture_output=True, text=True)
            if r.returncode != 0:
                r = subprocess.run(["net", "share", f"{share}={base}"], capture_output=True, text=True)
            assert r.returncode == 0, r.stdout + r.stderr
            handle = None
            try:
                target = Path(f"\\\\localhost\\{share}\\nomserve.txt")
                handle = open(target, "rb")
                time.sleep(1)
                st = of.list_open(base)
                seen = [f for f in st["files"] if f["path"].lower().endswith("nomserve.txt")]
                assert seen, f"fichier ouvert via le partage non vu : {st['files']}"
                # On ne ferme que ce fichier (pas les processus du poste).
                res = of.close_all({"method": st["method"], "files": seen, "processes": []})
                assert res["closed"] >= 1 and not res["failed"], res
                time.sleep(1)
                st2 = of.list_open(base)
                still = [f for f in st2["files"] if f["path"].lower().endswith("nomserve.txt")]
                assert not still, f"toujours ouvert : {still}"
                return f"methode={st['method']} ferme={res['closed']}"
            finally:
                if handle:
                    try:
                        handle.close()
                    except OSError:
                        pass
                subprocess.run(["net", "share", share, "/delete", "/y"], capture_output=True)
        check("fichiers ouverts : fermeture forcee via partage temporaire", t_of_smb)

    # Restaure le nomserve d'origine de la copie.
    if original is not None:
        ns_path.write_bytes(original)
    elif ns_path.is_file():
        ns_path.unlink()
    shutil.rmtree(os.environ["APPDATA"], ignore_errors=True)

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} OK")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
