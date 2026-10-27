param(
    [Parameter(Mandatory)][string]$Output,
    [string]$Python = 'python'
)
$ErrorActionPreference = 'Stop'
& $Python "$PSScriptRoot/mobile_fog_reference.py" cpu --output $Output
if ($LASTEXITCODE -ne 0) { throw "Mobile fog CPU reference rejected (exit $LASTEXITCODE)." }
