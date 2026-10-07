# Signature de vega_toolbox.exe (gratuit, auto-signe)

L'objectif ici n'est pas de supprimer SmartScreen (impossible sans certif paye)
mais de :
- afficher un editeur lisible ("Zucchetti Vega Toolbox") dans les proprietes
- garantir l'integrite (toute modif post-signature casse la signature)
- preparer l'infra si Zucchetti finit par fournir un certif officiel

## Pre-requis

`signtool.exe` est dans le **Windows SDK** (gratuit, telechargement Microsoft).
Tu l'as probablement deja avec Visual Studio Build Tools. Verifier :

```powershell
where signtool
# Sinon : installe Windows SDK depuis https://developer.microsoft.com/windows/downloads/windows-sdk/
```

## 1. Creer le certif auto-signe (une seule fois)

Depuis PowerShell :

```powershell
$cert = New-SelfSignedCertificate `
  -Type CodeSigningCert `
  -Subject "CN=Zucchetti Vega Toolbox, O=Zucchetti, C=FR" `
  -CertStoreLocation Cert:\CurrentUser\My `
  -KeyUsage DigitalSignature `
  -KeyAlgorithm RSA `
  -KeyLength 2048 `
  -NotAfter (Get-Date).AddYears(10)

# Note le thumbprint, tu en auras besoin pour signer
$cert.Thumbprint
```

Sauvegarde du certif (avec sa cle privee) au cas ou tu changerais de poste :

```powershell
$pwd = ConvertTo-SecureString -String "<CHOISIS_UN_MOT_DE_PASSE>" -Force -AsPlainText
Export-PfxCertificate -Cert $cert -FilePath C:\backup\VegaToolbox-CodeSigning.pfx -Password $pwd
```

Range le .pfx dans un endroit sur (NAS Zucchetti, gestionnaire de mots de passe).

## 2. Signer le build

Apres chaque `pyinstaller` :

```powershell
signtool sign `
  /sha1 <TON_THUMBPRINT> `
  /tr http://timestamp.digicert.com `
  /td sha256 `
  /fd sha256 `
  /d "Vega Toolbox" `
  dist\vega_toolbox.exe
```

- `/tr` : timestamp authority (gratuit, public). Garantit que la signature reste
  valide meme si ton certif auto-signe expire dans 10 ans.
- `/td sha256` / `/fd sha256` : algo (Microsoft refuse SHA1 depuis 2016).
- `/d` : description visible dans la fenetre UAC d'elevation.

Verification :

```powershell
signtool verify /pa /v dist\vega_toolbox.exe
```

## 3. Automatiser dans le build PyInstaller

Ajouter un script `build.ps1` a la racine du projet :

```powershell
# build.ps1
param(
    [string]$Thumbprint = "<TON_THUMBPRINT>"
)

python -m PyInstaller VegaV6_Migration.spec --noconfirm
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

signtool sign /sha1 $Thumbprint /tr http://timestamp.digicert.com `
    /td sha256 /fd sha256 /d "Vega Toolbox" dist\vega_toolbox.exe

signtool verify /pa /v dist\vega_toolbox.exe
```

Lancer : `.\build.ps1`

## 4. Importer le certif sur les postes clients (optionnel)

Sans cet import : les postes clients verront "Editeur : Inconnu" + warning SmartScreen
classique des exes telecharges.

Avec cet import : "Editeur : Zucchetti Vega Toolbox" et l'integrite est verifiee.

```powershell
# 1. Sur ton poste : exporter la cle publique (.cer, sans cle privee)
Get-ChildItem Cert:\CurrentUser\My\<TON_THUMBPRINT> | Export-Certificate `
  -FilePath VegaToolbox-CodeSigning.cer

# 2. Sur chaque poste client (admin requise) :
Import-Certificate -FilePath VegaToolbox-CodeSigning.cer `
  -CertStoreLocation Cert:\LocalMachine\Root
Import-Certificate -FilePath VegaToolbox-CodeSigning.cer `
  -CertStoreLocation Cert:\LocalMachine\TrustedPublisher
```

A faire une seule fois par poste. Une fois le `.cer` deploye, n'importe quelle
version de Vega Toolbox signee avec ton thumbprint sera consideree "de confiance"
sur ce poste, sans nouveau prompt.

Pour automatiser le deploiement : .cer dans un partage reseau + GPO ou script
d'install qui appelle `Import-Certificate`.

## 5. Limites de l'auto-signature

| Limite | Detail |
|--------|--------|
| SmartScreen sur poste neuf | Toujours present tant que le `.cer` n'a pas ete importe |
| Defender | Peut bloquer un exe non signe par un editeur connu, meme si signe localement |
| Reputation | L'exe ne gagne pas de "reputation" Microsoft |
| Confiance navigateur | Telechargement HTTP : Edge / Chrome avertiront quand meme |

Pour Vega Toolbox = outil interne deploye chez tes clients via tes mains : c'est
suffisant. Tu importes le `.cer` la 1re fois que tu interviens sur un poste,
ensuite plus de friction.

## 6. Pieges courants

- **Thumbprint qui change** : si tu regeneres le certif, tous les exes signes avant
  redeviennent "Editeur inconnu" sur les postes clients. Garde le meme certif.
- **Exporter SANS la cle privee** pour le `.cer` deploye : ne jamais distribuer le `.pfx`.
- **Sans `/tr`** : la signature meurt le jour ou ton certif expire. Toujours mettre
  un timestamp.
- **SmartScreen "App de Microsoft Store"** : pas concerne, on signe un exe classique.

## 7. Checklist

- [ ] Generer le certif auto-signe (PowerShell, une fois)
- [ ] Backuper le `.pfx` avec mot de passe dans un endroit sur
- [ ] Noter le thumbprint dans `build.ps1`
- [ ] Verifier `signtool` dispo (sinon Windows SDK)
- [ ] Premier build signe : `.\build.ps1` puis `signtool verify /pa dist\vega_toolbox.exe`
- [ ] Sur les postes clients : `Import-Certificate` du `.cer` (1 fois par poste)
