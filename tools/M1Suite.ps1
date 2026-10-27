param(
    [ValidateSet('Validation','Convergence','Reconstruction')][string]$Stage = 'Validation',
    [string]$EngineRoot = $env:UEFOGLAB_ENGINE_ROOT,
    [string]$Output = ''
)
if (!$EngineRoot) { throw 'Pass -EngineRoot or set UEFOGLAB_ENGINE_ROOT.' }
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path $PSScriptRoot -Parent
if (!$Output) { $Output = Join-Path $ProjectRoot ('Saved/M1/' + $Stage + '-' + (Get-Date -Format 'yyyyMMdd-HHmmss')) }
$Output = [IO.Path]::GetFullPath($Output)
if (Test-Path -LiteralPath $Output) { throw 'Suite output already exists.' }
New-Item -ItemType Directory -Path $Output | Out-Null
$Cases = [ordered]@{}
if ($Stage -eq 'Validation') {
    $Cases.vacuum = @{Width=320;Height=180;Extinction=0;Fixture='Empty'}
    $Cases.uniform = @{DensityMode=0;Fixture='Empty'}
    $Cases.inside = @{DensityMode=0;Fixture='Empty';Camera='Inside'}
    $Cases.occluded = @{DensityMode=0;Fixture='Occluded'}
    $Cases.diagnostic = @{DensityMode=0}
    $Cases.radial = @{}
    $Cases.oblique = @{DensityMode=1;Fixture='Empty';Camera='Oblique'}
    $Cases.sine = @{DensityMode=2}
    $Cases.oddhalf = @{Preset='Medium'}
    $Cases.translated = @{Fixture='Empty';OriginOffset=10000000}
    $Cases.exposure = @{PreExposure=2}
    $Cases.subrect = @{Width=400;Height=300;ConstrainedAspect=2.4}
}
if ($Stage -eq 'Convergence') {
    foreach ($Density in 0,1,2) { foreach ($Count in 256,512,1024) {
        $Cases["d$Density-s$Count"] = @{DensityMode=$Density;Steps=$Count}
    } }
}
if ($Stage -eq 'Reconstruction') {
    foreach ($Size in @(@(640,360),@(321,181))) {
        $Prefix = "$($Size[0])x$($Size[1])"
        $Cases["$Prefix-reference"] = @{Width=$Size[0];Height=$Size[1];Steps=1024}
        $Cases["$Prefix-check512"] = @{Width=$Size[0];Height=$Size[1];Steps=512}
        $Cases["$Prefix-high"] = @{Width=$Size[0];Height=$Size[1]}
        $Cases["$Prefix-medium-bilinear"] = @{Width=$Size[0];Height=$Size[1];Preset='Medium';Bilinear=$true}
        $Cases["$Prefix-medium-guided"] = @{Width=$Size[0];Height=$Size[1];Preset='Medium'}
        $Cases["$Prefix-low-guided"] = @{Width=$Size[0];Height=$Size[1];Preset='Low'}
        $Cases["$Prefix-low-bilinear"] = @{Width=$Size[0];Height=$Size[1];Preset='Low';Bilinear=$true}
    }
}
$Runs = @()
foreach ($Case in $Cases.GetEnumerator()) {
    $Arguments = $Case.Value.Clone()
    $Arguments.EngineRoot=$EngineRoot
    $Arguments.Output=Join-Path $Output $Case.Key
    $Arguments.Label=$Case.Key
    & (Join-Path $PSScriptRoot 'Readback.ps1') @Arguments
    $Runs += @{name=$Case.Key; path=$Arguments.Output}
    @{schemaVersion=1;stage=$Stage;runs=$Runs} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $Output 'suite.json') -Encoding utf8
}
Write-Output "Suite artifacts: $Output"
