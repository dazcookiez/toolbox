import datetime
import shutil
from collections import Counter
from pathlib import Path

from ._common import (
    CLEAN_ARCHIVE_EXTENSIONS,
    CLEAN_CATEGORY_DIRS,
    CLEAN_CATEGORY_LABELS,
    CLEAN_DELETE_EXTENSIONS,
    CLEAN_DOC_EXCEPTIONS,
    CLEAN_DOC_EXTENSIONS,
    CLEAN_FILE_EXCEPTIONS,
    CLEAN_IMAGE_EXTENSIONS,
    CLEAN_IMAGE_PREFIX_EXCEPTIONS,
    CLEAN_LOG_TEMPLATE,
    CLEAN_OLD_DLL_PREFIXES,
    CLEAN_OLD_EXE_NAMES,
    CLEAN_PDF_EXCEPTIONS,
    CLEAN_ROOT_DIR_MOVES,
    CLEAN_XLS_EXTENSIONS,
    OperationError,
    SmtpTestError,
    ToolLogger,
    remove_existing_path,
    windows_case_insensitive,
)

class CleanVegaManager:
    def __init__(self, logger):
        self.logger = logger

    def validate_root(self, root):
        root = Path(root)
        if not root.exists() or not root.is_dir():
            raise OperationError("Le dossier Vega sélectionné est introuvable.")

        vega_dos = windows_case_insensitive(root, Path("vega.dos"))
        if not vega_dos.exists() or not vega_dos.is_dir():
            raise OperationError("Le dossier vega.dos est introuvable dans cette base.")
        return root, vega_dos

    def analyze(self, root):
        root, vega_dos = self.validate_root(root)
        actions = self._collect_actions(root, vega_dos)
        counts = Counter(action["category"] for action in actions)
        return {
            "root": str(root),
            "vega_dos": str(vega_dos),
            "actions": actions,
            "counts": dict(counts),
            "total_actions": len(actions),
        }

    def execute(self, root, actions=None):
        # Si actions est fourni, n'execute que ces actions (selection utilisateur).
        # Sinon, recalcule l'analyse complete et execute tout (compat retro).
        root, vega_dos = self.validate_root(root)
        if actions is None:
            analysis = self.analyze(root)
            actions = analysis["actions"]
        else:
            counts = Counter(action["category"] for action in actions)
            analysis = {
                "root": str(root),
                "vega_dos": str(vega_dos),
                "actions": list(actions),
                "counts": dict(counts),
                "total_actions": len(actions),
            }
        log_path = root / datetime.datetime.now().strftime(CLEAN_LOG_TEMPLATE)

        self.logger.info("=== START VEGA CLEANUP ===")
        self.logger.info(f"Root: {root}")
        self.logger.info(f"Log:  {log_path}")
        self.logger.info(f"Actions selectionnees: {len(analysis['actions'])}")

        for action in analysis["actions"]:
            self._apply_action(action, vega_dos)

        self.logger.info("=== END VEGA CLEANUP ===")
        self.logger.save(log_path)

        result = dict(analysis)
        result["log_path"] = str(log_path)
        return result

    def _collect_actions(self, root, vega_dos):
        entries = sorted(root.iterdir(), key=lambda entry: (not entry.is_dir(), entry.name.lower()))
        names_lower = {entry.name.lower() for entry in entries}
        actions = []

        for entry in entries:
            if entry.name.lower() == vega_dos.name.lower():
                continue

            if entry.is_dir():
                action = self._classify_directory(entry, vega_dos)
            else:
                action = self._classify_file(entry, vega_dos, names_lower)

            if action:
                actions.append(action)

        return actions

    def _classify_directory(self, entry, vega_dos):
        canonical_name = CLEAN_ROOT_DIR_MOVES.get(entry.name.lower())
        if not canonical_name:
            return None

        return {
            "kind": "move_dir",
            "category": "dir_move",
            "label": CLEAN_CATEGORY_LABELS["dir_move"],
            "source": entry,
            "source_name": entry.name,
            "destination": vega_dos / canonical_name,
            "destination_label": canonical_name,
        }

    def _classify_file(self, entry, vega_dos, names_lower):
        lower_name = entry.name.lower()
        suffix = entry.suffix.lower()

        # Liste blanche : fichiers explicitement proteges du nettoyage.
        # Toujours en lowercase pour le matching insensible a la casse.
        if lower_name in CLEAN_FILE_EXCEPTIONS:
            return None

        if self._should_delete(lower_name, suffix):
            return {
                "kind": "delete",
                "category": "delete",
                "label": CLEAN_CATEGORY_LABELS["delete"],
                "source": entry,
                "source_name": entry.name,
                "reason": f"Suppression {suffix or entry.name}",
            }

        if self._is_old_executable(entry, names_lower):
            return self._move_action("old_exe", entry, vega_dos)

        if self._is_old_library(entry):
            return self._move_action("old_dll", entry, vega_dos)

        if lower_name.startswith("resultat"):
            return self._move_action("fdj", entry, vega_dos)

        if self._is_copy_file(lower_name):
            return self._move_action("copies", entry, vega_dos)

        if suffix in CLEAN_ARCHIVE_EXTENSIONS:
            return self._move_action("archives", entry, vega_dos)

        if suffix == ".pdf" and lower_name not in CLEAN_PDF_EXCEPTIONS:
            return self._move_action("pdf", entry, vega_dos)

        if suffix in CLEAN_DOC_EXTENSIONS and lower_name not in CLEAN_DOC_EXCEPTIONS:
            return self._move_action("docs", entry, vega_dos)

        if suffix in CLEAN_XLS_EXTENSIONS:
            return self._move_action("xls", entry, vega_dos)

        if suffix in CLEAN_IMAGE_EXTENSIONS and not lower_name.startswith(CLEAN_IMAGE_PREFIX_EXCEPTIONS):
            return self._move_action("images", entry, vega_dos)

        return None

    def _move_action(self, category, entry, vega_dos):
        target_dir_name = CLEAN_CATEGORY_DIRS[category]
        return {
            "kind": "move_file",
            "category": category,
            "label": CLEAN_CATEGORY_LABELS[category],
            "source": entry,
            "source_name": entry.name,
            "destination": vega_dos / target_dir_name / entry.name,
            "destination_label": target_dir_name,
        }

    def _should_delete(self, lower_name, suffix):
        if suffix in CLEAN_DELETE_EXTENSIONS:
            return True
        return lower_name == "derndoss.dat"

    def _is_old_executable(self, entry, _names_lower):
        lower_name = entry.name.lower()
        if entry.suffix.lower() != ".exe":
            return False

        if lower_name in CLEAN_OLD_EXE_NAMES:
            return True

        if lower_name.startswith("moul"):
            return True

        return False

    def _is_old_library(self, entry):
        lower_name = entry.name.lower()
        if lower_name == "vega.rep":
            return True
        if lower_name == "wdoutil.wdk":
            return False
        return any(lower_name.startswith(prefix) for prefix in CLEAN_OLD_DLL_PREFIXES) and (
            lower_name.endswith(".dll") or lower_name.endswith(".wdk")
        )

    def _is_copy_file(self, lower_name):
        if "~" in lower_name:
            return True
        if lower_name.startswith("fact") and "-" in lower_name:
            return True
        if lower_name.startswith("polic") and "-" in lower_name:
            return True
        return False

    def _apply_action(self, action, vega_dos):
        source = action["source"]
        destination = action.get("destination")

        if not source.exists():
            self.logger.info(f"Ignoré: '{action['source_name']}' n'existe plus.")
            return

        if action["kind"] == "move_dir":
            vega_dos.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                remove_existing_path(destination)
            shutil.move(str(source), str(destination))
            self.logger.info(f"Dossier déplacé: '{action['source_name']}' -> '{destination.name}'")
            return

        if action["kind"] == "move_file":
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                remove_existing_path(destination)
            shutil.move(str(source), str(destination))
            self.logger.info(f"Déplacé: '{action['source_name']}' -> '{action['destination_label']}'")
            return

        if action["kind"] == "delete":
            remove_existing_path(source)
            self.logger.info(f"Supprimé: '{action['source_name']}'")


