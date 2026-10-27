# Gera dist\TrampoTime-piloto.zip com o TrampoTime.exe, o .env de exemplo e o LEIA-ME.
# Rode na pasta do projeto, com a .venv criada:  .\empacotar\gerar_exe.ps1
$ErrorActionPreference = "Stop"
$raiz = Split-Path $PSScriptRoot -Parent
Set-Location $raiz
$python = Join-Path $raiz ".venv\Scripts\python.exe"

& $python -m pip install -q pyinstaller tzdata
& $python -m PyInstaller --noconfirm --clean --onefile --console --name TrampoTime `
    --paths $raiz `
    --add-data "$raiz\trampotime\pagina.html;trampotime" `
    --collect-data tzdata `
    --collect-data googleapiclient `
    --hidden-import googleapiclient.discovery `
    --hidden-import google_auth_oauthlib.flow `
    --hidden-import google.oauth2.credentials `
    --distpath dist --workpath build --specpath build `
    empacotar\lancador.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller falhou." }

$pacote = Join-Path $raiz "dist\TrampoTime-piloto"
if (Test-Path $pacote) { Remove-Item -Recurse -Force $pacote }
New-Item -ItemType Directory $pacote | Out-Null
Copy-Item dist\TrampoTime.exe $pacote
Copy-Item .env.example (Join-Path $pacote ".env")
Copy-Item empacotar\LEIA-ME.txt $pacote
$zip = Join-Path $raiz "dist\TrampoTime-piloto.zip"
if (Test-Path $zip) { Remove-Item -Force $zip }
Compress-Archive -Path "$pacote\*" -DestinationPath $zip
Write-Host "Pronto: $zip"
