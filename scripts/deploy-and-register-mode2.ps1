<#
.SYNOPSIS
  Deploy & register CAMRIE Mode 2 (user-owned compute) - Windows PowerShell.

.DESCRIPTION
  Thin wrapper around worker\manage.py: deploys the worker stack into YOUR AWS
  account and registers it with CloudMR Brain so it shows up in the CAMRIE
  frontend's computing-unit list. The CloudMR password is asked with a hidden
  prompt unless CLOUDMR_PASSWORD is set.

.EXAMPLE
  .\scripts\deploy-and-register-mode2.ps1 -Profile my-aws-profile -Alias "My Lab Worker"

.EXAMPLE
  # If script execution is blocked:
  powershell -ExecutionPolicy Bypass -File .\scripts\deploy-and-register-mode2.ps1 -Profile my-aws-profile

.NOTES
  Manage afterwards:  python worker\manage.py status|logs|costs|teardown --profile <p>
  GUI:                python worker\manage.py
#>
param(
    [Alias("Profile")][string]$AwsProfile = $env:AWS_PROFILE,
    [string]$Region = "us-east-1",
    [string]$Email = $env:CLOUDMR_EMAIL,
    [string]$Alias,
    [string]$Python
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Manage = Join-Path $RepoRoot "worker\manage.py"

if (-not $Python) {
    foreach ($c in @("python", "py", "python3")) {
        if (Get-Command $c -ErrorAction SilentlyContinue) { $Python = $c; break }
    }
}
if (-not $Python) { throw "Python 3.9+ not found. Install it or pass -Python C:\path\to\python.exe" }

& $Python -c "import boto3, requests" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[INFO] Installing worker manager dependencies (boto3, requests)..."
    & $Python -m pip install --quiet -r (Join-Path $RepoRoot "worker\requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
}

if (-not $AwsProfile) { $AwsProfile = Read-Host "AWS CLI profile" }

# Build the argument list as an array so values with spaces (alias) stay intact.
$argv = @("-u", $Manage, "deploy", "--profile", $AwsProfile, "--region", $Region)
if ($Email) { $argv += @("--email", $Email) }
if ($Alias) { $argv += @("--alias", $Alias) }

& $Python @argv
exit $LASTEXITCODE
