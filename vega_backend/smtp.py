import base64
import hashlib
import json
import secrets
import smtplib
import socket
import ssl
import webbrowser
from email import policy
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from ._common import (
    OperationError,
    SmtpTestError,
    ToolLogger,
)

# Client IDs publics utilises pour OAuth2 sans inscription Azure / Google :
# ce sont les identifiants officiels Thunderbird, explicitement autorises pour les clients mail
# embarques. Microsoft accepte ce client ID sur l'autorite "common" ; Google idem pour son scope mail.
THUNDERBIRD_MS_CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"
THUNDERBIRD_GOOGLE_CLIENT_ID = "406964657835-aq8lmia8j95dhl1a2bvharmfk3t1hgqj.apps.googleusercontent.com"
# "Secret" public connu (cf. code source Mozilla) : non confidentiel pour les apps installed.
THUNDERBIRD_GOOGLE_CLIENT_SECRET = "kSmqreRr0qwBWJgbf5Y-PjSU"

OAUTH_MS_AUTHORITY = "https://login.microsoftonline.com/common"
OAUTH_MS_SCOPES = ["https://outlook.office.com/SMTP.Send"]

OAUTH_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/auth"
OAUTH_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
OAUTH_GOOGLE_SCOPE = "https://mail.google.com/"

# Timeout reseau du test SMTP. Un serveur correct renvoie son verdict (banniere,
# EHLO, 235/535 d'auth...) en moins d'1-2 s : 10 s laissent une marge large tout
# en donnant un retour rapide quand le serveur ne repond pas (port filtre,
# mauvais mode SSL/STARTTLS, tarpit anti-brute-force). Avant : 30 s, ressenti
# trop long par l'utilisateur pour un simple "mot de passe refuse".
SMTP_TIMEOUT = 10
# Timeout par sonde en mode "Auto" (SSL/STARTTLS/Rien testes en cascade).
SMTP_PROBE_TIMEOUT = 3


class SmtpTestManager:
    # Test d'envoi de mail avec diagnostic detaille etape par etape.
    # Auth supportees : none, password (LOGIN/PLAIN), CRAM-MD5, XOAUTH2 (Microsoft, Google).
    # OAuth2 :
    #  - Microsoft : device code flow via msal (saisie d'un code sur microsoft.com/devicelogin)
    #  - Google    : loopback OAuth2 + PKCE (mini serveur HTTP local, ouvre le navigateur par defaut)

    def __init__(self, logger):
        self.logger = logger
        self._diag = []
        self._on_diag_line = None

    def _diag_reset(self):
        self._diag = []

    def _diag_add(self, level, message):
        self._diag.append((level, message))
        if level == "ERROR":
            self.logger.error(message)
        elif level == "WARN":
            self.logger.warn(message)
        else:
            self.logger.info(message)
        # Feedback live optionnel : appelé apres chaque ligne pour mise a jour temps-reel UI.
        if self._on_diag_line is not None:
            try:
                self._on_diag_line(level, message)
            except Exception:
                pass

    def _format_diag(self):
        return "\n".join(f"[{level}] {message}" for level, message in self._diag)

    def acquire_microsoft_token(self, on_user_code, cancel_event=None):
        try:
            import msal  # noqa: WPS433
        except ImportError as exc:
            raise OperationError(
                "Module 'msal' indisponible : OAuth2 Microsoft impossible. Reinstallez Vega Toolbox."
            ) from exc
        app = msal.PublicClientApplication(THUNDERBIRD_MS_CLIENT_ID, authority=OAUTH_MS_AUTHORITY)
        flow = app.initiate_device_flow(scopes=OAUTH_MS_SCOPES)
        if "user_code" not in flow:
            raise OperationError(
                "Initiation du flow device code Microsoft refusee : "
                + flow.get("error_description", flow.get("error", "raison inconnue"))
            )
        try:
            on_user_code({
                "user_code": flow["user_code"],
                "verification_uri": flow.get("verification_uri", "https://microsoft.com/devicelogin"),
                "message": flow.get("message", ""),
                "expires_in": flow.get("expires_in", 900),
            })
        except Exception:
            pass
        # exit_condition est verifie entre chaque polling (~5s) : permet l'annulation depuis l'UI.
        if cancel_event is not None:
            result = app.acquire_token_by_device_flow(
                flow,
                exit_condition=lambda _flow: cancel_event.is_set(),
            )
        else:
            result = app.acquire_token_by_device_flow(flow)
        if "access_token" not in result:
            if cancel_event is not None and cancel_event.is_set():
                raise OperationError("Authentification Microsoft annulee par l'utilisateur.")
            raise OperationError(
                "Authentification Microsoft refusee : "
                + result.get("error_description", result.get("error", "raison inconnue"))
            )
        return result["access_token"]

    def acquire_google_token(self, login_hint, on_open_url):
        verifier = base64.urlsafe_b64encode(secrets.token_bytes(40)).decode().rstrip("=")
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        state = secrets.token_urlsafe(16)
        captured = {"code": None, "error": None}

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_GET(self):  # noqa: N802
                params = parse_qs(urlparse(self.path).query)
                if params.get("state", [""])[0] != state:
                    captured["error"] = "state mismatch"
                elif "code" in params:
                    captured["code"] = params["code"][0]
                elif "error" in params:
                    captured["error"] = params["error"][0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                if captured["code"]:
                    body = "<h2>Authentification reussie</h2><p>Vous pouvez fermer cet onglet et revenir a Vega Toolbox.</p>"
                else:
                    body = f"<h2>Erreur</h2><p>{captured.get('error') or 'echec authentification'}</p>"
                self.wfile.write(body.encode("utf-8"))

        server = HTTPServer(("127.0.0.1", 0), _Handler)
        port = server.server_port
        redirect_uri = f"http://127.0.0.1:{port}"

        params = {
            "client_id": THUNDERBIRD_GOOGLE_CLIENT_ID,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": OAUTH_GOOGLE_SCOPE,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "access_type": "offline",
            "prompt": "consent",
        }
        if login_hint:
            params["login_hint"] = login_hint
        auth_url = OAUTH_GOOGLE_AUTH_URL + "?" + urlencode(params)
        try:
            on_open_url(auth_url)
        except Exception:
            pass
        try:
            webbrowser.open(auth_url)
        except Exception:
            pass

        server.timeout = 300
        try:
            server.handle_request()
        finally:
            server.server_close()

        if captured["error"]:
            raise OperationError(
                f"Authentification Google refusee : {captured['error']}.\n"
                ">> Plan B : passez en authentification 'Mot de passe' avec un App Password "
                "(compte Google -> Securite -> Mots de passe d'application). "
                "Necessite la 2FA activee mais c'est plus stable que l'OAuth tiers."
            )
        if not captured["code"]:
            raise OperationError(
                "Aucun code recu de Google (timeout ou refus).\n"
                ">> Plan B : passez en authentification 'Mot de passe' avec un App Password "
                "(compte Google -> Securite -> Mots de passe d'application)."
            )

        body = urlencode({
            "code": captured["code"],
            "client_id": THUNDERBIRD_GOOGLE_CLIENT_ID,
            "client_secret": THUNDERBIRD_GOOGLE_CLIENT_SECRET,
            "code_verifier": verifier,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }).encode("utf-8")
        req = Request(
            OAUTH_GOOGLE_TOKEN_URL,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urlopen(req, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise OperationError(f"Echange du code Google echoue : {exc}") from exc
        if "access_token" not in payload:
            raise OperationError(f"Reponse Google sans access_token : {payload}")
        return payload["access_token"]

    MICROSOFT_DOMAIN_HINTS = frozenset({
        "outlook.com", "hotmail.com", "live.com", "msn.com",
        "outlook.fr", "hotmail.fr", "live.fr", "passport.com",
    })
    GOOGLE_DOMAIN_HINTS = frozenset({"gmail.com", "googlemail.com"})

    def detect_provider(self, config):
        # Devine le fournisseur OAuth2 a partir du serveur SMTP puis du domaine email.
        # Priorite au serveur : c'est le signal le plus fort (un compte custom @entreprise.fr
        # peut etre heberge sur Office 365, et alors le serveur sera smtp.office365.com).
        server = (config.get("server") or "").lower()
        if any(token in server for token in ("office365", "outlook.office", "outlook.com")) or server == "smtp-mail.outlook.com":
            return "microsoft"
        if "gmail" in server or "googlemail" in server:
            return "google"
        for email in (config.get("username", ""), config.get("from_addr", "")):
            if "@" not in (email or ""):
                continue
            domain = email.rsplit("@", 1)[-1].lower()
            if domain in self.MICROSOFT_DOMAIN_HINTS or domain.endswith(".onmicrosoft.com"):
                return "microsoft"
            if domain in self.GOOGLE_DOMAIN_HINTS:
                return "google"
        return None

    def _auto_detect_security(self, host):
        # Sonde les 3 modes dans l'ordre SSL -> STARTTLS -> Rien. Pour SSL,
        # on tente le handshake TLS complet (plus precis qu'un simple TCP).
        # Pour STARTTLS et Rien, un TCP connect suffit pour valider le port.
        # Timeout court (5s par sonde) pour ne pas faire poireauter l'user.
        candidates = (
            ("ssl", 465),
            ("starttls", 587),
            ("none", 25),
        )
        for sec, port in candidates:
            try:
                self._diag_add("INFO", f"Sonde {sec.upper()} sur {host}:{port}...")
                with socket.create_connection((host, port), timeout=SMTP_PROBE_TIMEOUT) as sock:
                    if sec == "ssl":
                        ctx = ssl.create_default_context()
                        with ctx.wrap_socket(sock, server_hostname=host):
                            return sec, port
                    else:
                        return sec, port
            except Exception as exc:
                self._diag_add("WARN", f"  -> {sec.upper()} echoue : {exc}")
                continue
        return None

    def send_test_email(self, config, ui_callbacks=None):
        # auth_kind dans config : "auto" (defaut), "none", "password", "cram_md5",
        # "oauth_microsoft", "oauth_google".
        # En "auto" : essaie le mot de passe si fourni ; bascule sur OAuth (provider auto-detecte) si
        # le serveur refuse l'auth basique. Si pas de mot de passe, OAuth direct selon provider.
        ui_callbacks = ui_callbacks or {}
        self._on_diag_line = ui_callbacks.get("on_diag_line")
        self._diag_reset()
        self._diag_add("INFO", "=== DEBUT TEST SMTP ===")
        self._validate_config(config)

        host = config["server"].strip()
        security = (config.get("security") or "starttls").lower()
        requested_auth = config.get("auth_kind") or "auto"

        # Mode "auto" : sonde successivement SSL/STARTTLS/Rien et utilise le
        # premier qui repond (TCP connect + handshake si applicable). Pratique
        # quand on ne sait pas comment le serveur SMTP est configure.
        if security == "auto":
            self._diag_add("INFO", "Detection automatique de la securite SMTP...")
            detected = self._auto_detect_security(host)
            if detected is None:
                self._diag_add(
                    "ERROR",
                    "Aucun port SMTP n'a repondu (465 SSL / 587 STARTTLS / 25 Rien)."
                )
                raise SmtpTestError(self._diag)
            security, port = detected
            self._diag_add("OK", f"Securite detectee : {security.upper()} sur port {port}")
        else:
            try:
                port = int(config["port"])
            except (TypeError, ValueError):
                self._diag_add("ERROR", f"Port SMTP invalide : {config.get('port')!r}")
                raise SmtpTestError(self._diag)

        try:
            self._diag_add("INFO", f"Resolution DNS de {host}...")
            infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
            ips = sorted({info[4][0] for info in infos})
            self._diag_add("INFO", f"DNS resolu : {', '.join(ips)}")
        except socket.gaierror as exc:
            self._diag_add("ERROR", f"DNS introuvable pour {host} : {exc}")
            raise SmtpTestError(self._diag) from exc

        # Plan d'attaque selon auth_kind.
        provider = self.detect_provider(config)
        attempts = self._plan_auth_attempts(requested_auth, config, provider)
        if not attempts:
            self._diag_add("ERROR", "Aucun plan d'authentification disponible (config inattendue).")
            raise SmtpTestError(self._diag)

        last_error = None
        for index, plan_kind in enumerate(attempts, start=1):
            label = self._auth_kind_label(plan_kind)
            self._diag_add("INFO", f"--- Tentative {index} : {label} ---")
            try:
                return self._attempt_send(host, port, security, plan_kind, config, ui_callbacks)
            except smtplib.SMTPAuthenticationError as exc:
                last_error = exc
                self._diag_add("WARN", self._explain_auth_error(exc, plan_kind))
                # On ne tente une suite que si c'est un echec password et qu'un OAuth est planifie apres.
                if index < len(attempts):
                    self._diag_add("INFO", "Bascule automatique sur OAuth2.")
                    continue
                raise SmtpTestError(self._diag) from exc
            except OperationError:
                # OperationError = diagnostic deja formate, propager tel quel.
                raise
            except Exception as exc:
                # Les autres exceptions sont remontees telles quelles (deja loggees par _attempt_send).
                raise

        # Theoriquement injoignable (la boucle releve toujours), mais par securite :
        if last_error is not None:
            raise SmtpTestError(self._diag) from last_error
        raise SmtpTestError(self._diag)

    def _plan_auth_attempts(self, requested_auth, config, provider):
        # Calcule la liste d'auth a tester dans l'ordre.
        password = config.get("password") or ""
        if requested_auth == "auto":
            attempts = []
            if password:
                attempts.append("password")
            if provider == "microsoft":
                attempts.append("oauth_microsoft")
            elif provider == "google":
                attempts.append("oauth_google")
            if not attempts:
                # Aucun mot de passe et aucun provider connu : on tente sans auth (relais ouvert) ;
                # echouera proprement avec un code SMTP explicite.
                attempts.append("none")
            return attempts
        return [requested_auth]

    @staticmethod
    def _auth_kind_label(kind):
        return {
            "none": "sans authentification",
            "password": "mot de passe SMTP (PLAIN/LOGIN)",
            "cram_md5": "CRAM-MD5",
            "oauth_microsoft": "OAuth2 Microsoft (device code)",
            "oauth_google": "OAuth2 Google (loopback)",
        }.get(kind, kind)

    def _attempt_send(self, host, port, security, auth_kind, config, ui_callbacks):
        # Une tentative complete : open, EHLO, STARTTLS, AUTH, MAIL/RCPT/DATA, QUIT.
        # Si AUTH echoue, propage smtplib.SMTPAuthenticationError pour permettre un retry par l'appelant.
        access_token = None
        if auth_kind == "oauth_microsoft":
            self._diag_add("INFO", "Demande de token OAuth2 Microsoft (device code)...")
            access_token = self.acquire_microsoft_token(
                ui_callbacks.get("oauth_user_code", lambda _info: None),
                cancel_event=ui_callbacks.get("cancel_event"),
            )
            self._diag_add("INFO", "Token Microsoft obtenu.")
        elif auth_kind == "oauth_google":
            self._diag_add("INFO", "Demande de token OAuth2 Google (loopback)...")
            access_token = self.acquire_google_token(
                config.get("username", "") or config.get("from_addr", ""),
                ui_callbacks.get("oauth_open_url", lambda _url: None),
            )
            self._diag_add("INFO", "Token Google obtenu.")

        message = self._build_message(config)
        from_addr = config["from_addr"]
        rcpt_addrs = (
            list(config.get("to_addrs") or [])
            + list(config.get("cc_addrs") or [])
            + list(config.get("bcc_addrs") or [])
        )

        smtp = None
        try:
            self._diag_add("INFO", f"Connexion TCP a {host}:{port} (securite : {security})...")
            if security == "ssl":
                ctx = ssl.create_default_context()
                smtp = smtplib.SMTP_SSL(host, port, timeout=SMTP_TIMEOUT, context=ctx)
            else:
                smtp = smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT)
            self._diag_add("INFO", "Connexion etablie.")

            self._diag_add("INFO", "Envoi EHLO...")
            code, banner = smtp.ehlo()
            self._diag_add("INFO", f"EHLO -> {code} : {self._decode_banner(banner)}")

            if security == "starttls":
                if not smtp.has_extn("starttls"):
                    self._diag_add("ERROR", "Le serveur n'annonce pas STARTTLS dans ses extensions ESMTP.")
                    raise SmtpTestError(self._diag)
                self._diag_add("INFO", "Negociation STARTTLS...")
                ctx = ssl.create_default_context()
                code, _ = smtp.starttls(context=ctx)
                self._diag_add("INFO", f"STARTTLS -> {code} OK")
                smtp.ehlo()

            if auth_kind != "none":
                self._diag_add("INFO", f"Authentification ({auth_kind})...")
                self._do_auth(smtp, auth_kind, config.get("username", "") or config.get("from_addr", ""),
                              config.get("password", ""), access_token)
                self._diag_add("INFO", "Authentification reussie.")

            self._diag_add("INFO", f"MAIL FROM:<{from_addr}>")
            code, resp = smtp.mail(from_addr)
            self._check_code(code, resp, "MAIL FROM")

            for addr in rcpt_addrs:
                self._diag_add("INFO", f"RCPT TO:<{addr}>")
                code, resp = smtp.rcpt(addr)
                self._check_code(code, resp, f"RCPT TO {addr}")

            self._diag_add("INFO", "Envoi DATA...")
            code, resp = smtp.data(message.as_bytes())
            self._check_code(code, resp, "DATA")
            self._diag_add("INFO", f"Message accepte par le serveur : {self._decode_banner(resp)}")

            try:
                smtp.quit()
            except Exception:
                pass

            self._diag_add("INFO", "=== TEST SMTP REUSSI ===")
            return {
                "ok": True,
                "diagnostic": self._format_diag(),
                "lines": list(self._diag),
            }

        except smtplib.SMTPSenderRefused as exc:
            self._diag_add(
                "ERROR",
                f"Expediteur refuse : code {exc.smtp_code} - {self._decode_banner(exc.smtp_error)} (sender={exc.sender})",
            )
            raise SmtpTestError(self._diag) from exc
        except smtplib.SMTPRecipientsRefused as exc:
            for addr, (rcpt_code, rcpt_msg) in exc.recipients.items():
                self._diag_add(
                    "ERROR",
                    f"Destinataire refuse {addr} : code {rcpt_code} - {self._decode_banner(rcpt_msg)}",
                )
            raise SmtpTestError(self._diag) from exc
        except smtplib.SMTPDataError as exc:
            self._diag_add("ERROR", f"DATA refuse : code {exc.smtp_code} - {self._decode_banner(exc.smtp_error)}")
            raise SmtpTestError(self._diag) from exc
        except smtplib.SMTPConnectError as exc:
            self._diag_add(
                "ERROR",
                f"Connexion SMTP refusee : code {exc.smtp_code} - {self._decode_banner(exc.smtp_error)}",
            )
            raise SmtpTestError(self._diag) from exc
        except smtplib.SMTPHeloError as exc:
            self._diag_add("ERROR", f"EHLO/HELO refuse : code {exc.smtp_code} - {self._decode_banner(exc.smtp_error)}")
            raise SmtpTestError(self._diag) from exc
        except smtplib.SMTPNotSupportedError as exc:
            self._diag_add("ERROR", f"Extension SMTP non supportee : {exc}")
            raise SmtpTestError(self._diag) from exc
        except smtplib.SMTPAuthenticationError:
            # On laisse remonter pour permettre le fallback OAuth.
            raise
        except smtplib.SMTPException as exc:
            self._diag_add("ERROR", f"Erreur SMTP : {exc}")
            raise SmtpTestError(self._diag) from exc
        except socket.timeout as exc:
            self._diag_add(
                "ERROR",
                f"Timeout reseau apres {SMTP_TIMEOUT}s sur {host}:{port} : {exc}. "
                "Le serveur ne repond pas : verifiez le port, le mode SSL/STARTTLS "
                "et que le pare-feu n'a pas bloque la connexion.",
            )
            raise SmtpTestError(self._diag) from exc
        except ConnectionRefusedError as exc:
            self._diag_add(
                "ERROR",
                f"Connexion refusee par {host}:{port} (port ferme, pare-feu ou serveur arrete) : {exc}",
            )
            raise SmtpTestError(self._diag) from exc
        except ssl.SSLError as exc:
            self._diag_add(
                "ERROR",
                f"Erreur TLS : {exc}. Verifiez le mode (SSL implicite vs STARTTLS) et le certificat du serveur.",
            )
            raise SmtpTestError(self._diag) from exc
        except OSError as exc:
            self._diag_add("ERROR", f"Erreur reseau : {exc}")
            raise SmtpTestError(self._diag) from exc
        finally:
            if smtp is not None:
                try:
                    smtp.close()
                except Exception:
                    pass

    def _do_auth(self, smtp, kind, username, password, access_token):
        if kind == "password":
            smtp.login(username, password)
        elif kind == "cram_md5":
            mechanisms = (smtp.esmtp_features.get("auth", "") or "").upper()
            if "CRAM-MD5" not in mechanisms:
                raise OperationError("Le serveur n'annonce pas CRAM-MD5 dans les mecanismes AUTH.")
            smtp.user = username
            smtp.password = password
            smtp.auth("CRAM-MD5", smtp.auth_cram_md5)
        elif kind in ("oauth_microsoft", "oauth_google"):
            xoauth = base64.b64encode(
                f"user={username}\x01auth=Bearer {access_token}\x01\x01".encode("utf-8")
            ).decode("ascii")
            code, response = smtp.docmd("AUTH", f"XOAUTH2 {xoauth}")
            if code == 334:
                # Le serveur a renvoye un challenge JSON encode en base64 : ligne vide pour
                # provoquer la reponse finale (235 ou 535).
                code, response = smtp.docmd("")
            if code != 235:
                raise smtplib.SMTPAuthenticationError(code, response)
        else:
            raise OperationError(f"Methode d'authentification inconnue : {kind}")

    def _validate_config(self, config):
        for field in ("server", "port", "from_addr"):
            if not config.get(field):
                raise OperationError(f"Champ obligatoire manquant : {field}")
        if not config.get("to_addrs"):
            raise OperationError("Aucun destinataire renseigne (champ 'A').")
        try:
            int(config["port"])
        except (TypeError, ValueError):
            raise OperationError("Le port SMTP doit etre un nombre entier.")
        kind = config.get("auth_kind", "auto")
        if kind in ("password", "cram_md5"):
            if not config.get("username") and not config.get("from_addr"):
                raise OperationError("Identifiant ou adresse expediteur requis pour authentification.")
            if not config.get("password"):
                raise OperationError("Mot de passe requis pour cette methode d'authentification.")
        if kind in ("oauth_microsoft", "oauth_google"):
            if not config.get("username") and not config.get("from_addr"):
                raise OperationError("Adresse email requise pour OAuth2.")
        # Pour kind == "auto", aucune contrainte forte : on essaiera ce qu'on peut.

    def _build_message(self, config):
        # policy.SMTP : CRLF en fin de ligne (RFC 5321). Sans ca, certains
        # serveurs stricts rejettent avec 552 5.2.0 "Message contains bare LF".
        message = EmailMessage(policy=policy.SMTP)
        message["From"] = config["from_addr"]
        message["To"] = ", ".join(config.get("to_addrs") or [])
        if config.get("cc_addrs"):
            message["Cc"] = ", ".join(config["cc_addrs"])
        message["Subject"] = config.get("subject") or "Test depuis Vega Toolbox"
        message["Date"] = formatdate(localtime=True)
        message["Message-ID"] = make_msgid(domain="vegatoolbox.local")
        body = config.get("body") or "Ceci est un message de test envoye depuis Vega Toolbox."
        message.set_content(body)
        for attach_path in config.get("attachments") or []:
            path = Path(attach_path)
            if not path.exists():
                continue
            try:
                data = path.read_bytes()
            except Exception:
                continue
            message.add_attachment(
                data,
                maintype="application",
                subtype="octet-stream",
                filename=path.name,
            )
        return message

    @staticmethod
    def _decode_banner(banner):
        if isinstance(banner, bytes):
            try:
                return banner.decode("utf-8", errors="replace").strip()
            except Exception:
                return repr(banner)
        return str(banner).strip()

    def _check_code(self, code, response, step):
        if 200 <= code < 400:
            return
        decoded = self._decode_banner(response)
        self._diag_add("ERROR", f"{step} refuse : code {code} - {decoded}")
        raise SmtpTestError(self._diag)

    def _explain_auth_error(self, exc, auth_kind):
        code = getattr(exc, "smtp_code", None)
        msg_text = self._decode_banner(getattr(exc, "smtp_error", b""))
        upper = msg_text.upper()
        explain = ""
        if code == 535:
            explain = "Identifiant ou mot de passe refuse par le serveur."
            if "5.7.3" in msg_text or "MFA" in upper or "MULTI-FACTOR" in upper or "CONDITIONAL" in upper:
                explain = (
                    "Microsoft refuse le mot de passe : MFA / acces conditionnel actif. "
                    "Utilisez l'authentification OAuth2 Microsoft."
                )
            elif "5.7.8" in msg_text and auth_kind == "password":
                explain += (
                    " Pour Gmail : utilisez un App Password (compte Google avec 2FA) ou OAuth2 Google. "
                    "Les mots de passe directs sont refuses par Google depuis 2022."
                )
            elif "BASIC" in upper and "DISABLED" in upper:
                explain = (
                    "Le serveur a desactive l'authentification basique (Basic Auth). "
                    "Passez en OAuth2 ou utilisez un App Password."
                )
        elif code == 534:
            explain = "Le serveur exige une methode d'authentification plus forte (OAuth2, App Password, certificat)."
        elif code == 530:
            explain = "Authentification requise mais non fournie. Activez 'Authentification' dans la config."
        elif code == 538:
            explain = "Le serveur exige un canal chiffre (TLS) avant l'authentification. Activez STARTTLS ou SSL."
        elif code == 454:
            explain = "Erreur temporaire d'authentification. Reessayez dans quelques secondes."
        result = f"Auth refusee : code {code} - {msg_text}"
        if explain:
            result += "\n>> " + explain
        return result
