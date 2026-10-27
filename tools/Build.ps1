param(
    [string]$EngineRoot = $env:UEFOGLAB_ENGINE_ROOT,
    [ValidateSet('Editor', 'Game')][string]$Target = 'Editor'
)
if (!$EngineRoot) { throw 'Pass -EngineRoot or set UEFOGLAB_ENGINE_ROOT.' }
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path $PSScriptRoot -Parent
$BuildTool = Join-Path $EngineRoot 'Engine\Build\BatchFiles\Build.bat'
if (!(Test-Path -LiteralPath $BuildTool)) { throw "Missing UE build tool: $BuildTool" }
$TargetName = if ($Target -eq 'Editor') { 'UEFogLabEditor' } else { 'UEFogLab' }
New-Item -ItemType Directory -Force -Path (Join-Path $ProjectRoot 'Saved\Logs') | Out-Null
& $BuildTool $TargetName Win64 Development "-Project=$ProjectRoot\UEFogLab.uproject" -WaitMutex -NoHotReloadFromIDE "-Log=$ProjectRoot\Saved\Logs\Build-$Target.log"
if ($LASTEXITCODE -ne 0) { throw "UE build failed with exit code $LASTEXITCODE" }
