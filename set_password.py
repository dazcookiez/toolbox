import getpass

from vega_security import is_valid_password_format


def main():
    print("Validation du format de mot de passe Vega Tool")
    first = getpass.getpass("Mot de passe a tester (doit commencer et finir par !): ")

    if not is_valid_password_format(first):
        raise SystemExit("Le mot de passe doit commencer et se terminer par !, avec au moins un caractere entre les deux.")

    print("Format valide. Aucun mot de passe n'est stocke : toute saisie encadree par ! est acceptee par l'outil.")


if __name__ == "__main__":
    main()
