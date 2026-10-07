import ctypes

from ._common import (
    FIREWALL_HFSQL_DISPLAY_NAME,
    FIREWALL_HFSQL_PORT,
    FIREWALL_HFSQL_PROTOCOL,
    FIREWALL_HFSQL_RULE_DESCRIPTION,
    FIREWALL_HFSQL_RULE_NAME,
    FIREWALL_RULE_DESCRIPTION,
    FIREWALL_RULE_DISPLAY_NAME,
    FIREWALL_RULE_NAME,
    FIREWALL_RULE_PROTOCOL,
    FIREWALL_TCP_PORT,
    OperationError,
    SmtpTestError,
    ToolLogger,
    _PROTOCOL_TO_NUMBER,
    _REQUIRED_FIREWALL_PROFILES,
    _parse_firewall_rule,
    _read_firewall_rules,
    _rule_lport_matches,
    _rule_profiles,
    command_output,
    is_admin,
    powershell_output,
)
from ._compat import has_modern_cmdlets, ps_json_helper

class FirewallManager:
    def __init__(self, logger):
        self.logger = logger

    def check_status(self):
        # Lecture directe du registre : ~10ms vs 5-10s pour Get-NetFirewallRule.
        # Format de chaque valeur : "v2.10|Action=Allow|Active=TRUE|Dir=In|Protocol=6|LPort=7678|Name=...|".
        try:
            rules = _read_firewall_rules()
        except Exception as exc:
            raise OperationError(f"Lecture des regles pare-feu impossible : {exc}") from exc

        retailforce_proto = _PROTOCOL_TO_NUMBER.get(FIREWALL_RULE_PROTOCOL, FIREWALL_RULE_PROTOCOL)
        hfsql_proto = _PROTOCOL_TO_NUMBER.get(FIREWALL_HFSQL_PROTOCOL, FIREWALL_HFSQL_PROTOCOL)

        # Recherche de la regle RetailForce par nom interne (registry value name) puis fallback display name.
        target_raw = None
        target_fields = None
        target_display = FIREWALL_RULE_DISPLAY_NAME
        for name, raw in rules:
            if name == FIREWALL_RULE_NAME:
                target_raw = raw
                target_fields = _parse_firewall_rule(raw)
                target_display = target_fields.get("Name", FIREWALL_RULE_DISPLAY_NAME)
                break
        if target_fields is None:
            for name, raw in rules:
                fields = _parse_firewall_rule(raw)
                if fields.get("Name", "") == FIREWALL_RULE_DISPLAY_NAME:
                    target_raw = raw
                    target_fields = fields
                    target_display = fields.get("Name", FIREWALL_RULE_DISPLAY_NAME)
                    break

        rule_present = target_fields is not None
        display_name_ok = enabled_ok = direction_ok = action_ok = protocol_ok = port_ok = profiles_ok = False
        if target_fields is not None:
            display_name_ok = target_fields.get("Name", "") == FIREWALL_RULE_DISPLAY_NAME
            enabled_ok = target_fields.get("Active", "").upper() == "TRUE"
            direction_ok = target_fields.get("Dir", "") == "In"
            action_ok = target_fields.get("Action", "") == "Allow"
            protocol_ok = target_fields.get("Protocol", "") == retailforce_proto
            port_ok = _rule_lport_matches(target_fields, FIREWALL_TCP_PORT)
            profiles_ok = _REQUIRED_FIREWALL_PROFILES.issubset(_rule_profiles(target_raw))

        # HFSQL : on agrege les profils de toutes les regles inbound allow sur TCP 4900.
        # Couverture totale = Domain + Private + Public (peut etre splittee sur plusieurs regles).
        hfsql_profiles_covered = set()
        hfsql_any_match = False
        hfsql_display = FIREWALL_HFSQL_DISPLAY_NAME
        for _name, raw in rules:
            fields = _parse_firewall_rule(raw)
            if (fields.get("Active", "").upper() == "TRUE"
                    and fields.get("Dir", "") == "In"
                    and fields.get("Action", "") == "Allow"
                    and fields.get("Protocol", "") == hfsql_proto
                    and _rule_lport_matches(fields, FIREWALL_HFSQL_PORT)):
                hfsql_profiles_covered |= _rule_profiles(raw)
                if not hfsql_any_match:
                    hfsql_display = fields.get("Name", FIREWALL_HFSQL_DISPLAY_NAME)
                hfsql_any_match = True

        hfsql_port_open = hfsql_any_match
        hfsql_profiles_ok = _REQUIRED_FIREWALL_PROFILES.issubset(hfsql_profiles_covered)

        return {
            "rule_present": rule_present,
            "display_name_ok": display_name_ok,
            "enabled_ok": enabled_ok,
            "direction_ok": direction_ok,
            "action_ok": action_ok,
            "protocol_ok": protocol_ok,
            "port_ok": port_ok,
            "profiles_ok": profiles_ok,
            "hfsql_port_open": hfsql_port_open,
            "hfsql_profiles_ok": hfsql_profiles_ok,
            "rule_name": FIREWALL_RULE_NAME,
            "display_name": target_display,
            "protocol": FIREWALL_RULE_PROTOCOL,
            "port": FIREWALL_TCP_PORT,
            "hfsql_display_name": hfsql_display,
            "hfsql_protocol": FIREWALL_HFSQL_PROTOCOL,
            "hfsql_port": FIREWALL_HFSQL_PORT,
        }

    def create_or_repair(self):
        if not is_admin():
            raise OperationError("Les actions pare-feu demandent un lancement en administrateur.")

        self.logger.info(
            f"Création / réparation des règles pare-feu {FIREWALL_RULE_DISPLAY_NAME} et {FIREWALL_HFSQL_DISPLAY_NAME}"
        )
        # Le module NetSecurity (New-NetFirewallRule) n'existe qu'a partir de
        # Windows 8 / Server 2012. En dessous, on passe par netsh advfirewall,
        # qui produit exactement les memes regles dans le registre — et c'est
        # le registre que check_status relit.
        if has_modern_cmdlets():
            result = powershell_output(ps_json_helper() + self._create_or_repair_script())
            if result.returncode != 0:
                details = result.stderr.strip() or result.stdout.strip() or "Création de la règle pare-feu impossible."
                self.logger.error(details)
                raise OperationError("Création de la règle pare-feu impossible. Consultez le journal.")
        else:
            self._create_or_repair_netsh()

        self.logger.info(f"Règles {FIREWALL_RULE_DISPLAY_NAME} et {FIREWALL_HFSQL_DISPLAY_NAME} vérifiées / réparées.")
        return self.check_status()

    def _create_or_repair_netsh(self):
        # Equivalent historique de New-NetFirewallRule (Windows 7 / Server 2008 R2).
        # netsh identifie les regles par leur nom d'affichage : on supprime
        # d'abord les homonymes pour rester idempotent, puis on recree.
        # 'profile=any' couvre Domaine + Prive + Public.
        for display_name, description, protocol, port in (
            (FIREWALL_RULE_DISPLAY_NAME, FIREWALL_RULE_DESCRIPTION,
             FIREWALL_RULE_PROTOCOL, FIREWALL_TCP_PORT),
            (FIREWALL_HFSQL_DISPLAY_NAME, FIREWALL_HFSQL_RULE_DESCRIPTION,
             FIREWALL_HFSQL_PROTOCOL, FIREWALL_HFSQL_PORT),
        ):
            # Suppression best-effort : netsh renvoie un code != 0 quand aucune
            # regle ne correspond, ce qui est un cas normal ici.
            command_output([
                "netsh", "advfirewall", "firewall", "delete", "rule",
                f"name={display_name}",
            ])
            result = command_output([
                "netsh", "advfirewall", "firewall", "add", "rule",
                f"name={display_name}",
                "dir=in",
                "action=allow",
                f"protocol={protocol}",
                f"localport={port}",
                "profile=any",
                "enable=yes",
                f"description={description}",
            ])
            if result.returncode != 0:
                details = (result.stderr or result.stdout or "").strip()
                self.logger.error(f"netsh advfirewall a échoué pour {display_name} : {details}")
                raise OperationError(
                    f"Création de la règle pare-feu « {display_name} » impossible (netsh). "
                    "Consultez le journal."
                )
            self.logger.info(f"Règle « {display_name} » créée via netsh (TCP {port}).")

    def _status_script(self):
        return f"""
$ErrorActionPreference = 'SilentlyContinue'
$ruleName = '{FIREWALL_RULE_NAME}'
$displayName = '{FIREWALL_RULE_DISPLAY_NAME}'
$port = '{FIREWALL_TCP_PORT}'
$hfsqlDisplayName = '{FIREWALL_HFSQL_DISPLAY_NAME}'
$hfsqlPort = '{FIREWALL_HFSQL_PORT}'
$rule = Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $rule) {{
    $rule = Get-NetFirewallRule -DisplayName $displayName -ErrorAction SilentlyContinue | Select-Object -First 1
}}

$rulePresent = $null -ne $rule
$displayNameOk = $false
$enabledOk = $false
$directionOk = $false
$actionOk = $false
$protocolOk = $false
$portOk = $false
$hfsqlPortOpen = $false

if ($rulePresent) {{
    $displayNameOk = $rule.DisplayName -eq $displayName
    $enabledOk = ([string]$rule.Enabled) -eq 'True'
    $directionOk = ([string]$rule.Direction) -eq 'Inbound'
    $actionOk = ([string]$rule.Action) -eq 'Allow'
    $filter = $rule | Get-NetFirewallPortFilter -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($filter) {{
        $protocolOk = ([string]$filter.Protocol) -eq '{FIREWALL_RULE_PROTOCOL}'
        $portOk = (($filter.LocalPort -as [string]) -eq $port)
    }}
}}

$hfsqlRule = Get-NetFirewallRule -PolicyStore ActiveStore -Enabled True -Direction Inbound -Action Allow -ErrorAction SilentlyContinue | Where-Object {{
    $filter = $_ | Get-NetFirewallPortFilter -ErrorAction SilentlyContinue | Select-Object -First 1
    $filter -and ([string]$filter.Protocol) -eq '{FIREWALL_HFSQL_PROTOCOL}' -and (($filter.LocalPort -as [string]) -eq $hfsqlPort)
}} | Select-Object -First 1

if ($hfsqlRule) {{
    $hfsqlPortOpen = $true
}}

ToJson @{{
    rule_present = [bool]$rulePresent
    display_name_ok = [bool]$displayNameOk
    enabled_ok = [bool]$enabledOk
    direction_ok = [bool]$directionOk
    action_ok = [bool]$actionOk
    protocol_ok = [bool]$protocolOk
    port_ok = [bool]$portOk
    hfsql_port_open = [bool]$hfsqlPortOpen
    rule_name = if ($rulePresent) {{ $rule.Name }} else {{ $ruleName }}
    display_name = if ($rulePresent) {{ $rule.DisplayName }} else {{ $displayName }}
    hfsql_display_name = if ($hfsqlRule) {{ $hfsqlRule.DisplayName }} else {{ $hfsqlDisplayName }}
}}
""".strip()

    def _create_or_repair_script(self):
        return f"""
$ErrorActionPreference = 'Stop'
$ruleName = '{FIREWALL_RULE_NAME}'
$displayName = '{FIREWALL_RULE_DISPLAY_NAME}'
$description = '{FIREWALL_RULE_DESCRIPTION}'
$hfsqlRuleName = '{FIREWALL_HFSQL_RULE_NAME}'
$hfsqlDisplayName = '{FIREWALL_HFSQL_DISPLAY_NAME}'
$hfsqlDescription = '{FIREWALL_HFSQL_RULE_DESCRIPTION}'
$hfsqlPort = '{FIREWALL_HFSQL_PORT}'
$rules = @()
$rules += Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue
$rules += Get-NetFirewallRule -DisplayName $displayName -ErrorAction SilentlyContinue

if ($rules.Count -gt 0) {{
    $rules | Sort-Object -Property Name -Unique | Remove-NetFirewallRule -Confirm:$false
}}

New-NetFirewallRule `
    -Name $ruleName `
    -DisplayName $displayName `
    -Description $description `
    -Direction Inbound `
    -Action Allow `
    -Protocol {FIREWALL_RULE_PROTOCOL} `
    -LocalPort {FIREWALL_TCP_PORT} `
    -Profile Domain,Private,Public `
    -Enabled True | Out-Null

$hfsqlMatches = Get-NetFirewallRule -PolicyStore ActiveStore -Enabled True -Direction Inbound -Action Allow -ErrorAction SilentlyContinue | Where-Object {{
    $filter = $_ | Get-NetFirewallPortFilter -ErrorAction SilentlyContinue | Select-Object -First 1
    $filter -and ([string]$filter.Protocol) -eq '{FIREWALL_HFSQL_PROTOCOL}' -and (($filter.LocalPort -as [string]) -eq $hfsqlPort)
}}

$hfsqlProfiles = @()
foreach ($r in $hfsqlMatches) {{
    foreach ($p in ([string]$r.Profile).Split(',')) {{
        $hfsqlProfiles += $p.Trim()
    }}
}}
$hasDomain = ($hfsqlProfiles -contains 'Domain') -or ($hfsqlProfiles -contains 'Any')
$hasPrivate = ($hfsqlProfiles -contains 'Private') -or ($hfsqlProfiles -contains 'Any')
$hasPublic = ($hfsqlProfiles -contains 'Public') -or ($hfsqlProfiles -contains 'Any')

if (-not ($hasDomain -and $hasPrivate -and $hasPublic)) {{
    $hfsqlRules = @()
    $hfsqlRules += Get-NetFirewallRule -Name $hfsqlRuleName -ErrorAction SilentlyContinue
    $hfsqlRules += Get-NetFirewallRule -DisplayName $hfsqlDisplayName -ErrorAction SilentlyContinue
    if ($hfsqlRules.Count -gt 0) {{
        $hfsqlRules | Sort-Object -Property Name -Unique | Remove-NetFirewallRule -Confirm:$false
    }}

    New-NetFirewallRule `
        -Name $hfsqlRuleName `
        -DisplayName $hfsqlDisplayName `
        -Description $hfsqlDescription `
        -Direction Inbound `
        -Action Allow `
        -Protocol {FIREWALL_HFSQL_PROTOCOL} `
        -LocalPort {FIREWALL_HFSQL_PORT} `
        -Profile Domain,Private,Public `
        -Enabled True | Out-Null
}}

ToJson @{{ ok = $true }}
""".strip()


# --- Énumération rapide des cartes réseau via GetAdaptersAddresses ---
# Remplace les appels PowerShell (coût de démarrage 400-800 ms) par une
# lecture native de l'API iphlpapi qui retourne tout en quelques millisecondes.

from ctypes import wintypes as _wintypes

