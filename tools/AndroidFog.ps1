param(
    [ValidateSet('Run','Validate','Negative')][string]$Action = 'Run',
    [ValidateSet('Off','Full')][string]$Mode = 'Full',
    [string]$Candidate = '', [string]$PreviousCandidate = '', [string]$InputRun = '',
    [Parameter(Mandatory)][string]$Output,
    [ValidateSet(400,600)][int]$NearCm = 400,
    [ValidateSet(0,0.35,0.70)][double]$Extinction = 0.35,
    [ValidateSet(1,2)][int]$PreExposure = 1,
    [string]$Serial = '',
    [string]$Python = 'python'
)
$ErrorActionPreference = 'Stop'
$FogArgs = @($Action.ToLowerInvariant(), '--output', $Output)
if ($Action -eq 'Run') {
    $FogArgs += @('--candidate', $Candidate, '--mode', $Mode, '--near', $NearCm,
        '--extinction', $Extinction.ToString([System.Globalization.CultureInfo]::InvariantCulture), '--exposure', $PreExposure)
    if ($PreviousCandidate) { $FogArgs += @('--previous-candidate', $PreviousCandidate) }
    if ($Serial) { $FogArgs += @('--serial', $Serial) }
} else { $FogArgs += @('--input', $InputRun) }
& $Python "$PSScriptRoot/android_fog.py" @FogArgs
if ($LASTEXITCODE -ne 0) { throw "Android fog $Action rejected (exit $LASTEXITCODE)." }
