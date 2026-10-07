"""Mixin HomeChecksMixin : verifications individuelles + 'tout verifier' + detection racine.

Extrait de HomeView pour clarifier la separation UI / logique de check.

Pre-requis cote View :
    self.app                 (VegaToolApp)
    self.vega_root_var       (tk.StringVar)
    self.set_*_status        (set_firewall_status, set_impressions_status,
                              set_structure_status, set_hfsql_status,
                              set_json_status, set_root_status)
    self._propagate          (forwarde les checks aux onglets)
    self._set_migration_var  (sync valeur prerequis vers Migration)
    self._prompt_choice      (popup modal radio)
"""
from ..theme import ERROR_COLOR, MUTED_TEXT, OK_COLOR, WARN_COLOR


class HomeChecksMixin:
    def check_firewall(self):
        self.set_firewall_status("Vérification...", WARN_COLOR)
        self.app.run_task(
            "Vérification pare-feu (accueil)",
            self.app.firewall.check_status,
            on_success=self._on_firewall_checked,
            key="home_firewall",
        )

    def _on_firewall_checked(self, data):
        data = data or {}
        if not data.get("rule_present"):
            self.set_firewall_status("Règle absente", ERROR_COLOR)
        else:
            fully_ok = all(
                data.get(k)
                for k in ("enabled_ok", "direction_ok", "action_ok", "protocol_ok", "port_ok", "profiles_ok")
            ) and data.get("hfsql_port_open") and data.get("hfsql_profiles_ok")
            if fully_ok:
                self.set_firewall_status("Règle active", OK_COLOR)
            else:
                self.set_firewall_status("Règle à corriger", WARN_COLOR)
        self._propagate("firewall", "apply_status", data)

    def check_impressions(self):
        self.set_impressions_status("Vérification...", WARN_COLOR)
        self.app.run_task(
            "Vérification Impressions (accueil)",
            self.app.impressions.check_status,
            on_success=self._on_impressions_checked,
            key="home_impressions",
        )

    def _on_impressions_checked(self, data):
        data = data or {}
        folder_ok = data.get("folder_exists", False)
        share_ok = data.get("share_exists", False) and data.get("share_path_ok", False)
        rights_ok = data.get("share_everyone_full", False) and data.get("ntfs_everyone_full", False)
        if folder_ok and share_ok and rights_ok:
            self.set_impressions_status("Dossier + partage OK", OK_COLOR)
        elif folder_ok and share_ok:
            self.set_impressions_status("Droits à corriger", WARN_COLOR)
        elif folder_ok:
            self.set_impressions_status("Partage absent", WARN_COLOR)
        else:
            self.set_impressions_status("À réparer", ERROR_COLOR)
        self._propagate("impressions", "apply_status", data)

    def check_structure(self):
        root_value = self.vega_root_var.get().strip()
        if not root_value:
            self.set_structure_status("Racine non renseignée", ERROR_COLOR)
            return
        self.set_structure_status("Vérification...", WARN_COLOR)

        def _on_struct_err(exc):
            # validate_root leve des messages specifiques qu'on remonte tels
            # quels (ils disent ce qui manque vraiment : V6 pas extrait,
            # vega.dos absent, racine introuvable). Sans ca, le user voit
            # "invalide" et ne sait pas ce qui cloche.
            msg = (str(exc) or "").lower()
            if "v6" in msg and "introuvable" in msg:
                # vega.dos\V6 absent : V6 pas encore telecharge/extrait
                self.set_structure_status("V6 non installé", ERROR_COLOR)
            elif "vega.dos" in msg and "introuvable" in msg:
                # vega.dos absent : pas la bonne etape
                self.set_structure_status("vega.dos manquant", ERROR_COLOR)
            elif "introuvable" in msg:
                # Racine elle-meme introuvable
                self.set_structure_status("Racine introuvable", ERROR_COLOR)
            else:
                self.set_structure_status("Échec vérification", ERROR_COLOR)

        self.app.run_task(
            "Vérification structure (accueil)",
            lambda: self.app.migration.validate_root(root_value),
            on_success=lambda _data: self.set_structure_status("OK", OK_COLOR),
            on_error=_on_struct_err,
            key="home_structure",
        )

    def check_hfsql(self):
        self.set_hfsql_status("Vérification...", WARN_COLOR)
        self.app.run_task(
            "Vérification HFSQL (accueil)",
            self.app.migration.hfsql_service_status,
            on_success=self._on_hfsql_checked,
            key="home_hfsql",
        )

    def _on_hfsql_checked(self, data):
        data = data or {}
        if not data.get("found"):
            self.set_hfsql_status("Non installé", OK_COLOR)
        elif data.get("running"):
            self.set_hfsql_status("En cours - à arrêter", ERROR_COLOR)
        else:
            self.set_hfsql_status("Arrêté", OK_COLOR)
        self._propagate("migration", "_apply_hfsql_state", data)

    def check_json(self):
        self.set_json_status("Vérification...", WARN_COLOR)
        self.app.run_task(
            "Vérification JSON RetailForce (accueil)",
            self.app.migration.retailforce_json_status,
            on_success=self._on_json_checked,
            key="home_json",
        )

    def _on_json_checked(self, data):
        data = data or {}
        if not data.get("exists"):
            self.set_json_status("Fichier absent", WARN_COLOR)
        elif data.get("error"):
            self.set_json_status("Lecture impossible", ERROR_COLOR)
        elif data.get("reset"):
            # FiscalClients vide = etat propre post-install
            self.set_json_status("Réinitialisé", OK_COLOR)
            self._set_migration_var("json_var", True)
        elif data.get("configured"):
            # Contient de vraies donnees client : NE PAS toucher
            self.set_json_status("Configuré", OK_COLOR)
            self._set_migration_var("json_var", True)
        elif data.get("is_test"):
            # Contient les donnees de demo RetailForce : a reinitialiser
            self.set_json_status("À réinitialiser", ERROR_COLOR)
            self._set_migration_var("json_var", False)
        else:
            # Forme inattendue (ni clients valides ni liste vide)
            self.set_json_status("Format inattendu", ERROR_COLOR)
            self._set_migration_var("json_var", False)

    def check_all(self):
        self.check_firewall()
        self.check_impressions()
        self.check_structure()
        self.check_hfsql()
        self.check_json()

    def run_full_check(self):
        # Bouton manuel : detection racine + verification de tous les statuts.
        self.detect_root()
        self.check_all()

    def detect_root(self):
        # Auto-detection des racines Vega : lance la recherche en arriere-plan, applique le resultat.
        self.set_root_status("Détection en cours...", MUTED_TEXT)
        self.app.run_task(
            "Détection racine Vega",
            self.app.migration.detect_vega_roots,
            on_success=self._on_root_detected,
            key="home_detect_root",
        )

    def _on_root_detected(self, results):
        results = results or []
        # Memorise le scan pour alimenter le menu deroulant "Bases" de l'accueil
        # et degrise le bouton : le poste a maintenant ete scanne.
        self._detected_roots = results
        if getattr(self, "home_bases_button", None) is not None:
            has_base = any(r.get("bases") for r in results)
            try:
                self.home_bases_button.configure(state="normal" if has_base else "disabled")
            except Exception:
                pass
        local_results = [r for r in results if not r.get("on_network")]
        network_results = [r for r in results if r.get("on_network")]

        if not results:
            self.set_root_status(
                "Aucune racine Vega détectée. Renseignez-la manuellement dans l'onglet Migration.",
                WARN_COLOR,
            )
            return

        if not local_results and network_results:
            paths = ", ".join(str(r["root"]) for r in network_results)
            self.set_root_status(
                f"Vega détecté sur disque réseau ({paths}) — migration impossible.",
                ERROR_COLOR,
            )
            return

        if len(local_results) > 1:
            # Plusieurs racines : prompt l'utilisateur. Chaque option = "<root> > <base>" si bases connues.
            options = []
            for result in local_results:
                root_path = result["root"]
                bases = result.get("bases", [])
                if bases:
                    for base in bases:
                        options.append((f"{root_path} > {base['name']}", str(base["path"])))
                else:
                    options.append((f"{root_path} (sans base BDD)", str(root_path)))
            chosen_value = self._prompt_choice(
                "Plusieurs racines Vega détectées",
                "Plusieurs installations Vega ont été trouvées sur ce poste.\nSélectionnez celle à utiliser :",
                options,
            )
            if chosen_value:
                self.vega_root_var.set(chosen_value)
                self.set_root_status(f"Sélectionné : {chosen_value}", OK_COLOR)
            else:
                self.set_root_status("Aucune racine sélectionnée.", WARN_COLOR)
            return

        # Une seule racine locale.
        chosen = local_results[0]
        bases = chosen.get("bases", [])
        if len(bases) > 1:
            options = [(b["name"], str(b["path"])) for b in bases]
            chosen_value = self._prompt_choice(
                "Plusieurs bases Vega détectées",
                f"Plusieurs bases ont été trouvées dans {chosen['root']}\\BDD.\nSélectionnez la base à migrer :",
                options,
            )
            if chosen_value:
                self.vega_root_var.set(chosen_value)
                base_name = next((n for n, p in options if p == chosen_value), "?")
                self.set_root_status(f"Sélectionné : base « {base_name} »", OK_COLOR)
            else:
                self.vega_root_var.set(str(chosen["root"]))
                self.set_root_status("Aucune base sélectionnée.", WARN_COLOR)
            return
        if len(bases) == 1:
            self.vega_root_var.set(str(bases[0]["path"]))
            self.set_root_status(f"Détecté : base « {bases[0]['name']} »", OK_COLOR)
        else:
            self.vega_root_var.set(str(chosen["root"]))
            self.set_root_status("Détecté (aucune base BDD trouvée).", WARN_COLOR)
