param(
    [Parameter(Mandatory = $true)][string]$SdkRoot,
    [Parameter(Mandatory = $true)][string]$JavaHome,
    [string]$EngineRoot = $env:UEFOGLAB_ENGINE_ROOT,
    [string]$GradleUserHome = ''
)
if (!$EngineRoot) { throw 'Pass -EngineRoot or set UEFOGLAB_ENGINE_ROOT.' }
$sdkManifestPath = Join-Path $EngineRoot 'Engine\Config\Android\Android_SDK.json'
$sdkManifest = Get-Content -LiteralPath $sdkManifestPath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
$resolvedSdk = (Resolve-Path -LiteralPath $SdkRoot -ErrorAction Stop).Path
$resolvedJava = (Resolve-Path -LiteralPath $JavaHome -ErrorAction Stop).Path
$resolvedNdk = Join-Path $resolvedSdk ('ndk\' + $sdkManifest.ndk)
$requiredFiles = @(
    (Join-Path $resolvedSdk 'platform-tools\adb.exe'),
    (Join-Path $resolvedSdk ('platforms\' + $sdkManifest.platforms + '\android.jar')),
    (Join-Path $resolvedSdk ('build-tools\' + $sdkManifest.'build-tools' + '\aapt2.exe')),
    (Join-Path $resolvedNdk 'toolchains\llvm\prebuilt\windows-x86_64\bin\clang++.exe'),
    (Join-Path $resolvedJava 'bin\java.exe')
)
foreach ($requiredFile in $requiredFiles) {
    if (!(Test-Path -LiteralPath $requiredFile -PathType Leaf)) { throw "Missing Android toolchain input: $requiredFile" }
}
if (!$GradleUserHome) { $GradleUserHome = Join-Path (Split-Path $resolvedSdk -Parent) 'GradleHome' }
$resolvedGradle = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($GradleUserHome)
# Environment changes affect this PowerShell process and its children only.
$env:ANDROID_HOME = $resolvedSdk
$env:ANDROID_SDK_ROOT = $resolvedSdk
$env:NDKROOT = $resolvedNdk
$env:ANDROID_NDK_ROOT = $resolvedNdk
$env:JAVA_HOME = $resolvedJava
$env:GRADLE_USER_HOME = $resolvedGradle
$toolPaths = @((Join-Path $resolvedSdk 'platform-tools'), (Join-Path $resolvedSdk 'cmdline-tools\latest\bin'), (Join-Path $resolvedJava 'bin'))
$existingPaths = @($env:PATH -split ';' | Where-Object { $_ -and $_ -notin $toolPaths })
$env:PATH = ($toolPaths + $existingPaths) -join ';'
[pscustomobject]@{ SdkRoot=$resolvedSdk; NdkRoot=$resolvedNdk; JavaHome=$resolvedJava; GradleUserHome=$resolvedGradle; EngineManifest=$sdkManifestPath; Scope='process and child processes' }
