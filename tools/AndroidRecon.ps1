param(
    [ValidateSet('Run','Validate','Negative','Quality')][string]$Action = 'Run',
    [Alias('Profile')][ValidateSet('Off','GuideProbe','Full64','HalfB64','HalfG64')][string]$ReconProfile = 'HalfG64',
    [ValidateSet('Planes','ThinStripe')][string]$Fixture = 'Planes',
    [ValidateSet('Half','Odd')][string]$Grid = 'Half',
    [ValidateSet(400,600)][int]$NearCm = 600,
    [ValidateSet(0,0.35,0.70)][double]$Extinction = 0.35,
    [ValidateSet(1,2)][int]$PreExposure = 1,
    [string]$Candidate = '', [string]$PreviousCandidate = '', [string]$InputRun = '', [string]$ReferenceRun = '', [string]$Serial = '',
    [Parameter(Mandatory)][string]$Output,
    [string]$Python = 'python'
)
$ErrorActionPreference = 'Stop'
$Arguments = @($Action.ToLowerInvariant(),'--output',$Output)
if ($Action -eq 'Run') {
    $Arguments += @('--candidate',$Candidate,'--profile',$ReconProfile,'--fixture',$Fixture,'--grid',$Grid,'--near',$NearCm,
        '--extinction',$Extinction.ToString([System.Globalization.CultureInfo]::InvariantCulture),'--exposure',$PreExposure)
    if ($PreviousCandidate) { $Arguments += @('--previous-candidate',$PreviousCandidate) }
    if ($Serial) { $Arguments += @('--serial',$Serial) }
} else {
    $Arguments += @('--input',$InputRun)
    if ($Action -in @('Negative','Quality')) { $Arguments += @('--reference',$ReferenceRun) }
}
& $Python "$PSScriptRoot/android_recon.py" @Arguments
if ($LASTEXITCODE -ne 0) { throw "Android reconstruction $Action rejected (exit $LASTEXITCODE)." }
