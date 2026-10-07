"""Coeur des telechargements HTTP : multi-part parallele (HTTP Range)
avec auto-retry des connexions lentes. Utilise par hfsql_installer.py et
downloads.py. Pas de dependance externe (urllib stdlib).

Concepts :
  - Multi-part : decoupe le fichier en N segments contigus, telecharge en
    parallele via 'Range: bytes=<start>-<end>'. Gain typique 2-4x quand le
    mirror throttle per-connection.
  - Auto-retry : chaque worker mesure sa vitesse sur fenetre glissante.
    Sous le seuil pendant SLOW_WINDOW_SEC -> kill socket + Range reprise
    sur les octets restants avec fresh TCP (Connection: close). Max
    RETRIES_PER_PART tentatives.
  - Fallback single-stream : si Accept-Ranges absent OU fichier trop petit.
  - Cancel : threading.Event optionnel verifie entre chaque chunk.
  - on_progress : callable(downloaded:int, total:int, speed_bps:float).
    Appelle ~tous les 0.5s. Caller formate l'affichage utilisateur.
"""
import threading
import time
from pathlib import Path
from urllib.request import Request, urlopen

from ._common import DOWNLOAD_USER_AGENT, OperationError

# HTTP/2 optionnel via httpx[http2]. Si indisponible (ou si httpx echoue
# au runtime), on tombe sur urllib (HTTP/1.1). httpx multiplexe les 4
# range requests sur 1 SEULE connexion TCP avec un seul slow-start +
# un seul handshake TLS - gain reel face a 4 TCP separees.
try:
    import httpx
    _HTTPX_AVAILABLE = True
except ImportError:
    httpx = None
    _HTTPX_AVAILABLE = False


# --- Tuning par defaut. Override possible via parametre dans download_file().
DEFAULT_NUM_PARTS = 4
# Multi-part desactive par defaut : sur reseau client (proxy/firewall stricts),
# les 4 connexions paralleles + le HTTP/2 negocie puis ferme par le serveur
# causent plus de problemes qu'ils n'apportent (debit instable + ETA pourries
# + reconnexions en boucle). Le single-stream est plus robuste. Seuil place
# tres haut pour neutraliser sans supprimer le code (cas d'usage potentiel
# en LAN si on rebascule un jour). Ancien seuil : 20 Mo.
DEFAULT_MULTIPART_THRESHOLD = 999 * 1024 * 1024 * 1024  # 999 Go = jamais
DEFAULT_SLOW_THRESHOLD_BPS = 300 * 1024  # 300 Ko/s
DEFAULT_SLOW_WINDOW_SEC = 10
DEFAULT_RETRIES_PER_PART = 3
DEFAULT_PROBE_TIMEOUT_SEC = 30
DEFAULT_READ_TIMEOUT_SEC = 300
CHUNK_SIZE = 256 * 1024  # 256 Ko


class DownloadCancelled(OperationError):
    # Signale une annulation utilisateur (cancel_event set).
    pass


class _SlowConnection(Exception):
    # Sentinel control flow : connexion sous le seuil -> retry fresh TCP.
    pass


def probe_url(url, logger=None, user_agent=DOWNLOAD_USER_AGENT,
              timeout=DEFAULT_PROBE_TIMEOUT_SEC):
    # HEAD pour Content-Length + Accept-Ranges. Retourne (length, range_ok).
    # En cas d'echec retourne (0, False) -> caller fallback sans probe.
    try:
        req = Request(url, method="HEAD", headers={"User-Agent": user_agent})
        with urlopen(req, timeout=timeout) as resp:
            length = int(resp.headers.get("Content-Length", "0") or 0)
            accept = (resp.headers.get("Accept-Ranges", "") or "").lower()
            return length, accept == "bytes"
    except Exception as exc:
        if logger:
            logger.warn(f"HEAD echoue ({exc}), fallback sans probe.")
        return 0, False


def download_file(
    url, target_path, *,
    on_progress=None,
    cancel_event=None,
    logger=None,
    user_agent=DOWNLOAD_USER_AGENT,
    num_parts=DEFAULT_NUM_PARTS,
    multipart_threshold=DEFAULT_MULTIPART_THRESHOLD,
    slow_threshold_bps=DEFAULT_SLOW_THRESHOLD_BPS,
    slow_window_sec=DEFAULT_SLOW_WINDOW_SEC,
    retries_per_part=DEFAULT_RETRIES_PER_PART,
    timeout=DEFAULT_READ_TIMEOUT_SEC,
):
    # Pipeline complet :
    #   1. HEAD pour Content-Length + Accept-Ranges
    #   2. Si Range OK + fichier >= seuil : multi-part parallele
    #   3. Sinon fallback single-stream
    # Retourne target_path apres rename atomique du .part. Leve
    # OperationError ou DownloadCancelled.
    target_path = Path(target_path)
    notify = on_progress or (lambda *_a, **_k: None)
    log = logger

    content_length, supports_ranges = probe_url(url, logger=log, user_agent=user_agent)

    if (supports_ranges and content_length
            and content_length >= multipart_threshold):
        if log:
            log.info(
                f"Multi-part : {content_length // (1024*1024)} Mo, "
                f"{num_parts} connexions paralleles"
            )
        _download_multipart(
            url, target_path, content_length,
            num_parts=num_parts, on_progress=notify,
            cancel_event=cancel_event, logger=log,
            user_agent=user_agent,
            slow_threshold_bps=slow_threshold_bps,
            slow_window_sec=slow_window_sec,
            retries_per_part=retries_per_part,
            timeout=timeout,
        )
    else:
        if log:
            if content_length:
                log.info(
                    f"Telechargement single-stream : "
                    f"{content_length // (1024*1024)} Mo, 1 connexion HTTP/1.1"
                )
            else:
                log.info("Telechargement single-stream (taille inconnue).")
        _download_single(
            url, target_path, content_length,
            on_progress=notify,
            cancel_event=cancel_event, logger=log,
            user_agent=user_agent, timeout=timeout,
        )
    return target_path


def _check_cancel(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise DownloadCancelled("Telechargement annule par l'utilisateur.")


def _download_multipart(
    url, target, content_length, *,
    num_parts, on_progress, cancel_event, logger,
    user_agent, slow_threshold_bps, slow_window_sec,
    retries_per_part, timeout,
):
    tmp_part = target.with_suffix(target.suffix + ".part")
    # Pre-alloue le fichier final
    with open(tmp_part, "wb") as f:
        f.truncate(content_length)

    # Partition contigue
    part_size = content_length // num_parts
    ranges = []
    for i in range(num_parts):
        start = i * part_size
        end = (start + part_size - 1) if i < num_parts - 1 else (content_length - 1)
        ranges.append((start, end))

    bytes_downloaded = [0]
    lock = threading.Lock()
    errors = []
    cancelled = [False]

    # Client HTTP/2 partage : si dispo et URL https, httpx multiplexe les
    # streams sur 1 SEULE connexion TCP (1 seul slow-start, 1 seul TLS
    # handshake). Si httpx absent OU si serveur ne negocie pas h2 via ALPN
    # -> httpx tombe sur HTTP/1.1, equivalent au comportement urllib.
    # Fallback gracieux : si httpx echoue au runtime, worker bascule sur
    # urllib (Connection: close) pour le reste de sa part.
    h2_client = None
    if _HTTPX_AVAILABLE and url.lower().startswith("https://"):
        try:
            h2_client = httpx.Client(
                http2=True,
                timeout=httpx.Timeout(timeout, connect=30),
                limits=httpx.Limits(
                    max_keepalive_connections=1,
                    max_connections=num_parts + 1,
                ),
                headers={"User-Agent": user_agent},
                follow_redirects=True,
            )
        except Exception:
            h2_client = None

    def _stream_httpx(part_index, current_offset, end):
        # Streame une part via httpx (HTTP/2 si negocie). Retourne
        # (done_bool, new_current_offset, slow_exc_or_None).
        # Leve une exception en cas d'erreur dure -> caller bascule urllib.
        window_t0 = time.time()
        window_b0 = current_offset
        headers = {"Range": f"bytes={current_offset}-{end}"}
        with h2_client.stream("GET", url, headers=headers) as resp:
            if resp.status_code not in (200, 206):
                raise OperationError(
                    f"Part {part_index}: HTTP {resp.status_code} (Range refuse)"
                )
            with open(tmp_part, "rb+") as f:
                f.seek(current_offset)
                for chunk in resp.iter_bytes(CHUNK_SIZE):
                    if cancel_event is not None and cancel_event.is_set():
                        cancelled[0] = True
                        return True, current_offset, None
                    if not chunk:
                        continue
                    f.write(chunk)
                    current_offset += len(chunk)
                    with lock:
                        bytes_downloaded[0] += len(chunk)
                    now = time.time()
                    elapsed = now - window_t0
                    if elapsed >= slow_window_sec:
                        avg = (current_offset - window_b0) / elapsed
                        if avg < slow_threshold_bps:
                            return False, current_offset, _SlowConnection(
                                f"{avg/1024:.0f} Ko/s sous "
                                f"{slow_threshold_bps/1024:.0f} Ko/s "
                                f"pendant {slow_window_sec}s"
                            )
                        window_t0 = now
                        window_b0 = current_offset
        return True, current_offset, None

    def _stream_urllib(part_index, current_offset, end):
        # Streame une part via urllib HTTP/1.1, fresh TCP a chaque appel.
        window_t0 = time.time()
        window_b0 = current_offset
        req = Request(url, headers={
            "User-Agent": user_agent,
            "Range": f"bytes={current_offset}-{end}",
            "Connection": "close",
        })
        with urlopen(req, timeout=timeout) as resp:
            if resp.status not in (200, 206):
                raise OperationError(
                    f"Part {part_index}: HTTP {resp.status} (Range refuse)"
                )
            with open(tmp_part, "rb+") as f:
                f.seek(current_offset)
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        cancelled[0] = True
                        return True, current_offset, None
                    chunk = resp.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    f.write(chunk)
                    current_offset += len(chunk)
                    with lock:
                        bytes_downloaded[0] += len(chunk)
                    now = time.time()
                    elapsed = now - window_t0
                    if elapsed >= slow_window_sec:
                        avg = (current_offset - window_b0) / elapsed
                        if avg < slow_threshold_bps:
                            return False, current_offset, _SlowConnection(
                                f"{avg/1024:.0f} Ko/s sous "
                                f"{slow_threshold_bps/1024:.0f} Ko/s "
                                f"pendant {slow_window_sec}s"
                            )
                        window_t0 = now
                        window_b0 = current_offset
        return True, current_offset, None

    def worker(part_index, start, end):
        current_offset = start
        retries = 0
        use_httpx = h2_client is not None
        while current_offset <= end and retries <= retries_per_part:
            try:
                fn = _stream_httpx if use_httpx else _stream_urllib
                done, current_offset, slow = fn(part_index, current_offset, end)
                if done:
                    return
                # Connexion stagnante -> retry (avec nouvelle connexion urllib
                # forcee : on bascule en HTTP/1.1 pour avoir fresh TCP)
                retries += 1
                if logger:
                    proto = "HTTP/2" if use_httpx else "HTTP/1.1"
                    logger.warn(
                        f"Part {part_index} lente ({slow}, {proto}) - retry "
                        f"{retries}/{retries_per_part} @offset {current_offset}"
                    )
                use_httpx = False  # fresh TCP via urllib pour la reprise
                time.sleep(0.5)
            except Exception as exc:
                if use_httpx:
                    # httpx en erreur -> on retente sur urllib pour le reste
                    if logger:
                        logger.warn(
                            f"Part {part_index} HTTP/2 KO ({exc}) - bascule HTTP/1.1"
                        )
                    use_httpx = False
                    continue
                errors.append(
                    f"Part {part_index} [{start}-{end}] @{current_offset}: {exc}"
                )
                return
        if current_offset <= end:
            errors.append(
                f"Part {part_index}: max retries depasse, "
                f"reste {end - current_offset + 1} octets."
            )

    threads = [
        threading.Thread(target=worker, args=(i, s, e), daemon=True)
        for i, (s, e) in enumerate(ranges)
    ]
    t_start = time.time()
    for t in threads:
        t.start()

    last_bytes = 0
    last_time = t_start
    smoothed_bps = 0.0
    while any(t.is_alive() for t in threads):
        time.sleep(0.5)
        with lock:
            current = bytes_downloaded[0]
        now = time.time()
        dt = now - last_time
        if dt > 0:
            instant = (current - last_bytes) / dt
            smoothed_bps = 0.6 * instant + 0.4 * smoothed_bps
        try:
            on_progress(current, content_length, smoothed_bps)
        except Exception:
            pass
        last_bytes = current
        last_time = now

    for t in threads:
        t.join()

    # Cleanup client HTTP/2 partage si on l'avait active
    if h2_client is not None:
        try:
            h2_client.close()
        except Exception:
            pass

    if cancelled[0]:
        try:
            tmp_part.unlink()
        except OSError:
            pass
        raise DownloadCancelled("Telechargement annule par l'utilisateur.")

    if errors:
        try:
            tmp_part.unlink()
        except OSError:
            pass
        raise OperationError(
            "Telechargement multi-part en echec : " + " | ".join(errors)
        )

    actual = tmp_part.stat().st_size
    if actual != content_length:
        try:
            tmp_part.unlink()
        except OSError:
            pass
        raise OperationError(
            f"Taille telecharge ({actual}) != Content-Length ({content_length})"
        )

    tmp_part.replace(target)
    duration = time.time() - t_start
    avg = (content_length / duration) if duration > 0 else 0.0
    if logger:
        proto_label = "HTTP/2 (1 TCP, streams multiplexes)" if h2_client else "HTTP/1.1 (TCP par part)"
        logger.info(
            f"Telechargement multi-part termine : "
            f"{content_length // (1024*1024)} Mo en {duration:.1f}s "
            f"({avg/(1024*1024):.1f} Mo/s, {num_parts} parts, {proto_label})"
        )


def _download_single(
    url, target, content_length, *,
    on_progress, cancel_event, logger,
    user_agent, timeout,
):
    req = Request(url, headers={"User-Agent": user_agent, "Connection": "close"})
    tmp_part = target.with_suffix(target.suffix + ".part")
    t_start = time.time()
    last_bytes = 0
    last_time = t_start
    smoothed_bps = 0.0
    last_emit = 0.0
    try:
        with urlopen(req, timeout=timeout) as resp, open(tmp_part, "wb") as out:
            total = 0
            cl = content_length or int(resp.headers.get("Content-Length", "0") or 0)
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise DownloadCancelled("Telechargement annule par l'utilisateur.")
                chunk = resp.read(CHUNK_SIZE)
                if not chunk:
                    break
                out.write(chunk)
                total += len(chunk)
                now = time.time()
                dt = now - last_time
                if dt >= 0.5:
                    instant = (total - last_bytes) / dt
                    smoothed_bps = 0.6 * instant + 0.4 * smoothed_bps
                    last_bytes = total
                    last_time = now
                if now - last_emit >= 0.3:
                    try:
                        on_progress(total, cl, smoothed_bps)
                    except Exception:
                        pass
                    last_emit = now
        # Notif finale (100%)
        try:
            on_progress(total, content_length or total, smoothed_bps)
        except Exception:
            pass
        tmp_part.replace(target)
    except DownloadCancelled:
        if tmp_part.exists():
            try:
                tmp_part.unlink()
            except OSError:
                pass
        raise
    except Exception as exc:
        if tmp_part.exists():
            try:
                tmp_part.unlink()
            except OSError:
                pass
        raise OperationError(f"Telechargement echoue : {exc}") from exc

    duration = time.time() - t_start
    avg = (target.stat().st_size / duration) if duration > 0 else 0.0
    if logger:
        logger.info(
            f"Telechargement single termine : "
            f"{target.stat().st_size // (1024*1024)} Mo en {duration:.1f}s "
            f"({avg/(1024*1024):.1f} Mo/s)"
        )
