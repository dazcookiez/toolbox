import threading
import time
import zipfile
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit
from urllib.request import Request, urlopen

from ._common import (
    DOWNLOAD_TIMEOUT,
    DOWNLOAD_USER_AGENT,
    DirectoryIndexParser,
    OperationError,
    STATIC_DOWNLOAD_ITEMS,
    SmtpTestError,
    ToolLogger,
    VEGA6_CHANNELS,
    VEGA6_GROUPS,
)
from . import _download_core
from ._download_core import DownloadCancelled  # re-export pour compatibilite


class DownloadManager:
    def __init__(self, logger, progress_sink=None):
        self.logger = logger
        self.progress_sink = progress_sink
        # Event partage pour signaler une demande d'annulation du batch en cours.
        # Le check est fait entre chaque chunk : un download chunk-sized arrete
        # en max ~150ms (taille buffer 256 KB sur 1 MB/s = ~0.25s par chunk).
        self._cancel_event = threading.Event()

    def request_cancel(self):
        # Signale au batch courant de stopper a la prochaine verification.
        self._cancel_event.set()

    def reset_cancel(self):
        # A appeler avant un nouveau batch pour repartir d'un event clean.
        self._cancel_event.clear()

    def is_cancelled(self):
        return self._cancel_event.is_set()

    def set_progress_sink(self, progress_sink):
        self.progress_sink = progress_sink

    def list_static_items(self):
        return [self._normalize_item(item, dynamic=False) for item in STATIC_DOWNLOAD_ITEMS]

    def list_vega6_items(self):
        # Les paquets VEGA6 sont découverts en direct depuis les index distants
        # (PROD et BETA). Un canal indisponible n'empeche pas d'afficher l'autre.
        items = []
        errors = []
        for group, index_url in VEGA6_CHANNELS:
            try:
                items.extend(self._list_vega6_channel(group, index_url))
            except Exception as exc:
                self.logger.warn(f"Liste {group} indisponible : {exc}")
                errors.append(exc)
        if errors and len(errors) == len(VEGA6_CHANNELS):
            raise errors[0]
        return items

    def _list_vega6_channel(self, group, index_url):
        self.logger.info(f"Actualisation de la liste {group}.")
        request = Request(index_url, headers={"User-Agent": DOWNLOAD_USER_AGENT})
        with urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:
            html = response.read().decode("utf-8", errors="ignore")

        parser = DirectoryIndexParser()
        parser.feed(html)

        items = []
        seen = set()
        id_prefix = group.lower().replace(" ", "_")
        for href in parser.links:
            if not href or href.startswith("?") or href.startswith("/"):
                continue
            if href.endswith("/"):
                continue
            url = urljoin(index_url, href)
            filename = unquote(Path(urlsplit(url).path).name)
            if not filename or filename.lower() == "parent directory":
                continue
            suffix = Path(filename).suffix.lower()
            if suffix not in {".zip", ".exe", ".msi", ".7z"}:
                continue
            if filename.lower() in seen:
                continue
            seen.add(filename.lower())
            items.append(
                self._normalize_item(
                    {
                        "id": f"{id_prefix}_{filename}",
                        "name": filename,
                        "group": group,
                        "url": url,
                    },
                    dynamic=True,
                )
            )

        items.sort(key=lambda item: item["name"].lower(), reverse=True)
        self.logger.info(f"{len(items)} fichier(s) {group} disponible(s) trouvé(s).")
        return items

    def download_file(self, item, target_dir):
        return self.download_files([item], target_dir)[0]

    def download_files(self, items, target_dir):
        # Les téléchargements restent séquentiels pour garder une progression simple.
        # Reset le cancel event au debut du batch (sinon une demande precedente
        # serait deja set et le 1er fichier annulerait immediatement).
        self.reset_cancel()
        target_dir = Path(target_dir).expanduser().resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        normalized_items = [self._normalize_item(item, dynamic=item.get("dynamic", False)) for item in items]
        total_count = len(normalized_items)
        results = []

        for index, normalized in enumerate(normalized_items, start=1):
            if self.is_cancelled():
                raise DownloadCancelled("Telechargement annule par l'utilisateur.")
            results.append(self._download_one(normalized, target_dir, index, total_count))

        return results

    def extract_vega6_packages(self, download_results, vega_root):
        # Décompresse les paquets VEGA6 dans vega.dos\V6 après téléchargement.
        if not download_results:
            return []

        vega_root = Path(vega_root).expanduser().resolve()
        target_v6 = vega_root / "vega.dos" / "V6"
        extracted = []

        for result in download_results:
            if result.get("group") not in VEGA6_GROUPS:
                continue
            archive_path = Path(result["target_path"])
            if archive_path.suffix.lower() != ".zip":
                continue

            target_v6.mkdir(parents=True, exist_ok=True)
            self.logger.info(f"Extraction automatique de {archive_path.name} vers {target_v6}")
            try:
                with zipfile.ZipFile(archive_path, "r") as archive:
                    archive.extractall(target_v6)
            except zipfile.BadZipFile as exc:
                self.logger.error(f"Archive ZIP invalide pour {archive_path.name} : {exc}")
                raise OperationError(f"Impossible d'extraire {archive_path.name}. L'archive est invalide.") from exc
            except Exception as exc:
                self.logger.error(f"Extraction impossible pour {archive_path.name} : {exc}")
                raise OperationError(f"Impossible d'extraire {archive_path.name}. Consultez le journal.") from exc

            extracted.append(
                {
                    "archive_path": str(archive_path),
                    "target_path": str(target_v6),
                    "filename": archive_path.name,
                }
            )
            self.logger.info(f"Extraction terminée : {archive_path.name} -> {target_v6}")

        return extracted

    def _download_one(self, normalized, target_dir, batch_index, batch_count):
        # Écrit le fichier au fil de l'eau et remonte la progression au GUI.
        filename = normalized["filename"]
        url = normalized["url"]
        final_path = target_dir / filename
        # Note : le fichier .part est gere par _download_core (cree, ecrit,
        # renomme atomiquement en final_path).

        self.logger.info(f"Téléchargement de {normalized['name']} vers {final_path}")
        self._emit_progress(
            {
                "name": normalized["name"],
                "filename": filename,
                "target_path": str(final_path),
                "downloaded": 0,
                "total": 0,
                "speed": 0,
                "eta_seconds": None,
                "finished": False,
                "batch_index": batch_index,
                "batch_count": batch_count,
            }
        )

        # Wrapper de progression : le shared module (_download_core) emet
        # (downloaded, total, speed_bps). On traduit en dict pour le GUI
        # (avec ETA, batch index/count, etc.).
        started_at = time.time()
        last_total = [0]
        last_downloaded = [0]

        def _progress(downloaded, total, speed_bps):
            last_total[0] = total
            last_downloaded[0] = downloaded
            # Seuil de stabilite : sous 1 Ko/s soutenu, le smoothed_bps tend
            # asymptotiquement vers 0 (cas HTTP/2 KO -> reconnexion HTTP/1.1)
            # ce qui produit des ETA absurdes (1e15 secondes). On considere
            # le debit comme "instable" et on affiche "--:--" + 0 o/s.
            if speed_bps < 1024:
                speed_bps = 0.0
                eta = None
            else:
                eta = (
                    ((total - downloaded) / speed_bps)
                    if (total and speed_bps > 0) else None
                )
            self._emit_progress({
                "name": normalized["name"],
                "filename": filename,
                "target_path": str(final_path),
                "downloaded": downloaded,
                "total": total,
                "speed": speed_bps,
                "eta_seconds": eta,
                "finished": False,
                "batch_index": batch_index,
                "batch_count": batch_count,
            })

        # Outer retry x3 sur erreurs reseau dures (timeout, reset, HTTPError).
        # Les retries de connexion lente (par-part) sont gerees par
        # _download_core en interne.
        max_attempts = 3
        last_exc = None
        for attempt in range(1, max_attempts + 1):
            try:
                _download_core.download_file(
                    url, final_path,
                    on_progress=_progress,
                    cancel_event=self._cancel_event,
                    logger=self.logger,
                )
                last_exc = None
                break
            except DownloadCancelled:
                raise
            except Exception as exc:
                last_exc = exc
                if attempt < max_attempts:
                    backoff = 1.5 ** attempt  # 1.5s, 2.25s
                    self.logger.warn(
                        f"Echec telechargement {filename} (tentative {attempt}/{max_attempts}) : {exc}. "
                        f"Nouvelle tentative dans {backoff:.1f}s."
                    )
                    time.sleep(backoff)
                else:
                    self.logger.error(
                        f"Telechargement {filename} echoue apres {max_attempts} tentatives : {exc}"
                    )
        if last_exc is not None:
            raise last_exc

        elapsed = max(time.time() - started_at, 0.001)
        downloaded = (
            final_path.stat().st_size if final_path.exists() else last_downloaded[0]
        )
        total = last_total[0] or downloaded
        speed = downloaded / elapsed
        self._emit_progress(
            {
                "name": normalized["name"],
                "filename": filename,
                "target_path": str(final_path),
                "downloaded": downloaded,
                "total": total,
                "speed": speed,
                "eta_seconds": 0,
                "finished": True,
                "batch_index": batch_index,
                "batch_count": batch_count,
            }
        )
        self.logger.info(f"Téléchargement terminé : {final_path}")
        return {
            "name": normalized["name"],
            "id": normalized["id"],
            "group": normalized["group"],
            "filename": filename,
            "target_path": str(final_path),
            "downloaded": downloaded,
            "total": total,
        }

    def _normalize_item(self, item, dynamic=False):
        url = item["url"]
        filename = unquote(Path(urlsplit(url).path).name)
        return {
            "id": item.get("id", filename),
            "name": item.get("name", filename),
            "group": item.get("group", "Telechargement"),
            "url": url,
            "filename": filename,
            "dynamic": bool(dynamic),
        }

    def _emit_progress(self, payload):
        if self.progress_sink:
            self.progress_sink(payload)


