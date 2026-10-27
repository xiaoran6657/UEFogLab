# Desktop quickstart / 桌面复现入口

Run these commands from the repository root in **PowerShell 7** on Windows. The candidate uses the installed **UE 5.8.4 / CL 58902052**, Visual Studio C++ Build Tools and Windows SDK; a source-engine checkout, authored map, Android SDK and historical `Saved/` archive are unnecessary. Actual desktop evidence is scoped to RX 9070 / D3D12 SM6. Other devices are untested. Original project work uses MIT / 2026 xiaoran6657; UE and third-party rights are retained. Publication is a separate step.

中文：在仓库根目录使用 PowerShell 7，显式填写安装版 UE 5.8.4 路径。无需源码引擎、历史 Saved 或本地理论库。构建、CPU 数学、GPU 执行、GPU 数值和性能是分别验证的结果；成功启动不代表全部通过。

## Build, CPU check and launch

```powershell
$EngineRoot = 'C:\Program Files\Epic Games\UE_5.8' # Replace with your installed directory.
.\tools\Build.ps1 -EngineRoot $EngineRoot
.\tools\Test.ps1 -EngineRoot $EngineRoot
.\tools\Run.ps1 -EngineRoot $EngineRoot -Preset High
```

Build is Development Editor / Win64; the project and plugin compile from source. `Test.ps1` uses NullRHI and requires a passing `UEFogLab` CPU automation report in `Saved/Tests/`. Close the interactive game window before the next GPU run. Opening `UEFogLab.uproject` and pressing Play generates the diagnostic geometry and camera in `/Engine/Maps/Entry`; no custom `.umap` is needed. Engine content stays in the engine installation.

Public wrappers use UEFOGLAB_ENGINE_ROOT when set; these commands supply `-EngineRoot` explicitly. They do not install software or change user/machine environment settings. Engine/DDC caches can be reused; “clean project source” does not mean cold engine caches. For a new source copy, do not copy project `Binaries/`, `Intermediate/`, `Saved/`, `tools/LocalEnvironment.ps1` or third-party reference originals. Git-tracked source is sufficient.

## Python for raw numerical analysis

Use an existing Python **3.12** with NumPy and Pillow, or create an isolated environment. The pinned versions below are the versions used for this candidate's desktop analysis, not a claim about the newest versions. No Python dependency is required to build or view the scene.

```powershell
python -m venv out/python
$FogPython = (Resolve-Path 'out/python/Scripts/python.exe').Path
& $FogPython -m pip install -r tools/requirements-desktop.txt
```

If those packages already exist, select that interpreter explicitly instead of installing again:

```powershell
$FogPython = (Get-Command python).Source
& $FogPython -c 'import sys,numpy,PIL; print(sys.version); print(numpy.__version__,PIL.__version__)'
```

## Two separate GPU models

Choose new output directory names for every capture; the readback script refuses an existing directory. All output must remain under this checkout's `Saved/`, `out/` or `work/`. Commit source first: manifests record the actual Git revision, working tree, source hashes, engine build, parameters and raw artifact hashes.

```powershell
$CaptureRoot = Join-Path $PWD 'Saved/public-quickstart-01' # Change on every repeat.
.\tools\Readback.ps1 -EngineRoot $EngineRoot -Preset High -DebugLayer -Output (Join-Path $CaptureRoot 'prescribed')
& $FogPython tools/fog_analysis.py (Join-Path $CaptureRoot 'prescribed') --output (Join-Path $CaptureRoot 'prescribed-analysis.json')

.\tools\Readback.ps1 -EngineRoot $EngineRoot -LightingModel ManualDirectionalHG -Preset Medium -Width 640 -Height 360 -DebugLayer -Output (Join-Path $CaptureRoot 'manual')
& $FogPython tools/fog_m2_analysis.py (Join-Path $CaptureRoot 'manual') --output (Join-Path $CaptureRoot 'manual-analysis.json')
```

The first capture is the default Prescribed / High full64 path, scene and fog 321×181. It prescribes a constant source `J=(0.35,0.60,0.90)`. The second is explicit ManualDirectionalHG / Medium64 guided, scene 640×360 and fog 320×180; manual albedo .8, g .5, E=(4,3,2), photon direction (-1,-1,.15) use the script defaults. Manual uses normalized HG and a ray-constant source with incoming transmittance/visibility fixed to one. Neither custom model extracts UE lights, renders volume shadows, multiple scattering or temporal reconstruction. Use each model's own analysis tool/schema.

Exit 0 with `passed=true` is required from each numerical CLI. Read `opticalPassed`, `strictGeometryPassed` and `depthScope` separately: the Diagnostic fixture's optical checks are conditional on independently decoded actual device depth, with known ideal-box geometry differences. They do not prove general-scene geometry or nonzero ViewRect origins. S/T readback does not establish full HDR or timing correctness. Debug/capture runs are unsuitable for formal performance sampling.

For a screenshot-only visual sample:

```powershell
.\tools\Run.ps1 -EngineRoot $EngineRoot -Preset High -LightingModel ManualDirectionalHG -Capture
```

Screenshots pass through tonemapping. Encoding a still-image video does not measure real-time FPS. High / Full64 remains the default; Reference is a high-sample preset whose convergence is limited to recorded cases.

## Evidence and optional studies

- [Public case](CASE.en.md) and [derived historical summary](evidence/summary.md) preserve model/device/APK boundaries, known geometry differences and counterexamples.
- [Technical provenance](data/provenance.json) records original report hashes and historical source identifiers; excluded local records are not fake public links.
- [Primary references](references.md) are links only. No historical Saved, local theory originals, SDKs or tool binaries are needed for the desktop route.
- Android renderer sources are retained. Historical mobile observations use separate fixed quality and timing APKs; this public snapshot has not been newly accepted on a phone. Archive-specific collection/aggregation drivers are omitted and listed in the package provenance.

Optional scripts default to a Python executable on PATH, or accept -Python explicitly. Set UEFOGLAB_ENGINE_ROOT or pass -EngineRoot; no personal engine/Python path is baked into the public wrappers. Optional media generation requires the pinned showcase requirements, existing FFmpeg and selected installed fonts. It reads only the included compact data and PNGs.

Build/CPU/GPU numerical checks, real browser media playback and formal performance are separate evidence. New capture logs contain your own local paths and must stay in ignored outputs. No fresh Android/whole-frame/FPS/energy or owner-learning claim follows from this snapshot.
