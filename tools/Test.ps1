param([string]$EngineRoot = $env:UEFOGLAB_ENGINE_ROOT)
if (!$EngineRoot) { throw 'Pass -EngineRoot or set UEFOGLAB_ENGINE_ROOT.' }
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path $PSScriptRoot -Parent
$Report = Join-Path $ProjectRoot ('Saved\Tests\' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Force -Path $Report | Out-Null
& (Join-Path $EngineRoot 'Engine\Binaries\Win64\UnrealEditor-Cmd.exe') "$ProjectRoot\UEFogLab.uproject" -unattended -NullRHI -nosound '-ExecCmds=Automation RunTests UEFogLab' '-TestExit=Automation Test Queue Empty' "-ReportExportPath=$Report" "-abslog=$Report\tests.log"
if ($LASTEXITCODE -ne 0) { throw "Automation failed with exit code $LASTEXITCODE. Inspect $Report" }
$Index = Join-Path $Report 'index.json'
if (!(Test-Path -LiteralPath $Index)) { throw "Automation did not produce a report: $Index" }
$Result = Get-Content -LiteralPath $Index -Raw | ConvertFrom-Json
if ($Result.succeeded -lt 1 -or $Result.failed -gt 0) { throw "No passing test or failed tests in $Index" }
Write-Output "Automation report: $Report"
