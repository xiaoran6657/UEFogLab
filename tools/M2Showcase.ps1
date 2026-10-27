param([string]$EngineRoot=$env:UEFOGLAB_ENGINE_ROOT,[string]$Output='',[int]$Width=1280,[int]$Height=720)
if (!$EngineRoot) { throw 'Pass -EngineRoot or set UEFOGLAB_ENGINE_ROOT.' }
$ErrorActionPreference='Stop'
$ProjectRoot=Split-Path $PSScriptRoot -Parent
if (!$Output) { $Output=Join-Path $ProjectRoot ('Saved/M2/showcase-'+(Get-Date -Format 'yyyyMMdd-HHmmss')) }
$Output=[IO.Path]::GetFullPath($Output)
if (Test-Path -LiteralPath $Output) { throw 'Showcase output already exists.' }
New-Item -ItemType Directory -Path $Output | Out-Null
$Cases=@(@{id='isotropic';g=0;direction='-1,-1,0.15'},@{id='forward';g=0.5;direction='-1,-1,0.15'},@{id='backward';g=-0.5;direction='-1,-1,0.15'},@{id='reversed';g=0.5;direction='1,1,-0.15'})
$Runs=@()
$Fixed=@('-FogLabPreExposure=1','-FogLabDensityMode=1','-FogLabExtinction=0.35','-FogLabEarlyOut=0','-FogLabFixture=Diagnostic','-FogLabCamera=Outside','-FogLabOriginOffset=0','-FogLabConstrainedAspect=0')
foreach ($Case in $Cases) {
    $Lines=@(& (Join-Path $PSScriptRoot 'Run.ps1') -EngineRoot $EngineRoot -Preset High -Capture -Width $Width -Height $Height -Label ('m2-'+$Case.id) -LightingModel ManualDirectionalHG -HGg $Case.g -PhotonDirection $Case.direction -ExtraArguments $Fixed)
    $Record=@($Lines | Where-Object { $_ -like 'Run artifacts: *' })[-1]
    if (!$Record) { throw 'Missing normal capture output identity.' }
    $Path=$Record.Substring('Run artifacts: '.Length)
    $Runs+=@{id=$Case.id;path=$Path;g=$Case.g;photonDirection=$Case.direction}
    @{schemaVersion=2;modelKey='ManualDirectionalHG-v1';stage='Showcase';width=$Width;height=$Height;fixedArguments=$Fixed;runs=$Runs} | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $Output 'suite.json') -Encoding utf8
    Write-Output $Record
}
