param(
    [Parameter(Mandatory)][string]$Mode,
    [string]$Profile = '', [string]$Camera = 'A',
    [int]$Width = 1280, [int]$Height = 720, [int]$Frames = 120,
    [switch]$ZeroLocal,
    [Parameter(Mandatory)][string]$Output,
    [string]$Config = "$PSScriptRoot/m3_config.json",
    [string]$EngineRoot = $env:UEFOGLAB_ENGINE_ROOT,
    [string]$Python = 'python'
)
if (!$EngineRoot) { throw 'Pass -EngineRoot or set UEFOGLAB_ENGINE_ROOT.' }
$ErrorActionPreference = 'Stop'
$RunArgs = @('run', '--mode', $Mode, '--camera', $Camera, '--width', $Width, '--height', $Height, '--frames', $Frames, '--output', $Output, '--config', $Config, '--engine', $EngineRoot)
if ($Profile) { $RunArgs += @('--profile', $Profile) }
if ($ZeroLocal) { $RunArgs += '--zero-local' }
& $Python "$PSScriptRoot/m3_compare.py" @RunArgs
if ($LASTEXITCODE -ne 0) { throw "Comparison failed (exit $LASTEXITCODE): $Output" }
