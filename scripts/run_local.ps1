<#
.SYNOPSIS
  Run PolyComic directly on Windows (no Docker).
.DESCRIPTION
  Creates .venv if missing, installs requirements, loads .env into this process,
  runs Alembic migrations and starts uvicorn on 127.0.0.1.
  DATABASE_URL in .env must use the postgresql+asyncpg:// scheme.
.EXAMPLE
  ./scripts/run_local.ps1 -Reload
  ./scripts/run_local.ps1 -Fake -Reload      # offline, fake image provider
  ./scripts/run_local.ps1 -Port 8001         # overrides PORT from .env (default 7659)
  powershell -ExecutionPolicy Bypass -File scripts/run_local.ps1
#>
[CmdletBinding()]
param(
    [switch]$Dev,
    [switch]$Reload,
    [switch]$Fake,
    [switch]$SkipMigrate,
    # Priority: -Port, then PORT (environment / .env), then 7659.
    [int]$Port
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Fail([string]$msg) {
    [Console]::Error.WriteLine("ERROR: $msg")
    exit 1
}

$EnvFile = Join-Path $Root '.env'
if (-not (Test-Path $EnvFile)) {
    Fail "No .env file found at $EnvFile. Copy .env.example to .env (Copy-Item .env.example .env) and fill it in."
}

# --- virtualenv -----------------------------------------------------------
$VenvPython = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $VenvPython)) {
    Write-Host 'Creating .venv ...'
    $created = $false
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($ver in '-3.13', '-3.12') {
            & py $ver -c "import sys" 2>$null
            if ($LASTEXITCODE -eq 0) {
                & py $ver -m venv .venv
                if ($LASTEXITCODE -eq 0) { $created = $true; break }
            }
        }
    }
    if (-not $created) {
        if (Get-Command python -ErrorAction SilentlyContinue) {
            & python -m venv .venv
            if ($LASTEXITCODE -ne 0) { Fail 'python -m venv failed.' }
        } else {
            Fail 'No suitable Python found (tried py -3.13, py -3.12, python). Install Python 3.12+.'
        }
    }
}

Write-Host 'Installing requirements ...'
& $VenvPython -m pip install --disable-pip-version-check -q -r requirements.txt
if ($LASTEXITCODE -ne 0) { Fail 'pip install failed.' }
if ($Dev) {
    & $VenvPython -m pip install --disable-pip-version-check -q -r requirements-dev.txt
    if ($LASTEXITCODE -ne 0) { Fail 'pip install (dev) failed.' }
}

# --- load .env (values are never printed) ----------------------------------
$count = 0
foreach ($line in Get-Content -LiteralPath $EnvFile) {
    $t = $line.Trim()
    if ($t -eq '' -or $t.StartsWith('#')) { continue }
    if ($t -match '^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$') {
        $name = $Matches[1]
        $val = $Matches[2].Trim()
        if ($val.Length -ge 2 -and (
                ($val.StartsWith('"') -and $val.EndsWith('"')) -or
                ($val.StartsWith("'") -and $val.EndsWith("'")))) {
            $val = $val.Substring(1, $val.Length - 2)
        }
        [Environment]::SetEnvironmentVariable($name, $val, 'Process')
        $count++
    }
}
Write-Host "Loaded $count variables from .env"

if (-not $PSBoundParameters.ContainsKey('Port')) {
    if ($env:PORT) {
        $parsed = 0
        if (-not [int]::TryParse($env:PORT.Trim(), [ref]$parsed)) {
            Fail "PORT must be a number (got '$($env:PORT)')."
        }
        $Port = $parsed
    } else {
        $Port = 7659
    }
}

if ($Fake) {
    $env:IMAGE_PROVIDER = 'fake'
    Write-Host 'IMAGE_PROVIDER=fake (offline mode)'
}

Write-Host 'Note: DATABASE_URL must use the postgresql+asyncpg:// scheme.'

# --- migrate + serve --------------------------------------------------------
if (-not $SkipMigrate) {
    Write-Host 'Running alembic upgrade head ...'
    & $VenvPython -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) {
        Fail 'Migration failed. Is PostgreSQL running and DATABASE_URL correct?'
    }
}

$uvArgs = @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', "$Port")
if ($Reload) { $uvArgs += '--reload' }
Write-Host "Starting http://127.0.0.1:$Port (Ctrl+C to stop)"
& $VenvPython @uvArgs
exit $LASTEXITCODE
