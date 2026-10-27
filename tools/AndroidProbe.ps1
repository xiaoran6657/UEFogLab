param(
    [ValidateSet('Run','Validate','Negative')][string]$Action = 'Run',
    [ValidateSet('Off','On')][string]$Mode = 'On',
    [string]$Candidate = '', [string]$PreviousCandidate = '', [string]$InputRun = '',
    [Parameter(Mandatory)][string]$Output,
    [ValidateSet(400,600)][int]$NearCm = 400,
    [ValidateSet(1,2)][int]$Scale = 1,
    [ValidateSet(1,2)][int]$PreExposure = 1,
    [string]$Serial = '',
    [string]$Python = 'python'
)
$ErrorActionPreference = 'Stop'
$ProbeArgs = @($Action.ToLowerInvariant(), '--output', $Output)
if ($Action -eq 'Run') {
    $ProbeArgs += @('--candidate', $Candidate, '--mode', $Mode, '--near', $NearCm, '--scale', $Scale, '--exposure', $PreExposure)
    if ($PreviousCandidate) { $ProbeArgs += @('--previous-candidate', $PreviousCandidate) }
    if ($Serial) { $ProbeArgs += @('--serial', $Serial) }
} else { $ProbeArgs += @('--input', $InputRun) }
& $Python "$PSScriptRoot/android_probe.py" @ProbeArgs
if ($LASTEXITCODE -ne 0) { throw "Android probe $Action rejected (exit $LASTEXITCODE)." }
