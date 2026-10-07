"""Couche de compatibilite Windows : route vers les commandes modernes ou
leurs equivalents historiques selon la version de l'OS.

Contexte
--------
Vega Toolbox est distribue en deux saveurs :
  - build MODERNE  (Python 3.12) : Windows 8.1+ / Server 2012 R2+
  - build LEGACY   (Python 3.8)  : Windows 7 SP1 / Server 2008 R2 -> Windows 8 /
                                   Server 2012

Les OS <= 6.1 (Windows 7 / Server 2008 R2) embarquent PowerShell 2.0 et ne
possedent AUCUN des modules apparus avec Windows 8 / PowerShell 3.0 :
    CimCmdlets   -> Get-CimInstance
    NetSecurity  -> Get-NetFirewallRule / New-NetFirewallRule
    SmbShare     -> Get-SmbShare / New-SmbShare / Grant-SmbShareAccess
    Storage      -> Get-PhysicalDisk
    NetTCPIP     -> Get-NetTCPConnection
    Defender     -> Add-MpPreference

Ce module expose des predicats et des helpers pour choisir la bonne commande.
Le seuil unique est la version de Windows : tout ce qui precede est apparu
avec Windows 8 / Server 2012 (6.2). En dessous, on utilise les equivalents
historiques (WMI, netsh, net share, netstat) qui, eux, existent encore sur les
Windows recents : les chemins "legacy" restent donc testables partout.
"""
import functools
import struct
import sys
import winreg


# Version de Windows a partir de laquelle les cmdlets modernes existent :
# 6.2 = Windows 8 / Windows Server 2012.
_MODERN_CMDLETS_SINCE = (6, 2)

_CURRENT_VERSION_KEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"


@functools.lru_cache(maxsize=1)
def windows_version():
    """(major, minor) de Windows, lu dans le registre.

    On privilegie le registre car sys.getwindowsversion() peut mentir : sans
    manifeste declarant la compatibilite, Windows plafonne la version rapportee
    a 6.2. Le registre, lui, donne toujours la vraie valeur.
    """
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _CURRENT_VERSION_KEY) as key:
            try:
                # Presentes a partir de Windows 10 uniquement.
                major = winreg.QueryValueEx(key, "CurrentMajorVersionNumber")[0]
                minor = winreg.QueryValueEx(key, "CurrentMinorVersionNumber")[0]
                return (int(major), int(minor))
            except OSError:
                # Windows 7/8/8.1 : chaine "6.1", "6.2", "6.3".
                current = str(winreg.QueryValueEx(key, "CurrentVersion")[0])
                major, _, minor = current.partition(".")
                return (int(major), int(minor or 0))
    except Exception:
        pass
    try:
        info = sys.getwindowsversion()
        return (int(info.major), int(info.minor))
    except Exception:
        # Inconnu : on suppose un OS recent pour ne pas degrader inutilement.
        return _MODERN_CMDLETS_SINCE


@functools.lru_cache(maxsize=1)
def has_modern_cmdlets():
    """True si l'OS fournit PowerShell 3.0+ et les modules Net*/Smb*/Storage."""
    return windows_version() >= _MODERN_CMDLETS_SINCE


def is_legacy_os():
    return not has_modern_cmdlets()


@functools.lru_cache(maxsize=1)
def windows_label():
    """Libelle lisible de l'OS, pour les journaux."""
    major, minor = windows_version()
    return {
        (6, 0): "Windows Vista / Server 2008",
        (6, 1): "Windows 7 / Server 2008 R2",
        (6, 2): "Windows 8 / Server 2012",
        (6, 3): "Windows 8.1 / Server 2012 R2",
        (10, 0): "Windows 10/11 / Server 2016+",
    }.get((major, minor), f"Windows {major}.{minor}")


# --- Saveur du build (utilisee par l'updater pour ne pas croiser les canaux) ---

def is_legacy_build():
    """True si l'executable courant est le build legacy (compile en Python 3.8).

    On se base sur la version de l'interpreteur plutot que sur un drapeau injecte
    au build : c'est infalsifiable et ne demande aucune plomberie PyInstaller.
    """
    return sys.version_info < (3, 9)


def is_64bit():
    return struct.calcsize("P") * 8 == 64


def build_flavor():
    """'modern' | 'legacy_x64' | 'legacy_x86' — identifie l'asset de mise a jour."""
    if not is_legacy_build():
        return "modern"
    return "legacy_x64" if is_64bit() else "legacy_x86"


@functools.lru_cache(maxsize=1)
def os_architecture():
    """Architecture du SYSTEME : 'x64' ou 'x86'.

    A ne pas confondre avec celle du processus : le build legacy est compile en
    32 bits et tourne aussi sur des Windows 64 bits. Un processus 32 bits sous
    WOW64 voit PROCESSOR_ARCHITECTURE='x86' ; c'est PROCESSOR_ARCHITEW6432 qui
    revele alors la vraie architecture de la machine. Sert a proposer le bon
    installeur (Notepad++, RetailForce, HFSQL...).
    """
    import os
    arch = (os.environ.get("PROCESSOR_ARCHITEW6432")
            or os.environ.get("PROCESSOR_ARCHITECTURE") or "").upper()
    if arch in ("AMD64", "IA64", "ARM64"):
        return "x64"
    if arch == "X86":
        return "x86"
    # Repli : si le processus lui-meme est 64 bits, le systeme l'est forcement.
    return "x64" if is_64bit() else "x86"


# --- Helpers PowerShell ---

def cim_cmdlet():
    """Cmdlet d'interrogation WMI adaptee a l'OS.

    Get-CimInstance (PowerShell 3.0+) n'existe pas sur Windows 7 ; Get-WmiObject
    accepte la meme syntaxe pour les classes Win32_* (y compris -Filter) et reste
    disponible sur les Windows recents via PowerShell 5.1.
    """
    return "Get-CimInstance" if has_modern_cmdlets() else "Get-WmiObject"


def uses_wmi_dates():
    """True si les dates renvoyees sont au format WMI (chaine CIM_DATETIME).

    Get-CimInstance deserialise les dates en [datetime]; Get-WmiObject renvoie
    une chaine '20260730161954.500000+120' qu'il faut convertir via la methode
    ConvertToDateTime() portee par l'objet WMI lui-meme.
    """
    return not has_modern_cmdlets()


# Fragment PowerShell injecte en tete des scripts de collecte : fournit une
# fonction ToDate uniforme quel que soit le cmdlet utilise.
PS_DATE_HELPER = """
$UseWmiDates = ${use_wmi}
function ToDate($obj, $val) {
    if ($null -eq $val) { return $null }
    if ($UseWmiDates) {
        try { return $obj.ConvertToDateTime($val) } catch { return $null }
    }
    return $val
}
"""


def ps_date_helper():
    """Retourne le fragment PowerShell ToDate() pret a etre injecte."""
    return PS_DATE_HELPER.replace("${use_wmi}", "$true" if uses_wmi_dates() else "$false")


# Fragment PowerShell fournissant ToJson(), utilisable sur PowerShell 2.0.
# ConvertTo-Json n'existe qu'a partir de PowerShell 3.0, or Windows 7 et
# Server 2008 R2 livrent PowerShell 2.0 : sans repli, toute commande qui rend
# du JSON echoue (collecte Info PC, etat des partages, pare-feu...).
# Le repli utilise le serialiseur JSON de .NET 3.5 (System.Web.Extensions),
# present nativement sur ces machines. La detection se fait dans le script
# lui-meme : une machine Windows 7 dotee de WMF 3+ reprend automatiquement
# ConvertTo-Json.
PS_JSON_HELPER = r"""
function NormalizeForJson($value) {
    # Reduit une structure a des types primitifs. Indispensable avant
    # JavaScriptSerializer : les proprietes issues de Get-WmiObject restent
    # enveloppees dans des PSObject, que le serialiseur .NET suit en profondeur
    # jusqu'a lever "A circular reference was detected".
    if ($null -eq $value) { return $null }
    if ($value -is [System.Collections.IDictionary]) {
        $copy = @{}
        foreach ($key in @($value.Keys)) {
            $copy[[string]$key] = NormalizeForJson $value[$key]
        }
        return $copy
    }
    if ($value -is [string]) { return [string]$value }
    if ($value -is [bool] -or $value -is [int] -or $value -is [long] -or
        $value -is [double] -or $value -is [decimal] -or $value -is [single]) {
        return $value
    }
    if ($value -is [System.Collections.IEnumerable]) {
        $items = @()
        foreach ($item in $value) { $items += ,(NormalizeForJson $item) }
        return ,$items
    }
    # PSObject, objet WMI, enum... : representation texte, toujours serialisable.
    return [string]$value
}

function ToJson($obj) {
    if (Get-Command ConvertTo-Json -ErrorAction SilentlyContinue) {
        return ($obj | ConvertTo-Json -Compress -Depth 8)
    }
    Add-Type -AssemblyName System.Web.Extensions -ErrorAction SilentlyContinue
    $serializer = New-Object System.Web.Script.Serialization.JavaScriptSerializer
    $serializer.MaxJsonLength = 67108864
    return $serializer.Serialize((NormalizeForJson $obj))
}
"""


def ps_json_helper():
    """Fragment PowerShell definissant ToJson() (compatible PowerShell 2.0)."""
    return PS_JSON_HELPER
