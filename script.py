# Utilise le certificate store Windows pour valider les certs TLS, au lieu du
# bundle Python interne. Indispensable derriere les proxies d'entreprise qui
# font de l'inspection TLS (l'AC d'entreprise est dans le store Windows mais
# pas dans certifi). A injecter avant tout import qui ouvre une connexion HTTPS.
try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    # Fallback silencieux : si truststore est absent (mode source non installe),
    # ssl continue avec son comportement par defaut (certifi).
    pass

from vega_gui import main
from vega_gui._crashlog import install as install_crash_log


if __name__ == "__main__":
    # Filet global : en build fenetre, un plantage ne laisse sinon aucune trace.
    install_crash_log()
    main()
