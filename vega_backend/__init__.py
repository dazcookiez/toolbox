"""Package vega_backend.

Re-export de l'API publique consommee par vega_gui. Les modules internes
(_common, clean, migration, ...) sont accessibles via attribut mais peuvent
changer sans preavis.
"""
from ._common import (
    OperationError,
    SmtpTestError,
    ToolLogger,
    is_admin,
    default_download_dir,
)
from .audit import AuditLogger
from .clean import CleanVegaManager
from .pc_info import PcInfoManager
from .migration import MigrationManager
from .firewall import FirewallManager
from .fixed_ip import FixedIPManager
from .downloads import DownloadManager
from .impressions import ImpressionsManager
from .notepad import NotepadInstallerManager
from .retailforce import RETAILFORCE_TARGET_VERSION, RetailForceManager
from .smtp import SmtpTestManager
from .updater import UpdateChecker
from .defender import ensure_exclusions as ensure_defender_exclusions

__all__ = [
    "OperationError",
    "SmtpTestError",
    "ToolLogger",
    "is_admin",
    "default_download_dir",
    "AuditLogger",
    "CleanVegaManager",
    "PcInfoManager",
    "MigrationManager",
    "FirewallManager",
    "FixedIPManager",
    "DownloadManager",
    "ImpressionsManager",
    "NotepadInstallerManager",
    "RETAILFORCE_TARGET_VERSION",
    "RetailForceManager",
    "SmtpTestManager",
    "UpdateChecker",
    "ensure_defender_exclusions",
]
