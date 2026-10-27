param(
    [string]$EngineRoot = $env:UEFOGLAB_ENGINE_ROOT,
    [ValidateSet('High','Medium','Low','Reference')][string]$Preset = 'High',
    [ValidateSet('Prescribed','ManualDirectionalHG')][string]$LightingModel = 'Prescribed',
    [double]$Albedo = 0.8, [double]$HGg = 0.5,
    [string]$IncidentWeight = '4,3,2', [string]$PhotonDirection = '-1,-1,0.15',
    [int]$Width = 321, [int]$Height = 181,
    [ValidateRange(1,1024)][int]$Steps = 64,
    [ValidateRange(0,2)][int]$DensityMode = 1,
    [ValidateRange(0,100)][float]$Extinction = 0.35,
    [ValidateSet('Diagnostic','Empty','Occluded')][string]$Fixture = 'Diagnostic',
    [ValidateSet('Outside','Inside','Oblique')][string]$Camera = 'Outside',
    [float]$OriginOffset = 0,
    [float]$PreExposure = 1,
    [float]$ConstrainedAspect = 0,
    [switch]$Bilinear, [switch]$DebugLayer,
    [ValidateRange(1,10000)][int]$Frame = 30,
    [ValidateRange(1,600)][int]$TimeoutSeconds = 180,
    [string]$Label = 'numeric',
    [string]$Output = ''
)
if (!$EngineRoot) { throw 'Pass -EngineRoot or set UEFOGLAB_ENGINE_ROOT.' }
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path $PSScriptRoot -Parent
. (Join-Path $PSScriptRoot 'Lighting.ps1')
$Lighting = Get-FogLighting $LightingModel $Albedo $HGg $IncidentWeight $PhotonDirection $PSBoundParameters
if ($Width -lt 64 -or $Height -lt 64) { throw 'Resolution must be at least 64x64.' }
if ($Label -notmatch '^[A-Za-z0-9_-]+$') { throw 'Invalid label.' }
if (!$PSBoundParameters.ContainsKey('Steps')) {
    $Steps = switch ($Preset) { 'Low' {32} 'Reference' {512} default {64} }
}
$Divisor = if ($Preset -in @('Medium','Low')) {2} else {1}
$Reconstruction = if ($Bilinear) {0} else {1}
$RunId = (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '-' + $Label
if (!$Output) { $Output = Join-Path $ProjectRoot "Saved\Numerics\$RunId" }
$Output = [IO.Path]::GetFullPath($Output)
$AllowedRoots = @('Saved','out','work') | ForEach-Object { [IO.Path]::GetFullPath((Join-Path $ProjectRoot $_)) + [IO.Path]::DirectorySeparatorChar }
if (!($AllowedRoots | Where-Object { $Output.StartsWith($_,[StringComparison]::OrdinalIgnoreCase) })) { throw 'Output must be below repository Saved/, out/ or work/.' }
if (Test-Path -LiteralPath $Output) { throw "Output already exists; refusing stale artifacts: $Output" }
New-Item -ItemType Directory -Path $Output | Out-Null
$Raw = Join-Path $Output 'raw'
$SourceRevision = & git -C $ProjectRoot rev-parse --verify HEAD
if ($LASTEXITCODE -ne 0) { throw 'A committed source revision is required.' }
$SourceInputs = @(& git -C $ProjectRoot ls-files -- Config Plugins Source tools UEFogLab.uproject) | Where-Object { $_ -notmatch '/Binaries/|/Intermediate/' }
$Hashes = @($SourceInputs | ForEach-Object { @{path=$_; sha256=(Get-FileHash -LiteralPath (Join-Path $ProjectRoot $_) -Algorithm SHA256).Hash.ToLowerInvariant()} })
$Config = [ordered]@{
    schemaVersion=1; runId=$RunId; sourceRevision=$SourceRevision
    sourceWorkingTree=@(& git -C $ProjectRoot status --porcelain)
    sourceInputs=$Hashes; engineRoot=$EngineRoot
    engineBuild=(Get-Content -LiteralPath (Join-Path $EngineRoot 'Engine/Build/Build.version') -Raw | ConvertFrom-Json)
    width=$Width; height=$Height; preset=$Preset; steps=$Steps; downsample=$Divisor
    densityMode=$DensityMode; extinctionPerMetre=$Extinction; fixture=$Fixture; camera=$Camera
    originOffset=$OriginOffset; preExposureRequested=$PreExposure; constrainedAspect=$ConstrainedAspect
    reconstruction=$Reconstruction; frame=$Frame; debugLayer=[bool]$DebugLayer; status='started'
}
function Save-Run { $Config | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $Output 'run.json') -Encoding utf8 }
foreach ($Key in $Lighting.Keys) { $Config[$Key] = $Lighting[$Key] }
Save-Run
$Editor = Join-Path $EngineRoot 'Engine/Binaries/Win64/UnrealEditor-Cmd.exe'
$Start = [Diagnostics.ProcessStartInfo]::new($Editor)
$Start.UseShellExecute = $false
$Start.CreateNoWindow = $true
$Start.WorkingDirectory = $ProjectRoot
$Arguments = @("$ProjectRoot\UEFogLab.uproject", '/Engine/Maps/Entry', '-game', '-dx12', '-unattended', '-RenderOffscreen', '-nosound', '-NoSplash', '-NoLiveCoding', '-windowed', "-ResX=$Width", "-ResY=$Height", "-abslog=$Output\run.log",
    '-FogLabEnabled=1', "-FogLabSteps=$Steps", "-FogLabDownsample=$Divisor", "-FogLabReconstruction=$Reconstruction", "-FogLabDensityMode=$DensityMode", "-FogLabExtinction=$($Extinction.ToString([cultureinfo]::InvariantCulture))", '-FogLabEarlyOut=0',
    "-FogLabFixture=$Fixture", "-FogLabCamera=$Camera", "-FogLabOriginOffset=$($OriginOffset.ToString([cultureinfo]::InvariantCulture))", "-FogLabPreExposure=$($PreExposure.ToString([cultureinfo]::InvariantCulture))", "-FogLabConstrainedAspect=$($ConstrainedAspect.ToString([cultureinfo]::InvariantCulture))",
    "-FogLabReadback=$Raw", "-FogLabReadbackFrame=$Frame", "-FogLabReadbackTimeout=$TimeoutSeconds")
if ($DebugLayer) { $Arguments += '-d3ddebug' }
$Arguments += Get-FogLightingArguments $Lighting
foreach ($Argument in $Arguments) { $Start.ArgumentList.Add($Argument) }
$Process = [Diagnostics.Process]::new()
$Process.StartInfo = $Start
try {
    if (!$Process.Start()) { throw 'Could not start Unreal.' }
    $Config.processId = $Process.Id
    Save-Run
    if (!$Process.WaitForExit(($TimeoutSeconds + 60) * 1000)) {
        $Process.Kill()
        if (!$Process.WaitForExit(10000)) { throw 'Owned Unreal process did not confirm termination.' }
        throw 'Outer process timeout.'
    }
    $Config.exitCode = $Process.ExitCode
    if ($Process.ExitCode -ne 0) { throw "Unreal failed: exit=$($Process.ExitCode). See $Output/run.log" }
    if (Test-Path -LiteralPath (Join-Path $Raw 'failed.txt')) { throw (Get-Content -LiteralPath (Join-Path $Raw 'failed.txt') -Raw) }
    $MetaPath = Join-Path $Raw 'metadata.json'
    if (!(Test-Path -LiteralPath $MetaPath)) { throw 'Missing completion metadata.' }
    $Meta = Get-Content -LiteralPath $MetaPath -Raw | ConvertFrom-Json
    if ($Meta.status -ne 'complete' -or $Meta.schemaVersion -ne $Config.schemaVersion -or $Meta.steps -ne $Steps -or $Meta.downsample -ne $Divisor -or $Meta.densityMode -ne $DensityMode -or [Math]::Abs($Meta.extinctionPerMetre - $Extinction) -gt 1e-6) { throw 'Captured configuration mismatch.' }
    Assert-FogLightingSnapshot $Lighting $Meta
    $Config.artifacts = @('metadata.json') + @($Meta.files.file) | ForEach-Object {
        $Path = Join-Path $Raw $_
        if (!(Test-Path -LiteralPath $Path)) { throw "Missing output $_" }
        @{path="raw/$_"; bytes=(Get-Item -LiteralPath $Path).Length; sha256=(Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()}
    }
    foreach ($File in $Meta.files) {
        if ((Get-Item -LiteralPath (Join-Path $Raw $File.file)).Length -ne $File.bytes) { throw 'Raw data byte count mismatch.' }
    }
    $Config.status = 'complete'
    Save-Run
    Write-Output "Readback artifacts: $Output"
} catch {
    $Config.status = 'failed'; $Config.error = $_.Exception.Message; Save-Run
    throw
} finally {
    if ($Process.Id -and !$Process.HasExited) { $Process.Kill(); [void]$Process.WaitForExit(10000) }
    $Process.Dispose()
}
