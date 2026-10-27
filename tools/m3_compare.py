"""Opt-in M3 display comparison capture/validation; never a numeric ST oracle."""
from __future__ import annotations
import argparse
import os
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
MODES = ('Off', 'Ours', 'NativeLFV', 'NativeVF')
PROFILES = {'Off': ('Default',), 'Ours': ('High', 'MediumB', 'MediumG', 'LowG'),
            'NativeLFV': ('Default',), 'NativeVF': ('VFTR', 'VFNoTR')}

def read(p):
    return json.loads(Path(p).read_text(encoding='utf-8-sig'))

def write(p, d):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(d, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf8')

def bind(p):
    p = Path(p).resolve()
    return {'path': str(p), 'bytes': p.stat().st_size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}

def check(ok, message):
    if not ok:
        raise ValueError(message)

def allowed_output(p):
    p = Path(p).resolve()
    return any(p.is_relative_to(ROOT/name) for name in ('Saved', 'work', 'out'))

def close(a, b, tolerance=1e-5):
    if isinstance(b, (list, tuple)):
        return isinstance(a, list) and len(a) == len(b) and all(close(x, y, tolerance) for x, y in zip(a, b))
    return isinstance(a, (int, float)) and math.isfinite(a) and abs(a-b) <= tolerance

def source_inputs():
    names = subprocess.check_output(['git', 'ls-files', 'UEFogLab.uproject', 'Source', 'Plugins/UEFog', 'Config', 'tools'], cwd=ROOT, text=True).splitlines()
    return [bind(ROOT/n) for n in names if (ROOT/n).is_file()]

def check_bind(b):
    actual = bind(b['path'])
    check(actual == b, 'Artifact identity mismatch: '+b['path'])

def archive_runtime(b):
    archive = ROOT/'Saved/M3/BinaryArchive'/(b['sha256']+'.dll')
    if not archive.exists():
        check_bind(b)
        archive.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(b['path'], archive)
    check(archive.stat().st_size == b['bytes'] and hashlib.sha256(archive.read_bytes()).hexdigest() == b['sha256'], 'Runtime content archive mismatch')

def check_runtime(b):
    p = Path(b['path'])
    if p.is_file() and bind(p) == b:
        return
    archive = ROOT/'Saved/M3/BinaryArchive'/(b['sha256']+'.dll')
    check(archive.is_file() and archive.stat().st_size == b['bytes'] and hashlib.sha256(archive.read_bytes()).hexdigest() == b['sha256'],
          'Runtime binary content is unavailable (active build and content archive differ)')

def check_source(b, revision, tree):
    # Later M3 tooling commits may validate an older, clean capture. Resolve
    # changed text inputs against that exact Git revision, including CRLF form.
    p = Path(b['path'])
    relative = p.relative_to(ROOT).as_posix()
    check(relative in tree, 'Source input absent from frozen commit: '+relative)
    if p.is_file() and bind(p) == b:
        data = p.read_bytes()
        variants = (data, data.replace(b'\r\n', b'\n'))
        check(any(hashlib.sha1(b'blob '+str(len(x)).encode()+b'\0'+x).hexdigest() == tree[relative] for x in variants),
              'Current source does not match frozen Git commit: '+relative)
        return
    raw = subprocess.check_output(['git', 'show', revision+':'+relative], cwd=ROOT)
    variants = (raw, raw.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n'))
    check(any(len(x) == b['bytes'] and hashlib.sha256(x).hexdigest() == b['sha256'] for x in variants),
          'Captured source does not match frozen Git revision: '+relative)

def request(a):
    c = read(a.config)
    check(c.get('kind') == 'UEFogLabComparisonConfig' and c.get('schemaVersion') == 1, 'Bad config kind/schema')
    check(c.get('configId') == 'NativeCompareRadial-v2-height-gate' and
          [c.get('nativeHeightExtinction'), c.get('nativeHeightFalloff'), c.get('nativeHeightOffset')] == [8, 1, 0], 'Unfrozen native medium configuration')
    check(a.mode in MODES, 'Unknown comparison mode')
    p = a.profile or PROFILES[a.mode][0]
    check(p in PROFILES[a.mode], 'Profile conflicts with mode')
    check(a.camera in ('A', 'B'), 'Unknown camera')
    check(64 <= a.width <= 4096 and 64 <= a.height <= 4096 and 120 <= a.frames <= 600, 'Invalid dimensions/frames')
    d = c['nativeDensity']
    check(isinstance(d, (int, float)) and math.isfinite(d) and 0 < d <= 10, 'Illegal native density')
    check(close(c['scale'], [.72]*3, 1e-8), 'Nonuniform or unsupported LFV scale')
    check(not a.zero_local or a.mode in ('NativeLFV', 'NativeVF'), 'Zero control requires native mode')
    check(c == read(ROOT/'tools/m3_config.json'), 'Config differs from the complete frozen comparison specification')
    return {'mode': a.mode, 'profile': p, 'camera': a.camera, 'width': a.width, 'height': a.height,
            'frames': a.frames, 'zeroLocal': a.zero_local, 'nativeDensity': d,
            'scaleX': c['scale'][0], 'scaleY': c['scale'][1], 'scaleZ': c['scale'][2],
            'output': str(Path(a.output).resolve()), 'configId': c['configId']}

def export_trace(engine, out):
    exe = Path(engine)/'Engine/Binaries/Win64/UnrealInsights.exe'
    commands = ['TimingInsights.ExportThreads "'+(out/'threads.csv').as_posix()+'"',
                'TimingInsights.ExportTimers "'+(out/'timers.csv').as_posix()+'" -ShowFormatInTimerNames=1',
                'TimingInsights.ExportTimingEvents "'+(out/'gpu-events.csv').as_posix()+'" -threads=GPU* -columns=ThreadId,ThreadName,TimerId,TimerName,StartTime,EndTime,Duration,Depth']
    (out/'export.rsp').write_text('\n'.join(commands)+'\n', encoding='utf8')
    args = [str(exe), '-OpenTraceFile='+str(out/'gpu.utrace'), '-NoUI', '-AutoQuit', '-ExecOnAnalysisCompleteCmd=@='+str(out/'export.rsp'),
            '-abslog='+str(out/'insights.log')]
    with (out/'insights-stdout.txt').open('w', encoding='utf8') as log:
        result = subprocess.run(args, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=120)
    check(result.returncode == 0 and (out/'gpu-events.csv').is_file(), 'GPU event export failed')
    return args

def gpu_profile(out):
    log = (out/'run.log').read_text(encoding='utf-8-sig', errors='replace')
    headers = list(re.finditer(r'GPU Profile for Frame (\d+) - (\w+) pipeline (\d+) - GPU (\d+)', log))
    # Preserve occurrences: a set would conceal duplicate passes in this frame.
    matches = []
    for i, h in enumerate(headers):
        lines = log[h.end():headers[i+1].start() if i+1 < len(headers) else len(log)].splitlines()
        names = [line.split('┃')[-2].strip() for line in lines if 'LogRHI:' in line and line.count('┃') >= 3]
        if h[2] == 'Graphics' and names.count('UEFogCompareScreenshotWitness') == 1:
            matches.append((h, names))
    check(len(matches) == 1, 'ProfileGPU did not uniquely include screenshot-view witness')
    return matches[0]

def gpu_witness(out, snapshot):
    with (out/'gpu-events.csv').open(encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    with (out/'timers.csv').open(encoding='utf-8-sig', newline='') as f:
        timers = {r['Id']: r for r in csv.DictReader(f)}
    for row in rows:
        row['TimerName'] = timers[row['TimerId']]['Name']
    # GPU trace names are independent of requested mode and component registration.
    with (out/'threads.csv').open(encoding='utf-8-sig', newline='') as f:
        threads = {r['Id']: r for r in csv.DictReader(f)}
    gpu = [r for r in rows if threads[r['ThreadId']]['Group'] == 'GPU' and timers[r['TimerId']]['Type'] == 'GPU']
    frame = int(snapshot['view']['engineFrame'])
    markers = [r for r in gpu if r.get('TimerName') == 'UEFogCompareScreenshotWitness']
    index = int(snapshot['view']['renderedViewIndex'])
    check(len(markers) == 1, 'Missing/ambiguous unique screenshot GPU marker')
    m = markers[0]
    t = float(m['StartTime'])
    # All relevant native events live in the same GPU frame ancestor as the marker.
    ancestors = [r for r in gpu if r['ThreadName'] == m['ThreadName'] and float(r['StartTime']) <= t <= float(r['EndTime'])
                 and int(r['Depth']) < int(m['Depth'])]
    check(ancestors, 'GPU marker has no frame ancestor')
    scenes = [r for r in ancestors if r['TimerName'] == 'Scene']
    check(len(scenes) == 1, 'Screenshot GPU marker is not in one unambiguous Scene scope')
    scope = scenes[0]
    begin, end = float(scope['StartTime']), float(scope['EndTime'])
    trace_names = [r['TimerName'] for r in gpu if r['ThreadName'] == m['ThreadName'] and begin <= float(r['StartTime']) and float(r['EndTime']) <= end]
    # ProfileGPU expands RDG format arguments; the generic CSV RDGEvent name does not.
    profile, names = gpu_profile(out)
    grids = [list(map(int, hit)) for name in names for hit in re.findall(r'^LightScattering (\d+)x(\d+)x(\d+)', name)]
    return {'method': 'ProfileGPU expanded GPU event table selected by unique armed screenshot GPU marker, same marker in Insights GPU Scene, frozen view snapshot; both frame number domains recorded',
            'renderedViewIndex': index, 'screenshotMarkerCount': len(markers),
            'engineFrame': frame, 'profileRhiFrame': int(profile[1]), 'profileGpu': int(profile[4]),
            'actualVfGridFromGpuEvent': grids[0] if len(grids) == 1 else None,
            'marker': m, 'scope': scope, 'traceEventNames': sorted(set(trace_names)), 'eventNames': sorted(set(names))}

def validate_snapshot(s, r):
    check(s.get('kind') == 'UEFogLabComparisonSnapshot' and s.get('schemaVersion') == 1, 'Bad snapshot kind/schema')
    check(s['request'] == r, 'Snapshot/request mismatch')
    mode, p = r['mode'], r['profile']
    check(mode in MODES and p in PROFILES[mode], 'Invalid mode/profile')
    check(r.get('configId') == 'NativeCompareRadial-v2-height-gate' and close(r.get('nativeDensity'), .35*16/15, 1e-9), 'Unfrozen comparison medium')
    v, actors = s['view'], s['actors']
    check(v['renderedViewIndex'] >= r['frames'], 'Warmup did not render enough views')
    check([s['imageWidth'], s['imageHeight']] == [r['width'], r['height']] == v['viewSize'], 'Actual dimensions mismatch')
    check(v['viewMin'] == [0, 0] and all(x >= y for x, y in zip(v['sceneExtent'], v['viewSize'])), 'Invalid actual view rectangle')
    check(v['fogShowFlag'] and v['volumetricFogShowFlag'] and not v['localExposureShowFlag'] and close(v['preExposure'], 1), 'Fog flags or exposure mismatch')
    check(v['autoExposureMethod'] == 2 and close(v['autoExposureBias'], 0) and not v['physicalCameraExposure'], 'Final render exposure mismatch')
    eye, target = (([-850, -950, 310], [280, 0, 145]) if r['camera'] == 'A' else ([-350, -950, 600], [300, 0, 180]))
    fwd = [y-x for x, y in zip(eye, target)]; length = math.sqrt(sum(x*x for x in fwd)); fwd = [x/length for x in fwd]
    check(close(v['cameraWorldCm'], eye) and close(v['cameraForward'], fwd), 'Actual render camera mismatch')
    for key in ('viewToClip', 'worldToView'):
        check(len(v[key]) == 16 and all(isinstance(x, (float, int)) and math.isfinite(x) for x in v[key]), 'Invalid actual camera matrix: '+key)
    clip = v['viewToClip']
    # UE's default local-player constraint maintains vertical FOV derived from
    # the camera's nominal 16:9 aspect. Odd view ratios therefore change X.
    y_scale = math.sqrt(3)*16/9
    check(close(clip[0], y_scale*r['height']/r['width']) and close(clip[5], y_scale) and close(clip[11], 1), 'Actual render projection/FOV axis constraint mismatch')
    right = [-fwd[1], fwd[0], 0]; length = math.sqrt(sum(x*x for x in right)); right = [x/length for x in right]
    up = [fwd[1]*right[2]-fwd[2]*right[1], fwd[2]*right[0]-fwd[0]*right[2], fwd[0]*right[1]-fwd[1]*right[0]]
    world_view = [right[0], up[0], fwd[0], 0, right[1], up[1], fwd[1], 0, right[2], up[2], fwd[2], 0,
                  -sum(x*y for x,y in zip(eye,right)), -sum(x*y for x,y in zip(eye,up)), -sum(x*y for x,y in zip(eye,fwd)), 1]
    check(close(v['worldToView'], world_view, .001), 'Actual world-to-view matrix mismatch')
    check(close(actors['camera']['positionCm'], eye) and actors['camera']['fovDegrees'] == 60 and not actors['camera']['constrainAspectRatio'], 'Camera actor mismatch')
    ours, native, vf = mode == 'Ours', mode.startswith('Native'), mode == 'NativeVF'
    check(v['integrateExecutions'] == int(ours) and v['compositeExecutions'] == int(ours), 'Custom execution counts inconsistent with mode')
    cv = v['cvars']
    expected = {'r.UEFog.Enabled': int(ours), 'r.Fog': 1, 'r.SupportLocalFogVolumes': 1, 'r.LocalFogVolume': int(native),
                'r.VolumetricFog': int(vf), 'r.LocalFogVolume.RenderDuringHeightFogPass': 0,
                'r.LocalFogVolume.RenderIntoVolumetricFog': int(vf), 'r.LocalFogVolume.GlobalStartDistance': 10,
                'r.LocalFogVolume.TilePixelSize': 128, 'r.LocalFogVolume.TileDebug': 0, 'r.LocalFogVolume.HalfResolution': 0,
                'r.LocalFogVolume.MaxDensityIntoVolumetricFog': .01,
                'r.VolumetricFog.GridPixelSize': 8, 'r.VolumetricFog.GridSizeZ': 128,
                'r.VolumetricFog.TemporalReprojection': int(p != 'VFNoTR'), 'r.VolumetricFog.Jitter': 1, 'r.VolumetricFog.HistoryWeight': .9,
                'r.UEFog.Steps': 32 if p == 'LowG' else 64, 'r.UEFog.Downsample': 2 if p in ('MediumB', 'MediumG', 'LowG') else 1,
                'r.UEFog.Reconstruction': int(p != 'MediumB'), 'r.UEFog.LightingModel': 1, 'r.UEFog.DensityMode': 1,
                'r.UEFog.Extinction': .35, 'r.UEFog.EarlyOut': 0, 'r.UEFog.Albedo': .8, 'r.UEFog.HGg': .5,
                'r.UEFog.IncidentR': 4, 'r.UEFog.IncidentG': 3, 'r.UEFog.IncidentB': 2,
                'r.UEFog.PhotonX': -1, 'r.UEFog.PhotonY': -1, 'r.UEFog.PhotonZ': .15,
                'r.AntiAliasingMethod': 0, 'r.ScreenPercentage': 100, 'r.DynamicRes.OperationMode': 0,
                'r.EyeAdaptation.PreExposureOverride': 1, 'r.MotionBlurQuality': 0, 'r.BloomQuality': 0,
                'r.DepthOfFieldQuality': 0, 'r.OneFrameThreadLag': 0, 'r.DefaultFeature.AutoExposure': 0, 'sg.ShadowQuality': 3}
    for key, value in expected.items():
        check(close(cv.get(key), value), 'Effective CVar mismatch: '+key)
    check(actors['localFogActorCount'] == int(native) and actors['heightFogActorCount'] == int(vf), 'Native actor exclusivity mismatch')
    geometry = [([200,0,-35], [24,20,.5]), ([900,0,280], [.3,18,6])]
    geometry += [([250,i*190,150],[.6,.6,3]) for i in range(-3,4)]
    geometry += [([-150,i*85,125],[.06,.06,2.5]) for i in range(-2,3)]
    check(len(actors['geometry']) == len(geometry) and all(close(g['positionCm'], pos) and close(g['scale'], scale) for g, (pos, scale) in zip(actors['geometry'], geometry)), 'Common geometry mismatch')
    sun, fill = actors['sun'], actors['fill']
    k = [-1/math.sqrt(2.0225), -1/math.sqrt(2.0225), .15/math.sqrt(2.0225)]
    check(close(sun['photonDirection'], k) and close(sun['linearColor'], [1,.75,.5], .004) and sun['intensity'] == 3 and sun['volumetricIntensity'] == 1, 'Common sun mismatch')
    check(fill['intensity'] == 25000 and fill['volumetricIntensity'] == 0 and close(fill['linearColor'], [1,1,1]) and actors['fillAttenuationCm'] == 2200, 'Common fill mismatch')
    for light in (sun, fill):
        check(light['registered'] and light['visible'] and not light['castShadows'] and not light['castVolumetricShadow'], 'Common light state mismatch')
    if native:
        l = actors['lfv']
        check(l['registered'] and l['visible'] and close(l['positionCm'], [300,0,180]) and close(l['scale'], [.72]*3) and close(l['worldRadiusCm'], 360), 'LFV registration/shape mismatch')
        check(close(l['radialExtinction'], 0 if r['zeroLocal'] else r['nativeDensity']) and l['heightExtinction'] == (0 if r['zeroLocal'] else 8) and l['heightFalloff'] == 1 and l['heightOffset'] == 0 and l['startDistanceCm'] == 0 and l['g'] == .5, 'LFV medium mismatch')
        check(close(l['albedoLinear'], [.8]*3) and close(l['emissiveLinear'], [0]*3), 'LFV color mismatch')
    if vf:
        e = actors['ehfContainer']
        check(e['registered'] and e['visible'] and e['volumetricEnabled'] and close(e['fogDensity'], 1e-6, 1e-10) and close(e['fogMaxOpacity'], 1e-4, 1e-8), 'VF container registration mismatch')
        check(e['secondFogDensity'] == e['extinctionScale'] == e['startDistanceCm'] == e['nearFadeCm'] == 0 and e['distanceCm'] == 6000 and e['g'] == .5, 'VF container medium mismatch')
    return expected

def validate_capture(out, verify_current_source=True):
    out = Path(out).resolve(); m = read(out/'capture.json')
    check(m.get('kind') == 'UEFogLabComparisonCapture' and m.get('schemaVersion') == 1 and m.get('passed') is True, 'Capture is not successful comparison data')
    check(m['processExitCode'] == 0 and m['exitConfirmed'] is True and not m['sourceWorkingTree'], 'Unconfirmed exit or dirty source')
    check(re.fullmatch('[0-9a-f]{40}', m['sourceRevision']) is not None, 'Invalid frozen source revision')
    check(subprocess.check_output(['git', 'cat-file', '-t', m['sourceRevision']], cwd=ROOT).strip() == b'commit', 'Source revision is not a commit')
    required = {'request.json', 'snapshot.json', 'frame.png', 'gpu.utrace', 'gpu-events.csv', 'timers.csv', 'threads.csv', 'gpu-witness.json', 'run.log'}
    check(len(m['artifacts']) == len(required) and {Path(b['path']).resolve() for b in m['artifacts']} == {out/n for n in required}, 'Missing/duplicate required capture artifact bindings')
    for b in m['artifacts']:
        check_bind(b)
    entries = subprocess.check_output(['git', 'ls-tree', '-r', m['sourceRevision'], '--', 'UEFogLab.uproject', 'Source', 'Plugins/UEFog', 'Config', 'tools'], cwd=ROOT, text=True).splitlines()
    tree = {line.split('\t', 1)[1]: line.split('\t',1)[0].split()[2] for line in entries}
    check(len(m['sourceInputs']) == len(tree) and {Path(b['path']).relative_to(ROOT).as_posix() for b in m['sourceInputs']} == set(tree), 'Incomplete frozen source inputs')
    for b in m['sourceInputs']:
        if verify_current_source:
            check_source(b, m['sourceRevision'], tree)
    check_bind(m['config'])
    frozen = json.loads(subprocess.check_output(['git', 'show', m['sourceRevision']+':tools/m3_config.json'], cwd=ROOT))
    check(read(m['config']['path']) == frozen, 'Config values differ from capture revision')
    r = read(out/'request.json'); s = read(out/'snapshot.json')
    check(m['request'] == r and m['config']['sha256'] == next(b['sha256'] for b in m['sourceInputs'] if Path(b['path']) == ROOT/'tools/m3_config.json'), 'Manifest request/config mismatch')
    validate_snapshot(s, r)
    check(len(m['runtimeBinaries']) == 2, 'Missing runtime binary identities')
    check({Path(b['path']) for b in m['runtimeBinaries']} == {ROOT/n for n in ('Binaries/Win64/UnrealEditor-FogLabDemo.dll', 'Plugins/UEFog/Binaries/Win64/UnrealEditor-UEFog.dll')}, 'Runtime binary set mismatch')
    for b in m['runtimeBinaries']:
        check_runtime(b)
    from PIL import Image
    with Image.open(out/'frame.png') as image:
        check(list(image.size) == s['view']['viewSize'], 'PNG size mismatch')
    witness = read(out/'gpu-witness.json')
    check(witness == gpu_witness(out, s), 'GPU witness does not reproduce from raw trace export and profile log')
    check(witness['engineFrame'] == s['view']['engineFrame'], 'GPU witness frame mismatch')
    names = witness['eventNames']; joined = '\n'.join(names)
    mode = r['mode']
    if mode == 'NativeLFV' and not r['zeroLocal']:
        check('LocalFogVolume.Render' in joined and 'LocalFogVolume.Tiled ' in joined, 'Standalone LFV GPU render missing')
    if mode == 'NativeVF':
        check(witness['actualVfGridFromGpuEvent'] == [(r['width']+7)//8, (r['height']+7)//8, 128], 'Actual VF GPU grid mismatch')
        for event in ('VolumetricFog::InitialiseVolume', 'InitializeVolumeAttributes', 'VolumetricFog::LightScattering', 'LightScattering ', 'VolumetricFog::FinalIntegration', 'FinalIntegration'):
            check(event in joined, 'VF GPU path missing: '+event)
        check('LocalFogVolume.Render' not in joined, 'VF unexpectedly used standalone LFV')
    if mode != 'Ours':
        check('UEFog Integrate ' not in joined and 'UEFog ReconstructAndComposite' not in joined, 'Native/Off GPU custom pass contamination')
    else:
        _, events = gpu_profile(out)
        integrate = [name for name in events if name.startswith('UEFog Integrate ')]
        check(len(integrate) == 1 and events.count('UEFog ReconstructAndComposite') == 1, 'Custom GPU path missing/duplicate')
        actual = re.fullmatch(r'UEFog Integrate (\d+)x(\d+) Steps=(\d+)', integrate[0])
        cv = s['view']['cvars']; downsample = int(cv['r.UEFog.Downsample'])
        expected = [(size+downsample-1)//downsample for size in s['view']['viewSize']] + [int(cv['r.UEFog.Steps'])]
        check(actual is not None and list(map(int, actual.groups())) == expected, 'Custom GPU Integrate dimensions/Steps mismatch with actual view/CVars/profile')
    return m, s

def run(a):
    out = Path(a.output).resolve()
    check(allowed_output(out), 'Output must be below repository Saved/, work/ or out/')
    check(not out.exists(), 'Output must be a new directory (preserve previous runs)')
    r = request(a)
    check(not subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True).strip(), 'Commit implementation before capture')
    out.mkdir(parents=True)
    write(out/'request.json', r)
    rev = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    editor = Path(a.engine)/'Engine/Binaries/Win64/UnrealEditor-Cmd.exe'
    args = [str(editor), str(ROOT/'UEFogLab.uproject'), '/Engine/Maps/Entry', '-game', '-dx12', '-windowed',
            '-ResX='+str(a.width), '-ResY='+str(a.height), '-nosound', '-NoSplash', '-NoLiveCoding', '-unattended', '-RenderOffscreen',
            '-UseFixedTimeStep', '-FPS=30', '-FogLabCompareMode='+a.mode, '-FogLabCompareRequest='+str(out/'request.json'),
            '-trace=gpu,frame,bookmark,cpu', '-tracefile='+str(out/'gpu.utrace'), '-abslog='+str(out/'run.log')]
    m = {'kind': 'UEFogLabComparisonCapture', 'schemaVersion': 1, 'passed': False, 'exitConfirmed': False,
         'sourceRevision': rev, 'sourceWorkingTree': [], 'sourceInputs': source_inputs(), 'engineBuild': read(Path(a.engine)/'Engine/Build/Build.version'),
         'runtimeBinaries': [bind(ROOT/n) for n in ('Binaries/Win64/UnrealEditor-FogLabDemo.dll', 'Plugins/UEFog/Binaries/Win64/UnrealEditor-UEFog.dll')],
         'request': r, 'config': bind(a.config), 'launchArguments': args, 'startedUtc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    write(out/'capture.json', m)
    for b in m['runtimeBinaries']:
        archive_runtime(b)
    with (out/'stdout.txt').open('w', encoding='utf8') as log:
        process = subprocess.run(args, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=240)
    m.update(processExitCode=process.returncode, exitConfirmed=True)
    write(out/'capture.json', m)
    check(process.returncode == 0 and (out/'frame.png').is_file() and (out/'snapshot.json').is_file(), 'Unreal did not produce a confirmed screenshot')
    s = read(out/'snapshot.json'); validate_snapshot(s, r)
    m['traceExportArguments'] = export_trace(a.engine, out)
    write(out/'gpu-witness.json', gpu_witness(out, s))
    m['artifacts'] = [bind(out/n) for n in ('request.json', 'snapshot.json', 'frame.png', 'gpu.utrace', 'gpu-events.csv', 'timers.csv', 'threads.csv', 'gpu-witness.json', 'run.log')]
    m['passed'] = True
    write(out/'capture.json', m)
    try:
        validate_capture(out)
    except Exception:
        m['passed'] = False; write(out/'capture.json', m); raise
    print('Comparison captured and validated: '+str(out))

def main():
    p = argparse.ArgumentParser(description=__doc__); sub = p.add_subparsers(dest='action', required=True)
    q = sub.add_parser('run'); q.add_argument('--mode', required=True); q.add_argument('--profile', default='')
    q.add_argument('--camera', default='A'); q.add_argument('--width', type=int, default=1280); q.add_argument('--height', type=int, default=720)
    q.add_argument('--frames', type=int, default=120); q.add_argument('--zero-local', action='store_true'); q.add_argument('--output', required=True)
    q.add_argument('--config', default=str(ROOT/'tools/m3_config.json')); q.add_argument('--engine', default=os.environ.get('UEFOGLAB_ENGINE_ROOT', ''))
    q = sub.add_parser('validate'); q.add_argument('directory')
    a = p.parse_args()
    try:
        if a.action == 'run': run(a)
        else: validate_capture(a.directory); print('Valid comparison capture: '+a.directory)
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as e:
        print('REJECTED: '+str(e), file=sys.stderr); return 2
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
