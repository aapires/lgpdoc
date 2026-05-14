#Requires -Version 5.1
<#
.SYNOPSIS
    Sobe backend (FastAPI) e frontend (Next.js) do LGPDoc em um unico
    comando. Paridade Windows do start-anom.sh.

.PARAMETER Mock
    Roda em modo regex-only (sem download/load do OPF).

.PARAMETER NoUi
    Sobe apenas o backend (API + Swagger).

.PARAMETER Reset
    Apaga o diretorio var\ antes de subir (zera quarentena + DB).

.PARAMETER Port
    Porta do backend (default 9000).

.PARAMETER UiPort
    Porta da UI (default 3000).

.PARAMETER BindHost
    Endereco de bind do backend (default 127.0.0.1).

.PARAMETER OpenBrowser
    Abre o navegador automaticamente na URL da UI quando tudo estiver pronto.

.EXAMPLE
    .\scripts\start-anom.ps1
    .\scripts\start-anom.ps1 -Mock
    .\scripts\start-anom.ps1 -Mock -NoUi
    .\scripts\start-anom.ps1 -Reset -OpenBrowser
#>
[CmdletBinding()]
param(
    [switch]$Mock,
    [switch]$NoUi,
    [switch]$Reset,
    [int]$Port = 9000,
    [int]$UiPort = 3000,
    [string]$BindHost = "127.0.0.1",
    [switch]$OpenBrowser
)

$ErrorActionPreference = "Stop"

function Write-Step($msg) { Write-Host ""; Write-Host ">> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "   OK $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "   !! $msg" -ForegroundColor Yellow }

# -------------------------------------------------------------------
# Resolve repo root (script vive em scripts\, repo eh o parent)
# -------------------------------------------------------------------
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$UiDir      = Join-Path $RepoRoot "apps\reviewer-ui"
$VarDir     = Join-Path $RepoRoot "var"
$LogDir     = Join-Path $VarDir   "logs"
$ApiLog     = Join-Path $LogDir   "api.log"
$UiLog      = Join-Path $LogDir   "ui.log"

# -------------------------------------------------------------------
# Validacoes
# -------------------------------------------------------------------
if (-not (Test-Path $VenvPython)) {
    Write-Host "ERRO: venv nao encontrado em $VenvPython" -ForegroundColor Red
    Write-Host "Rode scripts\install.ps1 primeiro (ou instale manualmente)."
    exit 1
}
if (-not $NoUi) {
    if (-not (Test-Path (Join-Path $UiDir "node_modules"))) {
        Write-Host "ERRO: node_modules nao encontrado em apps\reviewer-ui" -ForegroundColor Red
        Write-Host "Rode 'npm install' la dentro, ou execute scripts\install.ps1."
        exit 1
    }
    $npmCmd = (Get-Command npm.cmd -ErrorAction SilentlyContinue)
    if (-not $npmCmd) { $npmCmd = (Get-Command npm -ErrorAction SilentlyContinue) }
    if (-not $npmCmd) {
        Write-Host "ERRO: npm nao encontrado no PATH. Use -NoUi para subir so o backend." -ForegroundColor Red
        exit 1
    }
}

# -------------------------------------------------------------------
# Reset opcional + diretorios runtime
# -------------------------------------------------------------------
if ($Reset -and (Test-Path $VarDir)) {
    Write-Step "Apagando $VarDir"
    Remove-Item -Recurse -Force $VarDir
}
New-Item -ItemType Directory -Force -Path (Join-Path $VarDir "quarantine") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $VarDir "output")     | Out-Null
New-Item -ItemType Directory -Force -Path $LogDir                          | Out-Null

# -------------------------------------------------------------------
# Env vars (paridade com start-anom.sh)
# -------------------------------------------------------------------
$env:ANONYMIZER_API_QUARANTINE_DIR = Join-Path $VarDir "quarantine"
$env:ANONYMIZER_API_OUTPUT_DIR     = Join-Path $VarDir "output"
$env:ANONYMIZER_API_DB_URL         = "sqlite:///" + (Join-Path $VarDir "anonymizer_api.db")
$env:ANONYMIZER_API_POLICY_PATH    = Join-Path $RepoRoot "policies\default.yaml"
$env:ANONYMIZER_API_MAX_BYTES      = "52428800"
$env:ANONYMIZER_API_USE_MOCK_CLIENT = if ($Mock) { "true" } else { "false" }

# -------------------------------------------------------------------
# Configura .env.local da UI para apontar pra essa API
# -------------------------------------------------------------------
if (-not $NoUi) {
    $envLocalPath = Join-Path $UiDir ".env.local"
    $envLocalContent = @"
NEXT_PUBLIC_API_BASE_URL=http://${BindHost}:${Port}
NEXT_PUBLIC_USE_MOCKS=false
"@
    Set-Content -Path $envLocalPath -Value $envLocalContent -Encoding ASCII
}

# -------------------------------------------------------------------
# Banner
# -------------------------------------------------------------------
$detectorDesc = if ($Mock) { "mock (regex local - nao baixa modelo)" } else { "OPF disponivel via toggle (modelo ~3 GB no 1o uso)" }
Write-Host ""
Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host " LGPDoc - stack completa" -ForegroundColor Cyan
Write-Host "------------------------------------------------------------------" -ForegroundColor Cyan
Write-Host " API          : http://${BindHost}:${Port}"
Write-Host " Swagger      : http://${BindHost}:${Port}/docs"
Write-Host " Detector     : $detectorDesc"
if (-not $NoUi) { Write-Host " Interface    : http://localhost:${UiPort}/jobs" }
Write-Host " Quarentena   : $($env:ANONYMIZER_API_QUARANTINE_DIR)"
Write-Host " Saida        : $($env:ANONYMIZER_API_OUTPUT_DIR)"
Write-Host " DB           : $($env:ANONYMIZER_API_DB_URL)"
Write-Host " Logs         : $LogDir"
Write-Host "==================================================================" -ForegroundColor Cyan
if ($Mock) {
    Write-Host " Para usar OPF: rode sem -Mock (precisa do extra [ml] instalado)" -ForegroundColor DarkGray
}
Write-Host " Ctrl+C encerra os dois processos."
Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host ""

# -------------------------------------------------------------------
# Spawn + cleanup
# -------------------------------------------------------------------
$script:Procs = @()

function Stop-Procs {
    Write-Host ""
    Write-Host ">> encerrando..." -ForegroundColor Yellow
    foreach ($p in $script:Procs) {
        if ($p -and -not $p.HasExited) {
            # taskkill /T mata a arvore inteira (uvicorn workers, node children)
            cmd /c "taskkill /F /T /PID $($p.Id)" 2>$null | Out-Null
        }
    }
}

function Wait-ForUrl($url, $timeoutSec, $proc, $label) {
    $iterations = [int]($timeoutSec * 4)  # checa a cada 250ms
    for ($i = 0; $i -lt $iterations; $i++) {
        try {
            $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 1 -ErrorAction Stop
            if ($r.StatusCode -lt 500) { return $true }
        } catch { }
        if ($proc.HasExited) {
            Write-Host "ERRO: $label morreu durante o boot." -ForegroundColor Red
            $logPath = if ($label -eq "API") { $ApiLog } else { $UiLog }
            if (Test-Path $logPath) {
                Write-Host "--- ultimas 30 linhas de $logPath ---" -ForegroundColor DarkGray
                Get-Content $logPath -Tail 30
            }
            return $false
        }
        Start-Sleep -Milliseconds 250
    }
    return $false
}

try {
    # ---------------- API ----------------
    Write-Step "subindo API (FastAPI)"
    $apiArgs = @(
        "-m", "uvicorn",
        "scripts.run_api:app",
        "--host", $BindHost,
        "--port", "$Port",
        "--log-level", "info"
    )
    $apiProc = Start-Process -FilePath $VenvPython -ArgumentList $apiArgs `
        -WorkingDirectory $RepoRoot -NoNewWindow -PassThru `
        -RedirectStandardOutput $ApiLog -RedirectStandardError "$ApiLog.err"
    $script:Procs += $apiProc

    if (-not (Wait-ForUrl "http://${BindHost}:${Port}/health" 30 $apiProc "API")) {
        Stop-Procs
        exit 1
    }
    Write-Ok "API pronta em http://${BindHost}:${Port}"

    # ---------------- UI ----------------
    if (-not $NoUi) {
        Write-Step "subindo UI (Next.js, primeira compilacao pode levar ~10-30s)"
        $env:PORT = "$UiPort"
        $uiProc = Start-Process -FilePath $npmCmd.Source -ArgumentList @("run", "dev") `
            -WorkingDirectory $UiDir -NoNewWindow -PassThru `
            -RedirectStandardOutput $UiLog -RedirectStandardError "$UiLog.err"
        $script:Procs += $uiProc

        if (-not (Wait-ForUrl "http://localhost:${UiPort}" 90 $uiProc "UI")) {
            Stop-Procs
            exit 1
        }
        Write-Ok "UI pronta em http://localhost:${UiPort}"

        if ($OpenBrowser) {
            Start-Process "http://localhost:${UiPort}/jobs" | Out-Null
        }
    }

    Write-Host ""
    Write-Host "==================================================================" -ForegroundColor Green
    Write-Host " Tudo no ar." -ForegroundColor Green
    if (-not $NoUi) {
        Write-Host "   Acesse: http://localhost:${UiPort}/jobs" -ForegroundColor Green
    } else {
        Write-Host "   Acesse: http://${BindHost}:${Port}/docs" -ForegroundColor Green
    }
    Write-Host " Para ver logs em tempo real, abra outro PowerShell e rode:" -ForegroundColor DarkGray
    Write-Host "   Get-Content '$ApiLog' -Wait -Tail 20" -ForegroundColor DarkGray
    if (-not $NoUi) {
        Write-Host "   Get-Content '$UiLog' -Wait -Tail 20" -ForegroundColor DarkGray
    }
    Write-Host " Ctrl+C aqui encerra tudo." -ForegroundColor Green
    Write-Host "==================================================================" -ForegroundColor Green

    # Monitora processos. Se algum morrer, encerra o outro tambem.
    while ($true) {
        foreach ($p in $script:Procs) {
            if ($p.HasExited) {
                Write-Host ""
                Write-Warn "processo PID $($p.Id) terminou (exit $($p.ExitCode))"
                throw "Encerrando porque um dos processos terminou."
            }
        }
        Start-Sleep -Seconds 1
    }
}
finally {
    Stop-Procs
}
