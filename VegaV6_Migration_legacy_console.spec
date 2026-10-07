# -*- mode: python ; coding: utf-8 -*-
"""Recette PyInstaller du build LEGACY (Windows 7 SP1 / Server 2008 R2 -> Server 2012).

A construire avec Python 3.8 (derniere version supportant ces OS) :
    .venv-legacy-x64\\Scripts\\python.exe -m PyInstaller VegaV6_Migration_legacy.spec --noconfirm --clean
    .venv-legacy-x86\\Scripts\\python.exe -m PyInstaller VegaV6_Migration_legacy.spec --noconfirm --clean

Le nom de sortie depend de l'architecture de l'interpreteur qui execute
PyInstaller : vega_toolbox_legacy_x64.exe ou vega_toolbox_legacy_x86.exe.
C'est ce nom que l'updater utilise pour ne jamais croiser les canaux
(cf. vega_backend/updater.py : EXE_ASSET_NAMES).

Difference avec la recette moderne : pas de 'truststore' (requiert Python 3.10+).
Sans lui, ssl retombe sur le magasin de certificats Windows, ce qui reste
correct sur ces OS.
"""
import os
import struct

_ARCH = "x64" if struct.calcsize("P") * 8 == 64 else "x86"
_EXE_NAME = "vega_toolbox_legacy_" + _ARCH + "_console"

# --- Universal C Runtime embarque (deploiement "app-local") ---
# python38.dll depend de l'UCRT, present nativement a partir de Windows 10
# seulement. Sur Windows 7 / 8 / Server 2008 R2-2012 il faut normalement le
# correctif KB2999226, sinon l'executable ne demarre pas du tout :
#     "api-ms-win-crt-runtime-l1-1-0.dll is missing"
# Microsoft autorise le deploiement local de l'UCRT : on embarque donc les
# 15 DLL d'API set + ucrtbase.dll a la racine du bundle, a cote de python38.dll,
# ce qui supprime tout prerequis sur le poste.
# On n'embarque QUE si le jeu est complet (stubs + ucrtbase) : un jeu partiel ou
# depareille casserait aussi les postes qui, eux, ont bien le correctif.
_UCRT_DIR = os.path.join("lib", "ucrt", _ARCH)
_ucrt_binaries = []
if os.path.isfile(os.path.join(_UCRT_DIR, "ucrtbase.dll")):
    _ucrt_binaries = [
        (os.path.join(_UCRT_DIR, _f), ".")
        for _f in sorted(os.listdir(_UCRT_DIR))
        if _f.lower().endswith(".dll")
    ]
    print(f"[spec] UCRT embarque ({_ARCH}) : {len(_ucrt_binaries)} DLL")
else:
    print(f"[spec] UCRT NON embarque ({_ARCH}) : ucrtbase.dll absent de {_UCRT_DIR} "
          f"-> le poste devra disposer du correctif KB2999226")


a = Analysis(
    ['script.py'],
    pathex=[],
    binaries=_ucrt_binaries,
    datas=[
        ('media\\vega_toolbox.ico', 'media'),
        ('media\\teams.png', 'media'),
        ('media\\outlook.png', 'media'),
        ('media\\logo_vega.png', 'media'),
        ('media\\kiwi.png', 'media'),
        ('media\\Logo_Zucchetti.png', 'media'),
        ('media\\Logo_Zucchetti.png-small.png', 'media'),
        ('media\\icons\\parcourir.png', 'media\\icons'),
        ('media\\icons\\verifier.png', 'media\\icons'),
        ('media\\icons\\lancer.png', 'media\\icons'),
        ('media\\icons\\actualiser.png', 'media\\icons'),
        ('media\\icons\\retour.png', 'media\\icons'),
        ('media\\icons\\reparer.png', 'media\\icons'),
        ('media\\icons\\impression.png', 'media\\icons'),
        ('media\\icons\\journal.png', 'media\\icons'),
        ('media\\icons\\info.png', 'media\\icons'),
        ('media\\icons\\notepad.png', 'media\\icons'),
        ('media\\icons\\house.png', 'media\\icons'),
        ('media\\icons\\arrow_switch.png', 'media\\icons'),
        ('media\\icons\\shield.png', 'media\\icons'),
        ('media\\icons\\computer.png', 'media\\icons'),
        ('media\\icons\\bin.png', 'media\\icons'),
        ('media\\icons\\email_go.png', 'media\\icons'),
        ('media\\icons\\arrow_down.png', 'media\\icons'),
        ('media\\icons\\money_euro.png', 'media\\icons'),
        ('media\\icons\\hfsql.png', 'media\\icons'),
        ('media\\icons\\database.png', 'media\\icons'),
        ('media\\icons\\LICENSE_FAMFAMFAM_SILK.md', 'media\\icons'),
        ('lib\\CryptoTools.dll', 'lib'),
        ('lib\\Newtonsoft.Json.dll', 'lib'),
    ],
    # pythonnet 2.5.2 (et non 3.x) : sa Python.Runtime.dll cible .NET Framework
    # 4.0 au lieu de netstandard2.0, ce qui abaisse le prerequis de l'ecran de
    # connexion de .NET 4.6.1+ a .NET 4.0 — disponible sur tous les Windows
    # vises. 'clr_loader' n'existe qu'en pythonnet 3.x : on ne le declare pas.
    hiddenimports=['msal', 'msal.application', 'msal.token_cache', 'tkinter', 'tkinter.ttk', 'tkinter.filedialog', 'tkinter.messagebox', 'httpx', 'httpcore', 'h11', 'anyio', 'sniffio', 'idna', 'certifi', 'clr', 'yaml'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    # NB : pas de parametre 'optimize' ici — il n'existe qu'a partir de
    # PyInstaller 6.x, or le build legacy utilise PyInstaller 5.13.2, derniere
    # version compatible Python 3.7. La valeur par defaut est equivalente.
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=_EXE_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    icon='media\\vega_toolbox.ico',
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=False,
    version='version_info.txt',
)
