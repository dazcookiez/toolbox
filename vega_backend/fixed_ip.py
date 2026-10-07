import ctypes

from ._common import (
    OperationError,
    SmtpTestError,
    ToolLogger,
    command_output,
    gateway_matches_subnet,
    is_admin,
    is_link_local_ipv4,
    prefix_to_mask,
)

# --- Enumeration rapide des cartes reseau via GetAdaptersAddresses ---
# Remplace les appels PowerShell (cout de demarrage 400-800 ms) par une
# lecture native de l'API iphlpapi qui retourne tout en quelques millisecondes.
from ctypes import wintypes as _wintypes

_AF_INET = 2
_GAA_FLAG_INCLUDE_GATEWAYS = 0x0080
_GAA_FLAG_INCLUDE_PREFIX = 0x0010
_GAA_FLAG_SKIP_ANYCAST = 0x0002
_GAA_FLAG_SKIP_MULTICAST = 0x0004
_IP_ADAPTER_DHCP_ENABLED = 0x00000004
_ERROR_BUFFER_OVERFLOW = 111
_MAX_ADAPTER_ADDRESS_LENGTH = 8

_OPER_STATUS_LABELS = {
    1: "Up",
    2: "Down",
    3: "Testing",
    4: "Unknown",
    5: "Dormant",
    6: "NotPresent",
    7: "LowerLayerDown",
}


class _SOCKET_ADDRESS(ctypes.Structure):
    _fields_ = [
        ("lpSockaddr", ctypes.c_void_p),
        ("iSockaddrLength", ctypes.c_int),
    ]


class _IP_ADAPTER_UNICAST_ADDRESS(ctypes.Structure):
    pass


_IP_ADAPTER_UNICAST_ADDRESS._fields_ = [
    ("Length", ctypes.c_ulong),
    ("Flags", _wintypes.DWORD),
    ("Next", ctypes.POINTER(_IP_ADAPTER_UNICAST_ADDRESS)),
    ("Address", _SOCKET_ADDRESS),
    ("PrefixOrigin", ctypes.c_int),
    ("SuffixOrigin", ctypes.c_int),
    ("DadState", ctypes.c_int),
    ("ValidLifetime", ctypes.c_ulong),
    ("PreferredLifetime", ctypes.c_ulong),
    ("LeaseLifetime", ctypes.c_ulong),
    ("OnLinkPrefixLength", ctypes.c_uint8),
]


class _IP_ADAPTER_GATEWAY_ADDRESS(ctypes.Structure):
    pass


_IP_ADAPTER_GATEWAY_ADDRESS._fields_ = [
    ("Length", ctypes.c_ulong),
    ("Reserved", _wintypes.DWORD),
    ("Next", ctypes.POINTER(_IP_ADAPTER_GATEWAY_ADDRESS)),
    ("Address", _SOCKET_ADDRESS),
]


class _IP_ADAPTER_DNS_SERVER_ADDRESS(ctypes.Structure):
    pass


_IP_ADAPTER_DNS_SERVER_ADDRESS._fields_ = [
    ("Length", ctypes.c_ulong),
    ("Reserved", _wintypes.DWORD),
    ("Next", ctypes.POINTER(_IP_ADAPTER_DNS_SERVER_ADDRESS)),
    ("Address", _SOCKET_ADDRESS),
]


class _IP_ADAPTER_PREFIX(ctypes.Structure):
    pass


_IP_ADAPTER_PREFIX._fields_ = [
    ("Length", ctypes.c_ulong),
    ("Flags", _wintypes.DWORD),
    ("Next", ctypes.POINTER(_IP_ADAPTER_PREFIX)),
    ("Address", _SOCKET_ADDRESS),
    ("PrefixLength", ctypes.c_ulong),
]


class _IP_ADAPTER_ANY_ADDRESS(ctypes.Structure):
    pass


_IP_ADAPTER_ANY_ADDRESS._fields_ = [
    ("Length", ctypes.c_ulong),
    ("Flags", _wintypes.DWORD),
    ("Next", ctypes.POINTER(_IP_ADAPTER_ANY_ADDRESS)),
    ("Address", _SOCKET_ADDRESS),
]


class _IP_ADAPTER_WINS_SERVER_ADDRESS(ctypes.Structure):
    pass


_IP_ADAPTER_WINS_SERVER_ADDRESS._fields_ = [
    ("Length", ctypes.c_ulong),
    ("Reserved", _wintypes.DWORD),
    ("Next", ctypes.POINTER(_IP_ADAPTER_WINS_SERVER_ADDRESS)),
    ("Address", _SOCKET_ADDRESS),
]


class _IP_ADAPTER_ADDRESSES(ctypes.Structure):
    pass


_IP_ADAPTER_ADDRESSES._fields_ = [
    ("Length", ctypes.c_ulong),
    ("IfIndex", _wintypes.DWORD),
    ("Next", ctypes.POINTER(_IP_ADAPTER_ADDRESSES)),
    ("AdapterName", ctypes.c_char_p),
    ("FirstUnicastAddress", ctypes.POINTER(_IP_ADAPTER_UNICAST_ADDRESS)),
    ("FirstAnycastAddress", ctypes.POINTER(_IP_ADAPTER_ANY_ADDRESS)),
    ("FirstMulticastAddress", ctypes.POINTER(_IP_ADAPTER_ANY_ADDRESS)),
    ("FirstDnsServerAddress", ctypes.POINTER(_IP_ADAPTER_DNS_SERVER_ADDRESS)),
    ("DnsSuffix", ctypes.c_wchar_p),
    ("Description", ctypes.c_wchar_p),
    ("FriendlyName", ctypes.c_wchar_p),
    ("PhysicalAddress", ctypes.c_ubyte * _MAX_ADAPTER_ADDRESS_LENGTH),
    ("PhysicalAddressLength", ctypes.c_ulong),
    ("Flags", _wintypes.DWORD),
    ("Mtu", ctypes.c_ulong),
    ("IfType", ctypes.c_ulong),
    ("OperStatus", ctypes.c_int),
    ("Ipv6IfIndex", _wintypes.DWORD),
    ("ZoneIndices", ctypes.c_ulong * 16),
    ("FirstPrefix", ctypes.POINTER(_IP_ADAPTER_PREFIX)),
    ("TransmitLinkSpeed", ctypes.c_uint64),
    ("ReceiveLinkSpeed", ctypes.c_uint64),
    ("FirstWinsServerAddress", ctypes.POINTER(_IP_ADAPTER_WINS_SERVER_ADDRESS)),
    ("FirstGatewayAddress", ctypes.POINTER(_IP_ADAPTER_GATEWAY_ADDRESS)),
]


def _sockaddr_to_ipv4(sockaddr_ptr):
    if not sockaddr_ptr:
        return ""
    buf = (ctypes.c_ubyte * 8).from_address(sockaddr_ptr)
    family = buf[0] | (buf[1] << 8)
    if family != _AF_INET:
        return ""
    return f"{buf[4]}.{buf[5]}.{buf[6]}.{buf[7]}"


def enumerate_adapters():
    # Un seul appel natif remplace l'ensemble des scripts PowerShell de lecture.
    iphlpapi = ctypes.WinDLL("iphlpapi")
    GetAdaptersAddresses = iphlpapi.GetAdaptersAddresses
    GetAdaptersAddresses.argtypes = [
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.POINTER(_IP_ADAPTER_ADDRESSES),
        ctypes.POINTER(ctypes.c_ulong),
    ]
    GetAdaptersAddresses.restype = ctypes.c_ulong

    flags = (
        _GAA_FLAG_INCLUDE_GATEWAYS
        | _GAA_FLAG_INCLUDE_PREFIX
        | _GAA_FLAG_SKIP_ANYCAST
        | _GAA_FLAG_SKIP_MULTICAST
    )

    size = ctypes.c_ulong(15000)
    buffer = ctypes.create_string_buffer(size.value)
    rc = GetAdaptersAddresses(
        _AF_INET, flags, None,
        ctypes.cast(buffer, ctypes.POINTER(_IP_ADAPTER_ADDRESSES)),
        ctypes.byref(size),
    )
    if rc == _ERROR_BUFFER_OVERFLOW:
        buffer = ctypes.create_string_buffer(size.value)
        rc = GetAdaptersAddresses(
            _AF_INET, flags, None,
            ctypes.cast(buffer, ctypes.POINTER(_IP_ADAPTER_ADDRESSES)),
            ctypes.byref(size),
        )
    if rc != 0:
        raise OperationError(f"Lecture des cartes réseau impossible (code Win32 {rc}).")

    adapters = []
    cursor = ctypes.cast(buffer, ctypes.POINTER(_IP_ADAPTER_ADDRESSES))
    while cursor:
        entry = cursor.contents
        alias = entry.FriendlyName or ""
        description = entry.Description or ""
        status = _OPER_STATUS_LABELS.get(entry.OperStatus, "Unknown")
        dhcp_enabled = bool(entry.Flags & _IP_ADAPTER_DHCP_ENABLED)

        primary = None
        fallback = None
        unicast = entry.FirstUnicastAddress
        while unicast:
            u = unicast.contents
            addr = _sockaddr_to_ipv4(u.Address.lpSockaddr)
            if addr:
                candidate = (addr, int(u.OnLinkPrefixLength))
                if addr.startswith("169.254."):
                    fallback = fallback or candidate
                else:
                    primary = primary or candidate
            unicast = u.Next
        ipv4, prefix_length = primary or fallback or ("", 0)

        gateway = ""
        gw = entry.FirstGatewayAddress
        while gw:
            addr = _sockaddr_to_ipv4(gw.contents.Address.lpSockaddr)
            if addr:
                gateway = addr
                break
            gw = gw.contents.Next

        dns_servers = []
        dns = entry.FirstDnsServerAddress
        while dns:
            addr = _sockaddr_to_ipv4(dns.contents.Address.lpSockaddr)
            if addr:
                dns_servers.append(addr)
            dns = dns.contents.Next

        adapters.append({
            "alias": alias,
            "interface_index": int(entry.IfIndex),
            "description": description,
            "status": status,
            "dhcp_enabled": dhcp_enabled,
            "ipv4": ipv4,
            "prefix_length": prefix_length,
            "gateway": gateway,
            "dns_servers": dns_servers,
        })
        cursor = entry.Next

    return adapters


class FixedIPManager:
    def __init__(self, logger):
        self.logger = logger

    def list_adapters(self):
        # Remonte les cartes utilisables avec leur état courant.
        adapters = []
        for item in enumerate_adapters():
            alias = item["alias"]
            if not alias:
                continue
            if item["status"] in ("Disabled", "NotPresent"):
                continue
            adapters.append(
                {
                    "alias": alias,
                    "interface_index": item["interface_index"],
                    "description": item["description"],
                    "status": item["status"],
                    "dhcp_enabled": item["dhcp_enabled"],
                    "ipv4": item["ipv4"],
                    "label": self._adapter_label(alias, item["ipv4"], item["dhcp_enabled"], item["status"]),
                }
            )

        adapters.sort(key=lambda item: (0 if item["status"].lower() == "up" else 1, item["alias"].lower()))
        return adapters

    def get_configuration(self, alias):
        alias = (alias or "").strip()
        if not alias:
            raise OperationError("Aucune carte réseau n'a été sélectionnée.")

        for item in enumerate_adapters():
            if item["alias"] != alias:
                continue
            prefix_length = item["prefix_length"]
            config = {
                "alias": alias,
                "interface_index": item["interface_index"],
                "description": item["description"],
                "status": item["status"],
                "dhcp_enabled": item["dhcp_enabled"],
                "ipv4": item["ipv4"],
                "prefix_length": prefix_length,
                "subnet_mask": prefix_to_mask(prefix_length) if prefix_length else "",
                "gateway": item["gateway"],
                "dns_servers": list(item["dns_servers"]),
            }
            config["link_local"] = is_link_local_ipv4(config["ipv4"])
            config["gateway_ok"] = not config["gateway"] or gateway_matches_subnet(
                config["ipv4"], config["prefix_length"], config["gateway"]
            )
            return config

        raise OperationError(f"Carte réseau '{alias}' introuvable.")

    def apply_static(self, alias):
        if not is_admin():
            raise OperationError("Le passage en IP fixe demande un lancement en administrateur.")

        alias = (alias or "").strip()
        if not alias:
            raise OperationError("Aucune carte réseau n'a été sélectionnée.")

        config = self.get_configuration(alias)
        ipv4 = config.get("ipv4") or ""
        prefix_length = int(config.get("prefix_length") or 0)
        gateway = config.get("gateway") or ""
        dns_servers = config.get("dns_servers") or []

        if not ipv4 or not prefix_length:
            raise OperationError("Aucune adresse IPv4 exploitable n'a été trouvée sur cette carte.")

        if config.get("link_local"):
            raise OperationError(
                "La carte utilise une adresse auto-attribuée (169.254.x.x). "
                "Reconnectez-la au réseau avant de la définir en IP fixe."
            )

        if gateway and not gateway_matches_subnet(ipv4, prefix_length, gateway):
            raise OperationError(
                f"La passerelle {gateway} n'est pas cohérente avec l'adresse {ipv4}/{prefix_length}. "
                "Vérifiez la connexion réseau avant de définir cette IP en fixe."
            )

        self.logger.info(f"Passage en IP fixe de la carte {alias}")
        self._apply_static_netsh(config)

        self.logger.info(f"Carte {alias} définie en IP fixe avec les paramètres actuels.")
        return self.get_configuration(alias)

    def set_dhcp(self, alias):
        if not is_admin():
            raise OperationError("Le retour en DHCP demande un lancement en administrateur.")

        alias = (alias or "").strip()
        if not alias:
            raise OperationError("Aucune carte réseau n'a été sélectionnée.")

        self.logger.info(f"Retour en DHCP de la carte {alias}")
        self._set_dhcp_netsh(self.get_configuration(alias))

        self.logger.info(f"Carte {alias} repassée en DHCP.")
        return self.get_configuration(alias)

    def _adapter_label(self, alias, ipv4, dhcp_enabled, status):
        parts = [alias]
        if ipv4:
            parts.append(ipv4)
        parts.append("DHCP" if dhcp_enabled else "IP fixe")
        if status:
            parts.append(status)
        return " | ".join(parts)

    def _run_netsh(self, args, error_message):
        result = command_output(["netsh", *args])
        if result.returncode != 0:
            details = result.stderr.strip() or result.stdout.strip() or error_message
            self.logger.error(details)
            raise OperationError(error_message)
        return result

    def _apply_static_netsh(self, config):
        interface_name = str(config.get("alias") or config.get("interface_index") or "").strip()
        if not interface_name:
            raise OperationError("Interface réseau introuvable.")

        mask = config.get("subnet_mask") or prefix_to_mask(config.get("prefix_length") or 0)
        if not mask:
            raise OperationError("Masque réseau introuvable.")

        gateway = config.get("gateway") or "none"
        ipv4 = config.get("ipv4") or ""
        dns_servers = list(config.get("dns_servers") or [])

        self._run_netsh(
            [
                "interface",
                "ipv4",
                "set",
                "address",
                f"name={interface_name}",
                "source=static",
                f"address={ipv4}",
                f"mask={mask}",
                f"gateway={gateway}",
                "store=persistent",
            ],
            "Impossible de définir l'IP fixe.",
        )

        if dns_servers:
            self._run_netsh(
                [
                    "interface",
                    "ipv4",
                    "set",
                    "dnsservers",
                    f"name={interface_name}",
                    "source=static",
                    f"address={dns_servers[0]}",
                    "validate=no",
                ],
                "Impossible de définir le DNS principal.",
            )
            for index, dns_server in enumerate(dns_servers[1:], start=2):
                self._run_netsh(
                    [
                        "interface",
                        "ipv4",
                        "add",
                        "dnsservers",
                        f"name={interface_name}",
                        f"address={dns_server}",
                        f"index={index}",
                        "validate=no",
                    ],
                    "Impossible d'ajouter un DNS secondaire.",
                )
        else:
            self._run_netsh(
                [
                    "interface",
                    "ipv4",
                    "set",
                    "dnsservers",
                    f"name={interface_name}",
                    "source=static",
                    "address=none",
                    "validate=no",
                ],
                "Impossible de réinitialiser les DNS statiques.",
            )

    def _set_dhcp_netsh(self, config):
        interface_name = str(config.get("alias") or config.get("interface_index") or "").strip()
        if not interface_name:
            raise OperationError("Interface réseau introuvable.")

        self._run_netsh(
            [
                "interface",
                "ipv4",
                "set",
                "address",
                f"name={interface_name}",
                "source=dhcp",
            ],
            "Impossible de repasser l'adresse IP en DHCP.",
        )
        self._run_netsh(
            [
                "interface",
                "ipv4",
                "set",
                "dnsservers",
                f"name={interface_name}",
                "source=dhcp",
            ],
            "Impossible de repasser les DNS en DHCP.",
        )

