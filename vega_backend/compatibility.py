"""Verification de la compatibilite materielle d'un poste avec les prerequis Vega.

Prend en entree le snapshot produit par PcInfoManager.collect() et renvoie
une liste de criteres avec leur statut (ok / warning / fail), un libelle
francais, la valeur detectee, et la valeur attendue.

Profil 'workstation' (poste client / monoposte) :
  - CPU : Intel Core i5/i7/i9 ou Ultra 12e gen min, OU Ryzen 5/7/9 5000+
  - RAM : 8 Go min, 16 Go recommande
  - Disque systeme : 160 Go min, SSD
  - OS : Windows 11 Pro 64 bits
  - Carte graphique dediee (warning si integree seulement)
  - Resolution >= 1024x768 (Restaurant) ou 1200x900 (Hotel)

Profil 'server' (4+ postes) :
  - CPU : Xeon/EPYC/Ryzen/Core compatible
  - RAM : 16 Go min, 32 Go recommande
  - Disque : 256 Go min, SSD
  - OS : Windows Server 2022 ou 2025

Les criteres reseau (cat 6/7, switch 1 Gb) ne sont pas verifiables depuis
le poste : on ne mesure que ce qui est detectable (debit lien adaptateur).
"""
import re


# --- Helpers parsing CPU ---

_INTEL_CORE_GEN  = re.compile(r"i[3579]-(\d{4,5})", re.IGNORECASE)
_INTEL_GEN_PREFIX = re.compile(r"(\d{1,2})\s*(?:st|nd|rd|th)\s*gen", re.IGNORECASE)
_INTEL_ULTRA     = re.compile(r"ultra\s*[579]", re.IGNORECASE)
_RYZEN           = re.compile(r"ryzen\s*[579]\s+(\d{4})", re.IGNORECASE)
_XEON            = re.compile(r"\bxeon\b", re.IGNORECASE)
_EPYC            = re.compile(r"\bepyc\b", re.IGNORECASE)


def parse_intel_generation(cpu_name):
    # Strategie 1 (la plus fiable) : prefix '13th Gen' / '12th Gen' dans le
    # nom complet (souvent retourne par WMI : "13th Gen Intel(R) Core(TM) i7-1365U").
    # Strategie 2 : extraction du model number apres le tiret.
    #   - 5 chiffres : i5-12500 -> gen 12, i7-13700K -> gen 13
    #   - 4 chiffres commencant par 10-20 : i7-1365U -> gen 13, i5-1240P -> gen 12
    #   - 4 chiffres autres : i7-8650U -> gen 8, i7-9750H -> gen 9
    if not cpu_name:
        return None
    m = _INTEL_GEN_PREFIX.search(cpu_name)
    if m:
        try:
            gen = int(m.group(1))
            if 1 <= gen <= 99:
                return gen
        except ValueError:
            pass
    m = _INTEL_CORE_GEN.search(cpu_name)
    if not m:
        return None
    model_num = m.group(1)
    if len(model_num) == 5:
        return int(model_num[:2])
    if len(model_num) == 4:
        first_two = int(model_num[:2])
        # 10-20 = gen recente (10e a 20e). Au-dela on retombe sur le 1er chiffre.
        if 10 <= first_two <= 20:
            return first_two
        return int(model_num[0])
    return None


def parse_ryzen_series(cpu_name):
    # "AMD Ryzen 7 5800X" -> 5000
    # "AMD Ryzen 5 3600" -> 3000
    if not cpu_name:
        return None
    m = _RYZEN.search(cpu_name)
    if not m:
        return None
    model = m.group(1)
    return int(model[0]) * 1000


def cpu_is_compatible(cpu_name, profile="workstation"):
    # Retourne (ok: bool, detected_label: str, explication: str)
    if not cpu_name:
        return False, "Inconnu", "Modèle CPU non détecté"

    # Xeon / EPYC : OK serveur, OK workstation aussi
    if _XEON.search(cpu_name):
        return True, "Intel Xeon", "Compatible serveur (Xeon)"
    if _EPYC.search(cpu_name):
        return True, "AMD EPYC", "Compatible serveur (EPYC)"

    # Intel Ultra (nouvelle gamme) -> OK
    if _INTEL_ULTRA.search(cpu_name):
        return True, "Intel Ultra", "Compatible (Intel Ultra)"

    # Intel Core 12e gen+
    intel_gen = parse_intel_generation(cpu_name)
    if intel_gen is not None:
        if intel_gen >= 12:
            return True, f"Intel Core {intel_gen}e génération", "Compatible (≥ 12e génération)"
        else:
            return False, f"Intel Core {intel_gen}e génération", "Trop ancien : 12e génération minimum requise"

    # Ryzen 5000+
    ryzen_series = parse_ryzen_series(cpu_name)
    if ryzen_series is not None:
        if ryzen_series >= 5000:
            return True, f"AMD Ryzen série {ryzen_series}", "Compatible (série 5000 ou supérieure)"
        else:
            return False, f"AMD Ryzen série {ryzen_series}", "Trop ancien : série 5000 minimum requise"

    return False, cpu_name[:50], "Modèle non reconnu / non éligible Vega"


# --- Helpers OS ---

def os_is_compatible(os_caption, profile="workstation"):
    if not os_caption:
        return False, "Inconnu", "Système d'exploitation non détecté"
    caption_lower = os_caption.lower()
    if profile == "server":
        # Windows Server 2022 / 2025
        if "server 2022" in caption_lower or "server 2025" in caption_lower:
            return True, os_caption, "Compatible serveur"
        if "server" in caption_lower:
            return False, os_caption, "Version Server trop ancienne (2022 ou 2025 requis)"
        return False, os_caption, "Pas un Windows Server (2022 ou 2025 requis pour ce profil)"
    # workstation : Windows 11 Pro
    if "windows 11" in caption_lower:
        if "pro" in caption_lower or "enterprise" in caption_lower:
            return True, os_caption, "Compatible (Windows 11 Pro ou Enterprise)"
        return False, os_caption, "Windows 11 détecté mais pas en édition Pro ou Enterprise (Home insuffisant)"
    if "windows 10" in caption_lower:
        return False, os_caption, "Windows 10 : migration vers Windows 11 Pro recommandée"
    return False, os_caption, "Système d'exploitation non conforme (Windows 11 requis)"


# --- Helpers RAM ---

def ram_status(total_gb, profile="workstation"):
    if total_gb is None:
        return "fail", "Inconnu", "RAM non détectée"
    total_gb = float(total_gb)
    if profile == "server":
        if total_gb >= 32:
            return "ok", f"{total_gb:.1f} Go", "Recommandé (≥ 32 Go)"
        if total_gb >= 16:
            return "warning", f"{total_gb:.1f} Go", "Minimum atteint, 32 Go recommandés"
        return "fail", f"{total_gb:.1f} Go", "Insuffisant : 16 Go minimum pour un serveur"
    # workstation
    if total_gb >= 16:
        return "ok", f"{total_gb:.1f} Go", "Recommandé (≥ 16 Go)"
    if total_gb >= 8:
        return "warning", f"{total_gb:.1f} Go", "Minimum atteint, 16 Go recommandés"
    return "fail", f"{total_gb:.1f} Go", "Insuffisant : 8 Go minimum"


# --- Helpers disque ---

def disk_ssd_verdict(physical_disks):
    # Determine si le poste dispose d'un SSD en combinant plusieurs signaux, du
    # plus fiable au plus approximatif :
    #   1. MSFT_PhysicalDisk.MediaType  (SSD / SCM = flash ; HDD = mecanique)
    #   2. SpindleSpeed == 0            (0 tr/min = non-rotatif = SSD)
    #   3. BusType / Model = NVMe       (bus NVMe = forcement SSD)
    #   4. Mots-cles dans le Model      (ssd, solid state, m.2) — dernier recours
    # Retourne (True, detail) / (False, detail) / (None, detail) si indeterminable.
    any_ssd = False
    any_hdd = False
    ssd_detail = None
    for p in physical_disks or []:
        media = (p.get("MediaType") or "").lower()
        spindle = p.get("SpindleSpeed")
        bus = (p.get("BusType") or "").lower()
        model = (p.get("Model") or "").lower()
        if media in ("ssd", "scm"):
            any_ssd = True
            ssd_detail = ssd_detail or "type media SSD"
        elif isinstance(spindle, int) and spindle == 0:
            any_ssd = True
            ssd_detail = ssd_detail or "0 tr/min (non-rotatif)"
        elif "nvme" in bus or "nvme" in model:
            any_ssd = True
            ssd_detail = ssd_detail or "bus NVMe"
        elif any(kw in model for kw in ("ssd", "solid state", "m.2")):
            any_ssd = True
            ssd_detail = ssd_detail or "modèle SSD"
        elif media == "hdd" or (isinstance(spindle, int) and spindle > 0):
            any_hdd = True
    if any_ssd:
        return True, ssd_detail or "SSD"
    if any_hdd:
        return False, "disque mécanique (HDD)"
    return None, "type indéterminé"


def system_disk_status(logical_disks, physical_disks, profile="workstation", is_virtual=False):
    # On regarde le C: et son disque physique parent (si on peut faire le lien).
    # Faute de mieux on prend le premier disque physique.
    min_size = 256 if profile == "server" else 160
    c_disk = None
    for d in logical_disks or []:
        if (d.get("DeviceID") or "").upper().startswith("C"):
            c_disk = d
            break
    if not c_disk:
        return "fail", "Inconnu", "Volume C: non détecté"
    size_gb = c_disk.get("SizeGB") or 0
    free_gb = c_disk.get("FreeGB") or 0
    is_ssd, ssd_detail = disk_ssd_verdict(physical_disks)
    label_size = f"{size_gb:.0f} Go (libre : {free_gb:.0f} Go)"
    if size_gb < min_size:
        return "fail", label_size, f"Volume C: trop petit : {min_size} Go minimum requis"
    if is_ssd is True:
        return "ok", label_size + " - SSD", f"Conforme (≥ {min_size} Go, SSD — {ssd_detail})"
    if is_ssd is False:
        return "warning", label_size + " - HDD", "Disque système mécanique détecté (SSD requis)"
    # Type indeterminable (souvent le cas sur VM : le disque physique n'expose
    # pas son type). On ne conclut pas HDD a tort : sur une VM le stockage
    # sous-jacent est quasi toujours SSD/SAN, donc on passe en OK avec mention.
    if is_virtual:
        return "ok", label_size + " - disque virtuel", (
            f"Disque virtuel (VM) : type non détectable, supposé conforme (≥ {min_size} Go)"
        )
    return "warning", label_size + " - type indéterminé", (
        "Type de disque non déterminé — vérifier manuellement qu'il s'agit d'un SSD"
    )


# --- Helpers reseau ---

def network_status(nics):
    # On verifie qu'il y a au moins une carte filaire (pas 'wireless' / 'wifi').
    # Le type de cable (Cat5/6/7) n'est pas detectable depuis l'OS, on ne
    # mesure que le debit negotie du lien (qui depend du cable ET du switch).
    if not nics:
        return "fail", "Aucune carte", "Aucune carte réseau active détectée"
    wired = []
    wifi = []
    for n in nics:
        desc = (n.get("Description") or n.get("Name") or "").lower()
        if any(kw in desc for kw in ("wireless", "wifi", "wi-fi", "802.11")):
            wifi.append(n)
        else:
            wired.append(n)
    if not wired and wifi:
        return "warning", "Connexion Wifi uniquement", "Ethernet filaire recommandé pour Vega"
    if wired:
        # Verifie debit du premier filaire. Seuil 900 Mb/s pour OK car en
        # pratique du Gigabit (1000 Mb/s theo) negocie ~940-960 Mb/s.
        n = wired[0]
        speed = n.get("SpeedMbps")
        label = f"{n.get('Name') or n.get('Description') or '?'}"
        if speed and speed >= 900:
            return "ok", f"{label} - {speed} Mb/s", "Liaison Gigabit conforme"
        if speed and speed >= 100:
            return "warning", f"{label} - {speed} Mb/s", "Liaison 100 Mb/s détectée, Gigabit recommandé (câble et/ou switch)"
        return "warning", label, "Liaison filaire détectée mais débit non identifié"
    return "warning", "Inconnu", "Aucune carte filaire détectée"


# --- Pipeline principal ---

def check_compatibility(pc_data, profile="workstation"):
    # pc_data : dict produit par PcInfoManager.collect()
    # profile : "workstation" ou "server"
    # Retourne (overall_status, list_of_criteria)
    if not pc_data:
        return "fail", []

    cpu = pc_data.get("cpu") or {}
    os_info = pc_data.get("os") or {}
    system = pc_data.get("system") or {}
    ram = pc_data.get("ram") or {}
    nics = pc_data.get("network") or []
    ldisks = pc_data.get("logicalDisks") or []
    pdisks = pc_data.get("physicalDisks") or []

    criteria = []

    # --- CPU : detected = nom complet propre, requirement contextuel ---
    cpu_name = cpu.get("Name") or ""
    cpu_ok, _cpu_short, cpu_msg = cpu_is_compatible(cpu_name, profile)
    cpu_clean = re.sub(r"\s+", " ", cpu_name).strip() or "Inconnu"
    if cpu_ok:
        intel_gen = parse_intel_generation(cpu_name)
        if intel_gen:
            cpu_detected = f"{cpu_clean} ({intel_gen}e génération)"
        else:
            cpu_detected = cpu_clean
    else:
        cpu_detected = cpu_clean
    # Requirement contextuel selon le constructeur detecte.
    # On explique pourquoi en termes generaux (perf / support / securite),
    # pas en citant l'editeur Vega ou PCSoft.
    if _XEON.search(cpu_name) or _EPYC.search(cpu_name):
        cpu_req_short = "Xeon ou EPYC : adaptés à un usage serveur"
        cpu_req_full = (
            "Les processeurs Intel Xeon et AMD EPYC sont conçus pour les "
            "charges serveur : plus de cœurs physiques, support de la "
            "mémoire ECC (correction d'erreurs), fonctionnement 24/7, "
            "et meilleure gestion des connexions clients simultanées."
        )
    elif "intel" in cpu_name.lower():
        cpu_req_short = "Intel Core 12e génération ou plus récent"
        cpu_req_full = (
            "Les processeurs Intel Core de 12e génération (Alder Lake, "
            "lancée fin 2021) introduisent une architecture hybride "
            "(cœurs Performance + cœurs Efficient) et le support complet "
            "des jeux d'instructions modernes (AVX2 etc.) nécessaires aux "
            "applications professionnelles récentes. Les générations 1 à "
            "11 sont en fin de vie commerciale et perdent progressivement "
            "le support des éditeurs logiciels."
        )
    elif "amd" in cpu_name.lower() or "ryzen" in cpu_name.lower():
        cpu_req_short = "AMD Ryzen série 5000 ou plus récente"
        cpu_req_full = (
            "Les processeurs AMD Ryzen série 5000 (architecture Zen 3, "
            "fin 2020) et plus récents offrent les performances "
            "monothread et le jeu d'instructions attendus par les "
            "logiciels professionnels actuels. Les séries 1000 à 3000 "
            "(Zen 1 / Zen+ / Zen 2) sont sensiblement plus lentes en "
            "monothread et leur support driver/firmware se réduit."
        )
    else:
        cpu_req_short = "Processeur récent (≤ 4 ans)"
        cpu_req_full = (
            "Le processeur n'a pas été identifié. Un processeur de moins "
            "de 4 ans est généralement recommandé pour assurer le support "
            "des jeux d'instructions modernes, la sécurité (mitigations "
            "Spectre/Meltdown matérielles) et les performances attendues "
            "par les logiciels professionnels actuels."
        )
    criteria.append({
        "label": "Processeur",
        "status": "ok" if cpu_ok else "fail",
        "detected": cpu_detected,
        "requirement_short": cpu_req_short,
        "requirement_full": cpu_req_full,
        "message": cpu_msg,
    })

    # --- OS : detected = caption + architecture (combine) ---
    os_caption = (os_info.get("Caption") or "").strip()
    arch = os_info.get("OSArchitecture") or system.get("SystemType") or ""
    is_64 = "64" in arch
    os_ok, _os_short, os_msg = os_is_compatible(os_caption, profile)
    # OS conforme = OS-compatible ET 64 bits
    os_global_ok = os_ok and is_64
    if not is_64:
        os_status = "fail"
        os_msg_final = "Architecture 32 bits détectée — Vega requiert un système 64 bits."
    elif not os_ok:
        os_status = "fail"
        os_msg_final = os_msg
    else:
        os_status = "ok"
        os_msg_final = os_msg
    # Detected : combine caption + architecture (normalisee en "64 bits")
    arch_label = "64 bits" if is_64 else (arch or "architecture inconnue")
    os_detected = f"{os_caption} ({arch_label})" if os_caption else arch_label
    if profile == "server":
        os_req_short = "Windows Server 2022 ou 2025"
        os_req_full = (
            "Windows Server 2022 et 2025 sont les versions actuellement "
            "sous support actif de Microsoft (correctifs de sécurité "
            "réguliers jusqu'en 2031 et 2034 respectivement). Les versions "
            "antérieures (2019, 2016, 2012 R2) entrent en fin de support "
            "étendu : sans patchs de sécurité réguliers, le serveur est "
            "exposé à des vulnérabilités non corrigées, ce qui est "
            "inacceptable pour un poste hébergeant des données métier."
        )
    else:
        os_req_short = "Windows 11 Pro ou Enterprise 64 bits"
        os_req_full = (
            "Les éditions Windows 11 Pro et Enterprise incluent des "
            "fonctionnalités absentes de Windows Home indispensables en "
            "environnement professionnel : jonction de domaine Active "
            "Directory, stratégies de groupe (GPO), BitLocker (chiffrement "
            "du disque), Bureau à distance entrant, Hyper-V. Windows 10 "
            "a atteint sa fin de support en octobre 2025 : il ne reçoit "
            "plus de mises à jour de sécurité gratuites, ce qui expose "
            "le poste à des failles non corrigées."
        )
    criteria.append({
        "label": "Système d'exploitation",
        "status": os_status,
        "detected": os_detected,
        "requirement_short": os_req_short,
        "requirement_full": os_req_full,
        "message": os_msg_final,
    })

    # --- RAM ---
    # Le CHECK utilise la RAM installee (Win32_PhysicalMemory) : c'est la
    # valeur materielle reelle, celle des barrettes. Conforme = ce qu'on
    # voit dans la facture/specs constructeur.
    # L'AFFICHAGE utilise la RAM utilisable (vue par l'OS) car c'est ce
    # que le user voit dans Windows Task Manager. La difference (~0.6 Go
    # typiquement) provient de la memoire reservee au GPU integre/BIOS,
    # on ne la mentionne pas pour pas confondre.
    installed_gb = ram.get("TotalGB")
    if installed_gb is None:
        installed_gb = system.get("TotalPhysicalMemoryGB")
    usable_gb = ram.get("UsableGB") or system.get("TotalPhysicalMemoryGB") or installed_gb
    ram_st, _ram_short_label, ram_msg = ram_status(installed_gb, profile)
    ram_detected = f"{usable_gb:.1f} Go" if usable_gb else "Inconnu"
    if profile == "server":
        ram_req_short = "32 Go recommandés (16 Go minimum)"
        ram_req_full = (
            "Un serveur doit garder en mémoire le moteur de base de "
            "données, les caches, les sessions des clients connectés et "
            "le système d'exploitation. 16 Go est un minimum strict pour "
            "un usage léger ; à partir de 5 postes clients ou pour des "
            "bases volumineuses, 32 Go évite que le serveur ne se mette "
            "à swapper sur le disque, ce qui provoque des ralentissements "
            "ressentis par tous les utilisateurs simultanément."
        )
    else:
        ram_req_short = "16 Go recommandés (8 Go minimum)"
        ram_req_full = (
            "Windows 11 consomme à lui seul environ 4 Go de RAM au repos. "
            "Avec 8 Go installés, il reste peu de marge pour faire tourner "
            "plusieurs applications en parallèle (navigateur, suite "
            "bureautique, logiciel métier). 16 Go offre un confort "
            "d'utilisation réel et évite les ralentissements liés au "
            "fichier d'échange (swap disque)."
        )
    criteria.append({
        "label": "Mémoire RAM",
        "status": ram_st,
        "detected": ram_detected,
        "requirement_short": ram_req_short,
        "requirement_full": ram_req_full,
        "message": ram_msg,
    })

    # --- Disque systeme ---
    is_virtual = bool(system.get("IsVirtual"))
    disk_st, disk_label, disk_msg = system_disk_status(ldisks, pdisks, profile, is_virtual)
    if profile == "server":
        disk_req_short = "SSD 256 Go minimum + sauvegarde"
        disk_req_full = (
            "Un SSD offre des temps d'accès environ 100 fois plus rapides "
            "qu'un disque dur mécanique (HDD), ce qui est critique pour un "
            "serveur qui répond aux requêtes de plusieurs clients en "
            "parallèle. 256 Go laissent la place pour Windows Server "
            "(~30 Go), les applications, les bases de données et leurs "
            "sauvegardes locales. Un système de sauvegarde externe "
            "(automatique, à minima quotidien) est indispensable : sans "
            "lui, une panne disque équivaut à une perte de données."
        )
    else:
        disk_req_short = "SSD 160 Go minimum"
        disk_req_full = (
            "Un SSD offre des temps d'accès environ 100 fois plus rapides "
            "qu'un disque dur mécanique (HDD) : démarrage Windows plus "
            "rapide, applications réactives, accès quasi instantané aux "
            "fichiers. 160 Go laissent la place pour Windows 11 (~30 Go), "
            "les applications professionnelles courantes et les données "
            "utilisateur avec une marge confortable. En dessous, le "
            "disque sature rapidement et les performances chutent."
        )
    criteria.append({
        "label": "Disque système",
        "status": disk_st,
        "detected": disk_label,
        "requirement_short": disk_req_short,
        "requirement_full": disk_req_full,
        "message": disk_msg,
    })

    # NOTE : critere RESEAU retire. Le type de cable (Cat 5/6/7), la qualite
    # du switch, le cablage mural ne sont pas detectables depuis l'OS - seul
    # le debit negotie est lisible et n'est pas representatif des prerequis
    # cabling Vega. Verification a faire visuellement sur site.

    # Statut global : pire des criteres
    if any(c["status"] == "fail" for c in criteria):
        overall = "fail"
    elif any(c["status"] == "warning" for c in criteria):
        overall = "warning"
    else:
        overall = "ok"

    return overall, criteria
