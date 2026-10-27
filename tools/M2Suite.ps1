param(
    [ValidateSet('Validation','Convergence','Reconstruction')][string]$Stage = 'Validation',
    [string]$EngineRoot = $env:UEFOGLAB_ENGINE_ROOT,
    [string]$Output = '', [switch]$DebugLayer
)
if (!$EngineRoot) { throw 'Pass -EngineRoot or set UEFOGLAB_ENGINE_ROOT.' }
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path $PSScriptRoot -Parent
if (!$Output) { $Output = Join-Path $ProjectRoot ('Saved/M2/' + $Stage + '-' + (Get-Date -Format 'yyyyMMdd-HHmmss')) }
$Output = [IO.Path]::GetFullPath($Output)
if (Test-Path -LiteralPath $Output) { throw 'Suite output already exists.' }
New-Item -ItemType Directory -Path $Output | Out-Null
$Matrix = Get-Content (Join-Path $PSScriptRoot 'm2_cases.json') -Raw | ConvertFrom-Json -AsHashtable
$Cases = [ordered]@{}
function Arguments($Overrides) {
    $Result = $Matrix.defaults.Clone()
    foreach ($Key in $Overrides.Keys) { $Result[$Key] = $Overrides[$Key] }
    $Result.LightingModel='ManualDirectionalHG'
    return $Result
}
if ($Stage -eq 'Validation') {
    foreach ($Case in $Matrix.validation) { $Cases[$Case.id] = Arguments $Case.overrides }
}
if ($Stage -eq 'Convergence') {
    foreach ($Case in $Matrix.convergence) { foreach ($Count in 256,512,1024) {
        $A = Arguments $Case.overrides; $A.Steps=$Count; $A.Preset='High'
        $Cases["$($Case.id)-s$Count"]=$A
    } }
}
if ($Stage -eq 'Reconstruction') {
    foreach ($Size in $Matrix.reconstruction.sizes) {
        foreach ($Variant in 'reference','check512','high','medium-bilinear','medium-guided','low-bilinear','low-guided') {
            $A=Arguments $Matrix.reconstruction.overrides; $A.Width=$Size[0]; $A.Height=$Size[1]
            if ($Variant -eq 'reference') { $A.Steps=1024 }
            if ($Variant -eq 'check512') { $A.Steps=512 }
            if ($Variant.StartsWith('medium')) { $A.Preset='Medium' }
            if ($Variant.StartsWith('low')) { $A.Preset='Low'; $A.Steps=32 }
            if ($Variant.EndsWith('bilinear')) { $A.Bilinear=$true }
            $Cases["$($Size[0])x$($Size[1])-$Variant"]=$A
        }
    }
}
$Runs=@()
foreach ($Case in $Cases.GetEnumerator()) {
    $A=$Case.Value.Clone(); $A.EngineRoot=$EngineRoot; $A.Output=Join-Path $Output $Case.Key; $A.Label=$Case.Key
    if ($DebugLayer) { $A.DebugLayer=$true }
    & (Join-Path $PSScriptRoot 'Readback.ps1') @A
    $Runs+=@{name=$Case.Key;path=$A.Output}
    @{schemaVersion=2;modelKey=$Matrix.modelKey;stage=$Stage;runs=$Runs} | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $Output 'suite.json') -Encoding utf8
}
Write-Output "Suite artifacts: $Output"
