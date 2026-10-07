"""Collecte d'informations systeme + Vega-specifiques pour l'onglet Info PC.

Affiche un snapshot du poste a la facon CPU-Z / fastfetch : hardware (CPU, RAM,
GPU, motherboard, BIOS), OS, disques, reseau, plus contexte Vega (service
HFSQL, partages SMB, .exe Vega detectes).

Tout passe par PowerShell + Get-CimInstance (WMI) en un seul gros script pour
eviter de payer le cout de demarrage de pwsh.exe 10 fois. La sortie est un
seul objet JSON consomme par le module UI.
"""
import json
from pathlib import Path

from ._common import OperationError, powershell_output
from ._compat import cim_cmdlet, ps_date_helper, ps_json_helper


# Script PowerShell unique qui collecte TOUT en une passe et renvoie un JSON.
# Pas d'interaction utilisateur, pas d'erreurs bloquantes : chaque section
# est protegee par try/catch et renvoie $null en cas d'echec.
_COLLECT_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'

function ToGB($bytes) {
    if (-not $bytes) { return $null }
    return [math]::Round([double]$bytes / 1GB, 2)
}

function ToMB($bytes) {
    if (-not $bytes) { return $null }
    return [math]::Round([double]$bytes / 1MB, 0)
}

# --- System / Computer ---
$cs = Get-CimInstance Win32_ComputerSystem
$os = Get-CimInstance Win32_OperatingSystem
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$bios = Get-CimInstance Win32_BIOS
$mobo = Get-CimInstance Win32_BaseBoard

# Detection machine virtuelle : sert a nuancer la detection SSD (sur une VM le
# type de disque physique n'est souvent pas exposé, on ne doit pas conclure HDD).
$vmSignatures = 'virtual|vmware|vbox|virtualbox|qemu|kvm|\bxen\b|hyper-v|bochs|parallels|innotek'
$isVirtual = ($cs.Model -match $vmSignatures) -or ($cs.Manufacturer -match $vmSignatures) -or ($bios.Manufacturer -match $vmSignatures)

$system = @{
    Hostname = $cs.Name
    Domain = $cs.Domain
    Workgroup = $cs.Workgroup
    PartOfDomain = [bool]$cs.PartOfDomain
    Manufacturer = $cs.Manufacturer
    Model = $cs.Model
    SystemType = $cs.SystemType
    TotalPhysicalMemoryGB = ToGB $cs.TotalPhysicalMemory
    NumberOfProcessors = $cs.NumberOfProcessors
    NumberOfLogicalProcessors = $cs.NumberOfLogicalProcessors
    CurrentUser = $cs.UserName
    IsVirtual = [bool]$isVirtual
}

# Uptime
# ToDate() normalise les dates : Get-CimInstance renvoie deja des [datetime],
# Get-WmiObject (Windows 7) renvoie des chaines CIM_DATETIME a convertir.
$bootTime = ToDate $os $os.LastBootUpTime
$installDate = ToDate $os $os.InstallDate
$uptime = $null
if ($bootTime) {
    $diff = (Get-Date) - $bootTime
    $uptime = "{0}j {1}h {2}m" -f $diff.Days, $diff.Hours, $diff.Minutes
}

$osInfo = @{
    Caption = $os.Caption
    Version = $os.Version
    BuildNumber = $os.BuildNumber
    OSArchitecture = $os.OSArchitecture
    InstallDate = if ($installDate) { $installDate.ToString('yyyy-MM-dd HH:mm') } else { $null }
    LastBootUpTime = if ($bootTime) { $bootTime.ToString('yyyy-MM-dd HH:mm') } else { $null }
    Uptime = $uptime
    Language = (Get-Culture).Name
    SerialNumber = $os.SerialNumber
}

# --- CPU ---
$cpuInfo = @{
    Name = if ($cpu) { ($cpu.Name -replace '\s+', ' ').Trim() } else { $null }
    Manufacturer = if ($cpu) { $cpu.Manufacturer } else { $null }
    Cores = if ($cpu) { [int]$cpu.NumberOfCores } else { $null }
    Threads = if ($cpu) { [int]$cpu.NumberOfLogicalProcessors } else { $null }
    MaxClockMHz = if ($cpu) { [int]$cpu.MaxClockSpeed } else { $null }
    CurrentClockMHz = if ($cpu) { [int]$cpu.CurrentClockSpeed } else { $null }
    Socket = if ($cpu) { $cpu.SocketDesignation } else { $null }
    Architecture = if ($cpu) { switch ($cpu.Architecture) {
        0 { 'x86' }; 5 { 'ARM' }; 9 { 'x64' }; 12 { 'ARM64' }; default { "$($cpu.Architecture)" }
    } } else { $null }
    L2CacheKB = if ($cpu) { [int]$cpu.L2CacheSize } else { $null }
    L3CacheKB = if ($cpu) { [int]$cpu.L3CacheSize } else { $null }
}

# --- RAM modules ---
$ramModules = @()
foreach ($m in (Get-CimInstance Win32_PhysicalMemory)) {
    $ramModules += @{
        Slot = $m.DeviceLocator
        BankLabel = $m.BankLabel
        CapacityGB = ToGB $m.Capacity
        SpeedMHz = [int]$m.Speed
        ConfiguredSpeedMHz = [int]$m.ConfiguredClockSpeed
        Manufacturer = ($m.Manufacturer -replace '\s+$', '')
        PartNumber = ($m.PartNumber -replace '\s+$', '')
        FormFactor = switch ($m.FormFactor) {
            8 { 'DIMM' }; 12 { 'SODIMM' }; default { "$($m.FormFactor)" }
        }
        MemoryType = switch ($m.SMBIOSMemoryType) {
            20 { 'DDR' }; 21 { 'DDR2' }; 22 { 'DDR2 FB-DIMM' };
            24 { 'DDR3' }; 26 { 'DDR4' };
            27 { 'LPDDR' }; 28 { 'LPDDR2' }; 29 { 'LPDDR3' }; 30 { 'LPDDR4' };
            34 { 'DDR5' }; 35 { 'LPDDR5' };
            default { "$($m.SMBIOSMemoryType)" }
        }
    }
}

# --- GPU ---
$gpus = @()
foreach ($g in (Get-CimInstance Win32_VideoController)) {
    $gpus += @{
        Name = $g.Name
        VRAMMB = if ($g.AdapterRAM) { [math]::Round([double]$g.AdapterRAM / 1MB, 0) } else { $null }
        DriverVersion = $g.DriverVersion
        DriverDate = $(
            $gd = ToDate $g $g.DriverDate
            if ($gd) { $gd.ToString('yyyy-MM-dd') } else { $null }
        )
        VideoMode = $g.VideoModeDescription
        Status = $g.Status
    }
}

# --- BIOS / Motherboard ---
$biosInfo = @{
    Manufacturer = $bios.Manufacturer
    Name = $bios.Name
    Version = ($bios.BIOSVersion -join ' / ')
    SMBIOSVersion = $bios.SMBIOSBIOSVersion
    ReleaseDate = $(
        $bd = ToDate $bios $bios.ReleaseDate
        if ($bd) { $bd.ToString('yyyy-MM-dd') } else { $null }
    )
    SerialNumber = $bios.SerialNumber
}

$moboInfo = @{
    Manufacturer = $mobo.Manufacturer
    Product = $mobo.Product
    Version = $mobo.Version
    SerialNumber = $mobo.SerialNumber
}

# --- Disques logiques ---
$disks = @()
foreach ($d in (Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3')) {
    $disks += @{
        DeviceID = $d.DeviceID
        VolumeName = $d.VolumeName
        FileSystem = $d.FileSystem
        SizeGB = ToGB $d.Size
        FreeGB = ToGB $d.FreeSpace
        UsedPercent = if ($d.Size -gt 0) {
            [math]::Round(100 - (100.0 * $d.FreeSpace / $d.Size), 1)
        } else { $null }
    }
}

# --- Disques physiques ---
# On enrichit Win32_DiskDrive (universel) avec MSFT_PhysicalDisk (Get-PhysicalDisk,
# Win8+/2012+) qui expose le VRAI type de media (SSD/HDD), la vitesse de rotation
# et le type de bus (NVMe...). Ces signaux sont bien plus fiables que le seul
# Model pour distinguer SSD et HDD, notamment sur les VM. Get-PhysicalDisk peut
# etre absent (Win7) : dans ce cas on retombe sur le Model uniquement.
$mediaByNum = @{}
try {
    foreach ($p in (Get-PhysicalDisk -ErrorAction SilentlyContinue)) {
        # MediaType peut revenir soit en chaine ('SSD'/'HDD'/'SCM'/'Unspecified'),
        # soit en enum numerique (3=HDD, 4=SSD, 5=SCM) selon la version de Windows.
        # On stringifie AVANT de comparer : [int]$p.MediaType leverait une
        # exception "Cannot convert 'SSD' to Int32" sur les systemes qui
        # renvoient la chaine, ce qui viderait toute la table (bug).
        $mtRaw = "$($p.MediaType)".Trim()
        $mt = switch ($mtRaw) {
            'SSD' { 'SSD' }
            'HDD' { 'HDD' }
            'SCM' { 'SCM' }
            '4'   { 'SSD' }
            '3'   { 'HDD' }
            '5'   { 'SCM' }
            default { 'Unspecified' }
        }
        $spindle = $null
        if ($p.SpindleSpeed -ne $null) { try { $spindle = [int64]$p.SpindleSpeed } catch {} }
        $mediaByNum["$($p.DeviceId)"] = @{
            MediaType = $mt
            SpindleSpeed = $spindle
            BusType = "$($p.BusType)"
        }
    }
} catch {}

$pdisks = @()
foreach ($pd in (Get-CimInstance Win32_DiskDrive)) {
    $media = $mediaByNum["$($pd.Index)"]
    $pdisks += @{
        Model = $pd.Model
        InterfaceType = $pd.InterfaceType
        SizeGB = ToGB $pd.Size
        Partitions = [int]$pd.Partitions
        SerialNumber = ($pd.SerialNumber -replace '\s', '')
        Index = [int]$pd.Index
        MediaType = if ($media) { $media.MediaType } else { $null }
        SpindleSpeed = if ($media) { $media.SpindleSpeed } else { $null }
        BusType = if ($media) { $media.BusType } else { $null }
    }
}

# --- Network adapters (avec IP config) ---
$nics = @()
$cfgs = Get-CimInstance Win32_NetworkAdapterConfiguration -Filter 'IPEnabled=true'
foreach ($c in $cfgs) {
    $adapter = Get-CimInstance Win32_NetworkAdapter -Filter "Index=$($c.Index)"
    $ipv4 = @()
    $ipv6 = @()
    if ($c.IPAddress) {
        foreach ($ip in $c.IPAddress) {
            if ($ip -match ':') { $ipv6 += $ip } else { $ipv4 += $ip }
        }
    }
    $nics += @{
        Name = $adapter.NetConnectionID
        Description = $c.Description
        MAC = $c.MACAddress
        IPv4 = $ipv4
        IPv6 = $ipv6
        Gateway = $c.DefaultIPGateway
        DNSServers = $c.DNSServerSearchOrder
        DHCPEnabled = [bool]$c.DHCPEnabled
        Status = if ($adapter.NetConnectionStatus -eq 2) { 'Connected' } else { "$($adapter.NetConnectionStatus)" }
        SpeedMbps = if ($adapter.Speed) { [math]::Round([double]$adapter.Speed / 1MB, 0) } else { $null }
    }
}

# --- Vega-specific : service HFSQL ---
$hfsql = $null
try {
    $svc = Get-Service | Where-Object {
        $_.Name -like '*HFSQL*' -or $_.DisplayName -like '*HFSQL*' -or
        $_.Name -like '*Manta*' -or $_.DisplayName -like '*Manta*'
    } | Select-Object -First 1
    if ($svc) {
        $hfsql = @{
            Name = $svc.Name
            DisplayName = $svc.DisplayName
            Status = "$($svc.Status)"
            StartType = "$($svc.StartType)"
        }
    }
} catch {}

# --- Vega-specific : partages SMB couvrant des paths Vega ---
# Base universelle : Win32_Share (WMI, present depuis toujours). Le module
# SmbShare (Get-SmbOpenFile / Get-SmbSession) n'existe qu'a partir de
# Windows 8 / Server 2012 : on ne l'utilise que pour enrichir, sans bloquer.
$vegaShares = @()
try {
    $shares = Get-CimInstance Win32_Share | Where-Object {
        $_.Name -notmatch '\$$' -and $_.Path -and (
            $_.Path -like '*VEGAHF*' -or $_.Path -like '*VEGACS*' -or
            $_.Path -like '*vega*' -or $_.Name -like '*VEGA*'
        )
    }
    $hasSmbModule = [bool](Get-Command Get-SmbOpenFile -ErrorAction SilentlyContinue)
    foreach ($s in $shares) {
        $opens = $null
        if ($hasSmbModule) {
            try {
                $opens = (Get-SmbOpenFile -ErrorAction SilentlyContinue |
                          Where-Object { $_.Path -like "$($s.Path)*" }).Count
            } catch {}
        }
        $vegaShares += @{
            Name = $s.Name
            Path = $s.Path
            Description = $s.Description
            CurrentUsers = $null
            OpenFiles = $opens
            ConcurrentUserLimit = $(if ($s.AllowMaximum) { 0 } else { [int]$s.MaximumAllowed })
        }
    }
} catch {}

# --- Vega-specific : detection des .exe Vega courants dans le PATH typique ---
# On scanne C:\VEGAHF\BDD et C:\VEGACS\BDD si presents, niveau 1 (vega*\),
# pour remonter les chemins + versions des vega.exe (V5) et vega6.exe (V6).
$vegaExes = @()
try {
    $bases = @('C:\VEGAHF\BDD', 'C:\VEGACS\BDD')
    $exeNames = @('vega.exe', 'vega6.exe')
    foreach ($base in $bases) {
        if (-not (Test-Path $base)) { continue }
        foreach ($baseFolder in (Get-ChildItem -Path $base -Directory -ErrorAction SilentlyContinue)) {
            foreach ($exeName in $exeNames) {
                $exe = Join-Path $baseFolder.FullName $exeName
                if (Test-Path $exe) {
                    try {
                        $vi = (Get-Item $exe).VersionInfo
                        $vegaExes += @{
                            Path = $exe
                            Version = $vi.FileVersion
                            Description = $vi.FileDescription
                        }
                    } catch {}
                }
            }
        }
    }
} catch {}

# --- Output unique en JSON ---
$payload = @{
    system = $system
    os = $osInfo
    cpu = $cpuInfo
    ram = @{
        # TotalGB = somme reelle des barrettes RAM installees (Win32_PhysicalMemory).
        # On somme DIRECTEMENT depuis CimInstance (Capacity en bytes) car
        # Measure-Object ne fonctionne pas sur des hashtables (besoin d'objets
        # avec proprietes). Differe de TotalPhysicalMemory qui amputait la
        # RAM reservee GPU/BIOS (ex : 16 Go installes, 15.4 Go vu par l'OS).
        TotalGB = $(
            $sumBytes = (Get-CimInstance Win32_PhysicalMemory | Measure-Object -Property Capacity -Sum).Sum
            if ($sumBytes) { [math]::Round([double]$sumBytes / 1GB, 2) }
            else { $system.TotalPhysicalMemoryGB }
        )
        UsableGB = $system.TotalPhysicalMemoryGB
        AvailableMB = ToMB ($os.FreePhysicalMemory * 1KB)
        Modules = $ramModules
    }
    gpus = $gpus
    bios = $biosInfo
    motherboard = $moboInfo
    logicalDisks = $disks
    physicalDisks = $pdisks
    network = $nics
    vega = @{
        HFSQL = $hfsql
        Shares = $vegaShares
        Exes = $vegaExes
    }
}

ToJson $payload
"""


class PcInfoManager:
    def __init__(self, logger):
        self.logger = logger

    @staticmethod
    def _build_script():
        # Adapte le script a l'OS :
        #   - Get-CimInstance (PowerShell 3.0+) -> Get-WmiObject sur Windows 7,
        #     la syntaxe des classes Win32_* et de -Filter etant identique ;
        #   - injection de la fonction ToDate qui absorbe la difference de
        #     format de dates entre les deux cmdlets.
        script = _COLLECT_SCRIPT.replace("Get-CimInstance", cim_cmdlet())
        return ps_json_helper() + ps_date_helper() + script

    def collect(self):
        # Lance le script PS de collecte et parse la sortie JSON.
        self.logger.info("Collecte des informations PC...")
        result = powershell_output(self._build_script())
        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip()
            raise OperationError(f"Collecte PC echouee : {err[:200]}")
        raw = (result.stdout or "").strip()
        if not raw:
            raise OperationError("Sortie vide de la collecte PC.")
        try:
            data = json.loads(raw)
        except Exception as exc:
            raise OperationError(f"Sortie JSON invalide : {exc}")
        self.logger.info("Informations PC collectees.")
        return data
