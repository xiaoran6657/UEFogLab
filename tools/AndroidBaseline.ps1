param(
    [ValidateSet('Build', 'Validate')][string]$Action = 'Build',
    [string]$Profile = 'VulkanMobileForwardHDR',
    [Parameter(Mandatory)][string]$Output,
    [string]$Candidate = '',
    [string]$EngineRoot = $env:UEFOGLAB_ENGINE_ROOT,
    [string]$SdkRoot = $env:ANDROID_SDK_ROOT,
    [string]$JavaHome = $env:JAVA_HOME,
    [string]$Python = 'python'
)
if (!$EngineRoot) { throw 'Pass -EngineRoot or set UEFOGLAB_ENGINE_ROOT.' }
$ErrorActionPreference = 'Stop'
if ($Action -eq 'Build') {
    . "$PSScriptRoot/Use-AndroidToolchain.ps1" -SdkRoot $SdkRoot -JavaHome $JavaHome -EngineRoot $EngineRoot | Out-Null
    # Android build preferences/debug signing are separate from normal ADB keys.
    $env:ANDROID_USER_HOME = Join-Path (Split-Path $env:ANDROID_HOME -Parent) 'UserHome'
    $ArgsForPython = @('build', '--profile', $Profile, '--output', $Output, '--engine', $EngineRoot)
} else {
    $ArgsForPython = @('validate', $Candidate, '--output', $Output, '--engine', $EngineRoot, '--sdk', $SdkRoot, '--java', $JavaHome)
}
& $Python "$PSScriptRoot/android_baseline.py" @ArgsForPython
if ($LASTEXITCODE -ne 0) { throw "Android baseline $Action failed (exit $LASTEXITCODE)." }
