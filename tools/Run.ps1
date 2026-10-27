param(
    [string]$EngineRoot = $env:UEFOGLAB_ENGINE_ROOT,
    [ValidateSet('Off', 'High', 'Medium', 'Low', 'Reference')][string]$Preset = 'High',
    [ValidateSet('Prescribed','ManualDirectionalHG')][string]$LightingModel = 'Prescribed',
    [double]$Albedo = 0.8, [double]$HGg = 0.5,
    [string]$IncidentWeight = '4,3,2', [string]$PhotonDirection = '-1,-1,0.15',
    [switch]$Bilinear,
    [switch]$Capture,
    [int]$Width = 1280,
    [int]$Height = 720,
    [string]$Label = '',
    [string[]]$ExtraArguments = @()
)
if (!$EngineRoot) { throw 'Pass -EngineRoot or set UEFOGLAB_ENGINE_ROOT.' }
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path $PSScriptRoot -Parent
. (Join-Path $PSScriptRoot 'Lighting.ps1')
$Lighting = Get-FogLighting $LightingModel $Albedo $HGg $IncidentWeight $PhotonDirection $PSBoundParameters
$Editor = Join-Path $EngineRoot 'Engine\Binaries\Win64\UnrealEditor-Cmd.exe'
if (!(Test-Path -LiteralPath $Editor)) { throw "Missing UnrealEditor-Cmd: $Editor" }
if ($Width -lt 64 -or $Height -lt 64) { throw 'Resolution must be at least 64x64.' }
if ($Label -and $Label -notmatch '^[A-Za-z0-9_-]+$') { throw 'Label accepts letters, numbers, underscores and hyphens.' }
$Divisor = if ($Preset -in @('Medium', 'Low')) { 2 } else { 1 }
$Steps = switch ($Preset) { 'Low' { 32 } 'Reference' { 512 } default { 64 } }
$Enabled = if ($Preset -eq 'Off') { 0 } else { 1 }
$Reconstruction = if ($Bilinear) { 0 } else { 1 }
$RunId = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + $Preset
if ($Label) { $RunId += '-' + $Label }
$EvidenceRoot = Join-Path $ProjectRoot "Saved\Evidence\$RunId"
New-Item -ItemType Directory -Force -Path $EvidenceRoot | Out-Null
$Commands = "r.UEFog.Enabled $Enabled,r.UEFog.Steps $Steps,r.UEFog.Downsample $Divisor,r.UEFog.Reconstruction $Reconstruction"
$SourceRevision = & git -C $ProjectRoot rev-parse --verify HEAD 2>$null
if ($LASTEXITCODE -ne 0) { $SourceRevision = $null }
$SourceState = @(& git -C $ProjectRoot status --porcelain)
$Arguments = @("$ProjectRoot\UEFogLab.uproject", '/Engine/Maps/Entry', '-game', '-dx12', '-windowed', "-ResX=$Width", "-ResY=$Height", '-nosound', '-NoSplash', '-NoLiveCoding', "-abslog=$EvidenceRoot\run.log", "-ExecCmds=$Commands")
$Arguments += $ExtraArguments
$Arguments += @("-FogLabEnabled=$Enabled", "-FogLabSteps=$Steps", "-FogLabDownsample=$Divisor", "-FogLabReconstruction=$Reconstruction")
$Arguments += Get-FogLightingArguments $Lighting
if ($Capture) { $Arguments += @('-unattended', '-RenderOffscreen', "-FogLabCapture=$EvidenceRoot\frame.png", '-FogLabFrames=120') }
$Config = @{
    schemaVersion = 1
    preset = $Preset
    width = $Width
    height = $Height
    fogDivisor = $Divisor
    steps = $Steps
    reconstruction = $Reconstruction
    engineRoot = $EngineRoot
    engineBuild = (Get-Content (Join-Path $EngineRoot 'Engine\Build\Build.version') -Raw | ConvertFrom-Json)
    sourceRevision = $SourceRevision
    sourceWorkingTree = $SourceState
    capture = [bool]$Capture
    extraArguments = $ExtraArguments
    model = 'prescribed constant source function; no UE light integration or volume shadows'
}
foreach ($Key in $Lighting.Keys) { $Config[$Key] = $Lighting[$Key] }
if ($Lighting.Count) { $Config.model = 'manual directional HG; light-path transmittance/visibility=1; no UE light integration or volume shadows' }
$Config | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $EvidenceRoot 'config.json') -Encoding utf8
& $Editor @Arguments
if ($LASTEXITCODE -ne 0) { throw "Unreal exited with code $LASTEXITCODE. Inspect $EvidenceRoot\run.log" }
if ($Capture -and !(Test-Path -LiteralPath (Join-Path $EvidenceRoot 'frame.png'))) { throw "No screenshot was produced. Inspect $EvidenceRoot\run.log" }
Write-Output "Run artifacts: $EvidenceRoot"
