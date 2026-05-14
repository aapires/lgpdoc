#Requires -Version 5.1
<#
.SYNOPSIS
    Bootstrap do LGPDoc em Windows 10/11. Instala dependencias via winget,
    clona o repo, cria venv e instala dependencias Python + Node.

.PARAMETER WithOpf
    Instala tambem o extra [ml] (torch + transformers + opf). Sem essa flag,
    a aplicacao roda em modo regex (mais leve, sem download de modelo).

.PARAMETER WithOcr
    Instala o Tesseract OCR via winget. Necessario para processar PDFs
    escaneados e imagens. (Poppler precisa de download manual - ver mensagem
    no fim da execucao.)

.PARAMETER RepoPath
    Pasta de destino do clone. Default: %USERPROFILE%\lgpdoc.

.PARAMETER SkipClone
    Usa o diretorio atual (ja clonado) em vez de clonar de novo.

.EXAMPLE
    .\install.ps1
    .\install.ps1 -WithOcr
    .\install.ps1 -WithOpf -WithOcr -RepoPath "D:\projetos\lgpdoc"
    .\install.ps1 -SkipClone    # do diretorio do repo ja clonado
#>
[CmdletBinding()]
param(
    [switch]$WithOpf,
    [switch]$WithOcr,
    [string]$RepoPath = (Join-Path $env:USERPROFILE "lgpdoc"),
    [switch]$SkipClone
)

$ErrorActionPreference = "Stop"
$ProgressPreference    = "SilentlyContinue"

function Write-Step($msg) {
    Write-Host ""
    Write-Host ">> $msg" -ForegroundColor Cyan
}

function Write-Ok($msg)   { Write-Host "   OK $msg" -ForegroundColor Green }
function Write-Skip($msg) { Write-Host "   -- $msg" -ForegroundColor DarkGray }
function Write-Warn($msg) { Write-Host "   !! $msg" -ForegroundColor Yellow }

function Test-Command($name) {
    return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

function Refresh-Path {
    $machinePath = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
    $userPath    = [System.Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path    = "$machinePath;$userPath"
}

function Assert-Winget {
    if (-not (Test-Command "winget")) {
        Write-Host ""
        Write-Host "ERRO: winget nao encontrado." -ForegroundColor Red
        Write-Host "Instale 'App Installer' pela Microsoft Store:"
        Write-Host "  https://apps.microsoft.com/detail/9NBLGGH4NNS1"
        Write-Host "Depois reabra o PowerShell e rode esse script de novo."
        exit 1
    }
}

function Install-WingetPackage($id, $friendly) {
    Write-Step "Instalando $friendly ($id)"
    $listing = winget list --id $id --exact --accept-source-agreements 2>$null
    if ($LASTEXITCODE -eq 0 -and ($listing | Select-String -SimpleMatch $id)) {
        Write-Skip "$friendly ja instalado."
        return
    }
    winget install --id $id --exact --silent --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        throw "Falha ao instalar $friendly via winget (exit $LASTEXITCODE)."
    }
    Refresh-Path
    Write-Ok "$friendly instalado."
}

# -------------------------------------------------------------------
# 1. Validacoes iniciais
# -------------------------------------------------------------------
Write-Step "Verificando ambiente"
$winVer = [System.Environment]::OSVersion.Version
if ($winVer.Major -lt 10) {
    throw "Windows 10 ou superior e necessario (detectado: $winVer)."
}
Assert-Winget
Write-Ok "Windows $($winVer.Major).$($winVer.Build), winget disponivel."

# -------------------------------------------------------------------
# 2. Instala Python, Node e Git via winget
# -------------------------------------------------------------------
Install-WingetPackage "Python.Python.3.11" "Python 3.11"
Install-WingetPackage "OpenJS.NodeJS.LTS"  "Node.js 20 LTS"
Install-WingetPackage "Git.Git"            "Git for Windows"

if ($WithOcr) {
    Install-WingetPackage "UB-Mannheim.TesseractOCR" "Tesseract OCR"
}

Refresh-Path

# -------------------------------------------------------------------
# 3. Verifica versoes pos-instalacao
# -------------------------------------------------------------------
Write-Step "Validando versoes instaladas"
foreach ($cmd in @("python", "node", "npm", "git")) {
    if (-not (Test-Command $cmd)) {
        throw "Comando '$cmd' nao encontrado no PATH apos instalacao. Feche e reabra o PowerShell, depois rode o script de novo."
    }
}
$pyVer = (& python --version 2>&1) -join " "
if ($pyVer -notmatch "^Python\s+3\.(1[1-9]|[2-9]\d)") {
    Write-Host ""
    Write-Host "ERRO: 'python' nao retorna versao 3.11+ valida." -ForegroundColor Red
    Write-Host "Output recebido: '$pyVer'"
    Write-Host ""
    Write-Host "Causa provavel: a stub 'python.exe' da Microsoft Store esta"
    Write-Host "interceptando o comando antes do Python real instalado via winget."
    Write-Host ""
    Write-Host "Como resolver:"
    Write-Host "  Configuracoes > Aplicativos > Configuracoes avancadas de aplicativo"
    Write-Host "    > Aliases de execucao de aplicativo"
    Write-Host "  Desative 'python.exe' e 'python3.exe'."
    Write-Host "Depois reabra o PowerShell e rode o script de novo com -SkipClone."
    exit 1
}
$nodeVer = (& node --version 2>&1) -join " "
Write-Ok "$pyVer / Node $nodeVer"

# -------------------------------------------------------------------
# 4. Clone do repo
# -------------------------------------------------------------------
if ($SkipClone) {
    $RepoPath = (Get-Location).Path
    Write-Step "Usando diretorio atual: $RepoPath"
    if (-not (Test-Path (Join-Path $RepoPath "pyproject.toml"))) {
        throw "Diretorio atual nao parece ser o repositorio LGPDoc (sem pyproject.toml)."
    }
} else {
    Write-Step "Clonando o repositorio em $RepoPath"
    if (Test-Path $RepoPath) {
        Write-Skip "Pasta ja existe - pulando clone. Rode 'git pull' manualmente se quiser atualizar."
    } else {
        git clone "https://github.com/aapires/lgpdoc.git" $RepoPath
        if ($LASTEXITCODE -ne 0) { throw "git clone falhou." }
        Write-Ok "Clonado."
    }
}

Set-Location $RepoPath

# -------------------------------------------------------------------
# 5. venv + dependencias Python
# -------------------------------------------------------------------
Write-Step "Criando virtualenv .venv"
if (-not (Test-Path ".venv")) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Falha ao criar venv." }
    Write-Ok "venv criado."
} else {
    Write-Skip ".venv ja existe."
}

$venvPython = Join-Path $RepoPath ".venv\Scripts\python.exe"
$venvPip    = Join-Path $RepoPath ".venv\Scripts\pip.exe"

Write-Step "Atualizando pip"
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade falhou." }

$extras = "dev,api,ocr"
if ($WithOpf) { $extras = "$extras,ml" }

Write-Step "Instalando dependencias Python [$extras]"
if ($WithOpf) {
    Write-Warn "Modo -WithOpf: torch + transformers serao baixados (~2 GB). Pode demorar 5-10 min."
}
& $venvPip install -e ".[$extras]"
if ($LASTEXITCODE -ne 0) { throw "pip install falhou." }
Write-Ok "Backend instalado."

# -------------------------------------------------------------------
# 6. npm install
# -------------------------------------------------------------------
Write-Step "Instalando dependencias do frontend (npm install)"
Push-Location (Join-Path $RepoPath "apps\reviewer-ui")
try {
    npm install
    if ($LASTEXITCODE -ne 0) { throw "npm install falhou." }
} finally {
    Pop-Location
}
Write-Ok "Frontend instalado."

# -------------------------------------------------------------------
# 7. Sucesso
# -------------------------------------------------------------------
Write-Host ""
Write-Host "===========================================================" -ForegroundColor Green
Write-Host " LGPDoc pronto em: $RepoPath" -ForegroundColor Green
Write-Host "===========================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Para subir a aplicacao, abra DOIS terminais PowerShell na pasta acima:"
Write-Host ""
Write-Host "  Terminal 1 (backend / API):" -ForegroundColor Cyan
if (-not $WithOpf) {
    Write-Host '    $env:ANONYMIZER_API_USE_MOCK_CLIENT = "true"' -ForegroundColor Yellow
}
Write-Host "    .\.venv\Scripts\python -m uvicorn scripts.run_api:app --host 127.0.0.1 --port 9000" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Terminal 2 (frontend / UI):" -ForegroundColor Cyan
Write-Host "    cd apps\reviewer-ui" -ForegroundColor Yellow
Write-Host "    npm run dev" -ForegroundColor Yellow
Write-Host ""
Write-Host "Depois abra http://localhost:3000/jobs no navegador."
Write-Host ""

if ($WithOcr) {
    Write-Host "OCR - passo manual restante:" -ForegroundColor Yellow
    Write-Host "  Poppler (binarios do PDF) nao tem pacote winget oficial."
    Write-Host "  Baixe o release mais recente em:"
    Write-Host "    https://github.com/oschwartz10612/poppler-windows/releases"
    Write-Host "  Descompacte em C:\poppler e adicione 'C:\poppler\Library\bin' ao PATH."
    Write-Host ""
}
