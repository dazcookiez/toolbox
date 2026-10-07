import datetime
import json
from pathlib import Path

from ._common import (
    OperationError,
    SHARE_DIR,
    SHARE_NAME,
    SmtpTestError,
    ToolLogger,
    command_output,
    is_admin,
    powershell_output,
)
from ._compat import has_modern_cmdlets, ps_json_helper


class ImpressionsManager:
    CACHE_TTL_SECONDS = 8

    def __init__(self, logger):
        self.logger = logger
        self._cache = None
        self._cache_at = None

    def invalidate_cache(self):
        self._cache = None
        self._cache_at = None

    def check_status(self, force_refresh=False):
        # Cache court : evite de relancer PowerShell (~500ms) a chaque navigation.
        # force_refresh=True apres create_or_repair pour voir l'effet immediat.
        now = datetime.datetime.now()
        if (
            not force_refresh
            and self._cache is not None
            and self._cache_at is not None
            and (now - self._cache_at).total_seconds() < self.CACHE_TTL_SECONDS
        ):
            return dict(self._cache)

        result = powershell_output(ps_json_helper() + (
            self._status_script() if has_modern_cmdlets() else self._status_script_legacy()
        ))
        if result.returncode != 0 and not result.stdout.strip():
            raise OperationError(result.stderr.strip() or "Impossible de vérifier ImpressionsVega.")

        try:
            data = json.loads(result.stdout.strip() or "{}")
        except Exception as exc:
            raise OperationError(f"Sortie PowerShell invalide pour ImpressionsVega : {exc}") from exc

        status = {
            "folder_exists": bool(data.get("folder_exists")),
            "share_exists": bool(data.get("share_exists")),
            "share_path_ok": bool(data.get("share_path_ok")),
            "share_everyone_full": bool(data.get("share_everyone_full")),
            "ntfs_everyone_full": bool(data.get("ntfs_everyone_full")),
            "path": str(SHARE_DIR),
            "share_name": SHARE_NAME,
            "everyone_name": data.get("everyone_name", "Everyone"),
        }
        self._cache = dict(status)
        self._cache_at = now
        return status

    def create_or_repair(self):
        if not is_admin():
            raise OperationError("Les actions ImpressionsVega demandent un lancement en administrateur.")

        self.logger.info("Création / réparation de C:\\ImpressionsVega")
        SHARE_DIR.mkdir(parents=True, exist_ok=True)

        acl_result = command_output(["icacls", str(SHARE_DIR), "/grant", "*S-1-1-0:(OI)(CI)F", "/T", "/C"])
        if acl_result.returncode != 0:
            self.logger.error(f"icacls a échoué : {acl_result.stderr or acl_result.stdout}")
        else:
            self.logger.info("Droits NTFS 'Tout le monde : contrôle total' appliqués.")

        share_result = powershell_output(ps_json_helper() + (
            self._create_or_repair_script() if has_modern_cmdlets()
            else self._create_or_repair_script_legacy()
        ))
        if share_result.returncode != 0:
            details = share_result.stderr.strip() or share_result.stdout.strip() or "Création du partage impossible."
            self.logger.error(details)
            raise OperationError("Création du partage ImpressionsVega impossible. Consultez le journal.")

        self.logger.info(f"Partage {SHARE_NAME} vérifié / réparé.")
        self.invalidate_cache()
        return self.check_status(force_refresh=True)

    def _status_script(self):
        share_path = str(SHARE_DIR)
        return f"""
$ErrorActionPreference = 'SilentlyContinue'
$shareName = '{SHARE_NAME}'
$sharePath = '{share_path}'
$everyone = (New-Object System.Security.Principal.SecurityIdentifier('S-1-1-0')).Translate([System.Security.Principal.NTAccount]).Value
$folderExists = Test-Path -LiteralPath $sharePath -PathType Container
$share = Get-SmbShare -IncludeHidden | Where-Object {{ $_.Name -eq $shareName }} | Select-Object -First 1
$shareExists = $null -ne $share
$sharePathOk = $false
$shareEveryoneFull = $false
$ntfsEveryoneFull = $false

if ($shareExists) {{
    $sharePathOk = ([System.IO.Path]::GetFullPath($share.Path) -ieq [System.IO.Path]::GetFullPath($sharePath))
    $access = Get-SmbShareAccess -Name $shareName -ErrorAction SilentlyContinue
    $shareEveryoneFull = ($access | Where-Object {{
        $_.AccessControlType -eq 'Allow' -and
        $_.AccessRight -eq 'Full' -and
        $_.AccountName -eq $everyone
    }} | Measure-Object).Count -gt 0
}}

if ($folderExists) {{
    $acl = Get-Acl -LiteralPath $sharePath -ErrorAction SilentlyContinue
    if ($acl) {{
        $ntfsEveryoneFull = ($acl.Access | Where-Object {{
            $_.AccessControlType -eq 'Allow' -and
            $_.IdentityReference.Value -eq $everyone -and
            $_.FileSystemRights.ToString().Contains('FullControl')
        }} | Measure-Object).Count -gt 0
    }}
}}

ToJson @{{
    everyone_name = $everyone
    folder_exists = [bool]$folderExists
    share_exists = [bool]$shareExists
    share_path_ok = [bool]$sharePathOk
    share_everyone_full = [bool]$shareEveryoneFull
    ntfs_everyone_full = [bool]$ntfsEveryoneFull
}}
""".strip()

    def _create_or_repair_script(self):
        share_path = str(SHARE_DIR)
        return f"""
$ErrorActionPreference = 'Stop'
$shareName = '{SHARE_NAME}'
$sharePath = '{share_path}'
$everyone = (New-Object System.Security.Principal.SecurityIdentifier('S-1-1-0')).Translate([System.Security.Principal.NTAccount]).Value
$existing = Get-SmbShare -IncludeHidden | Where-Object {{ $_.Name -eq $shareName }} | Select-Object -First 1

if ($existing -and $existing.Path -ne $sharePath) {{
    Remove-SmbShare -Name $shareName -Force -Confirm:$false
    $existing = $null
}}

if (-not $existing) {{
    New-SmbShare -Name $shareName -Path $sharePath -FullAccess @($everyone) -CachingMode None -Description 'Impressions Vega' -Confirm:$false | Out-Null
}} else {{
    $access = Get-SmbShareAccess -Name $shareName -ErrorAction SilentlyContinue
    $hasFull = ($access | Where-Object {{
        $_.AccessControlType -eq 'Allow' -and
        $_.AccessRight -eq 'Full' -and
        $_.AccountName -eq $everyone
    }} | Measure-Object).Count -gt 0
    if (-not $hasFull) {{
        Grant-SmbShareAccess -Name $shareName -AccountName $everyone -AccessRight Full -Force -Confirm:$false | Out-Null
    }}
}}

ToJson @{{ ok = $true }}
""".strip()

    # ------------------------------------------------------------------
    # Variantes historiques (Windows 7 / Server 2008 R2)
    # Le module SmbShare (Get-SmbShare / New-SmbShare / Grant-SmbShareAccess)
    # est apparu avec Windows 8 / Server 2012. En dessous on utilise :
    #   - Win32_Share                        pour l'existence et le chemin
    #   - Win32_LogicalShareSecuritySetting  pour les droits de partage
    #   - net share                          pour creer / accorder les droits
    # Les droits sont testes par SID (S-1-1-0) et non par nom, donc insensibles
    # a la langue du systeme.
    # ------------------------------------------------------------------

    def _status_script_legacy(self):
        share_path = str(SHARE_DIR)
        return f"""
$ErrorActionPreference = 'SilentlyContinue'
$shareName = '{SHARE_NAME}'
$sharePath = '{share_path}'
$everyone = (New-Object System.Security.Principal.SecurityIdentifier('S-1-1-0')).Translate([System.Security.Principal.NTAccount]).Value
$folderExists = Test-Path -LiteralPath $sharePath -PathType Container
$share = Get-WmiObject -Class Win32_Share -Filter "Name='$shareName'" | Select-Object -First 1
$shareExists = $null -ne $share
$sharePathOk = $false
$shareEveryoneFull = $false
$ntfsEveryoneFull = $false

if ($shareExists) {{
    $sharePathOk = ([System.IO.Path]::GetFullPath($share.Path) -ieq [System.IO.Path]::GetFullPath($sharePath))
    try {{
        $sec = Get-WmiObject -Class Win32_LogicalShareSecuritySetting -Filter "Name='$shareName'"
        if ($sec) {{
            $sd = $sec.GetSecurityDescriptor().Descriptor
            foreach ($ace in $sd.DACL) {{
                # AceType 0 = Allow ; 2032127 = FULL CONTROL
                if ($ace.Trustee.SIDString -eq 'S-1-1-0' -and
                    $ace.AceType -eq 0 -and
                    (($ace.AccessMask -band 2032127) -eq 2032127)) {{
                    $shareEveryoneFull = $true
                }}
            }}
        }}
    }} catch {{}}
}}

if ($folderExists) {{
    $acl = Get-Acl -LiteralPath $sharePath -ErrorAction SilentlyContinue
    if ($acl) {{
        $ntfsEveryoneFull = ($acl.Access | Where-Object {{
            $_.AccessControlType -eq 'Allow' -and
            $_.IdentityReference.Value -eq $everyone -and
            $_.FileSystemRights.ToString().Contains('FullControl')
        }} | Measure-Object).Count -gt 0
    }}
}}

ToJson @{{
    everyone_name = $everyone
    folder_exists = [bool]$folderExists
    share_exists = [bool]$shareExists
    share_path_ok = [bool]$sharePathOk
    share_everyone_full = [bool]$shareEveryoneFull
    ntfs_everyone_full = [bool]$ntfsEveryoneFull
}}
""".strip()

    def _create_or_repair_script_legacy(self):
        share_path = str(SHARE_DIR)
        return f"""
$ErrorActionPreference = 'Stop'
$shareName = '{SHARE_NAME}'
$sharePath = '{share_path}'
$everyone = (New-Object System.Security.Principal.SecurityIdentifier('S-1-1-0')).Translate([System.Security.Principal.NTAccount]).Value
$existing = Get-WmiObject -Class Win32_Share -Filter "Name='$shareName'" | Select-Object -First 1

# Partage present mais pointant ailleurs : on le supprime pour le recreer.
if ($existing -and ([System.IO.Path]::GetFullPath($existing.Path) -ine [System.IO.Path]::GetFullPath($sharePath))) {{
    & net share "$shareName" /DELETE /Y | Out-Null
    $existing = $null
}}

if (-not $existing) {{
    # net share cree le partage ET pose les droits en une seule operation.
    $out = & net share "$shareName=$sharePath" "/GRANT:$everyone,FULL" /REMARK:"Impressions Vega" 2>&1
    if ($LASTEXITCODE -ne 0) {{ throw "net share a echoue : $out" }}
}} else {{
    # Partage deja correct : on (re)pose simplement les droits Tout le monde.
    $out = & net share "$shareName" "/GRANT:$everyone,FULL" 2>&1
    if ($LASTEXITCODE -ne 0) {{
        # Certaines versions refusent /GRANT sur un partage existant :
        # on recree alors le partage avec les bons droits.
        & net share "$shareName" /DELETE /Y | Out-Null
        $out = & net share "$shareName=$sharePath" "/GRANT:$everyone,FULL" /REMARK:"Impressions Vega" 2>&1
        if ($LASTEXITCODE -ne 0) {{ throw "net share a echoue : $out" }}
    }}
}}

ToJson @{{ ok = $true }}
""".strip()


