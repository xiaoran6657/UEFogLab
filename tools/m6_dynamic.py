"""Continuous offline UE frames for the bounded M6 desktop demonstration.

30 fps is the playback/simulation time base, never a rendering performance result.
Each output is bound to its actual scene view, shader inputs and native proxies.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import math
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import time

import m3_compare as m3

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ('Camera', 'Object', 'Fog', 'Light', 'Reconstruction')
PROFILES = {'Off': ('Default',), 'Ours': ('High', 'MediumB', 'MediumG'),
            'NativeLFV': ('Default',), 'NativeVF': ('VFTR', 'VFNoTR')}
read, write, bind, check, close = m3.read, m3.write, m3.bind, m3.check, m3.close


def trajectory(shot, index, count):
    p = max(index, 0)/(count-1)
    u = .5-.5*math.cos(math.pi*p)
    eye, target = [-850, -950, 310], [280, 0, 145]
    sphere, prop = [300, 0, 180], [-100, -500, 160]
    k = [-1, -1, .15]; norm = math.sqrt(sum(x*x for x in k)); k = [x/norm for x in k]
    def rotate(v, degrees):
        a = math.radians(degrees)
        return [v[0]*math.cos(a)-v[1]*math.sin(a), v[0]*math.sin(a)+v[1]*math.cos(a), v[2]]
    if shot == 'Camera':
        offset = rotate([x-y for x, y in zip(eye, target)], -20+40*u)
        eye = [x+y for x, y in zip(target, offset)]
    if shot in ('Object', 'Reconstruction'):
        distance = -180+360*u
        prop = [-.6435*distance,-250+.7655*distance,160]
    if shot == 'Reconstruction': eye, target = [-650, -800, 260], [170, 0, 160]
    if shot == 'Fog': sphere[1] = -280+560*u
    if shot == 'Light': k = rotate(k, -75+150*u)
    f = [x-y for x, y in zip(target, eye)]; norm = math.sqrt(sum(x*x for x in f)); f = [x/norm for x in f]
    return eye, f, sphere, prop, k


def request(a):
    check(a.shot in SHOTS and a.mode in PROFILES, 'Unknown shot or mode')
    a.profile = a.profile or PROFILES[a.mode][0]
    check(a.profile in PROFILES[a.mode], 'Profile conflicts with mode')
    check(3 <= a.frames <= 900 and a.warmup in range(120, 601), 'Invalid output count or warmup')
    check(a.shot != 'Reconstruction' or a.mode == 'Ours', 'Reconstruction requires Ours')
    base = argparse.Namespace(mode=a.mode, profile=a.profile, camera='A', width=a.width, height=a.height,
                              frames=a.warmup, zero_local=False, output=a.output, config=ROOT/'tools/m3_config.json')
    r = m3.request(base)
    r.update(dynamicShot=a.shot, dynamicFrames=a.frames, dynamicFps=30)
    return r


def validate_sequence(s, r):
    check(s.get('kind') == 'UEFogLabDynamicSequence' and s.get('schemaVersion') == 1, 'Bad sequence schema')
    check(s['request'] == r and s['warmupTicks'] == r['frames'], 'Request/warmup mismatch')
    frames = s['frames']; count = r['dynamicFrames']; mode, profile = r['mode'], r['profile']
    check(mode in PROFILES and profile in PROFILES[mode] and r['dynamicShot'] in SHOTS, 'Illegal mode/profile/shot')
    check(r['dynamicFps'] == 30 and 3 <= count <= 900, 'Illegal sequence time base/count')
    check(len(frames) == count, 'Missing/extra output frames')
    check(frames[0]['view']['renderedViewIndex'] >= r['frames'], 'Insufficient actual rendered warmup')
    previous = None
    for i, f in enumerate(frames):
        v, d = f['view'], f['view']['dynamic']; actors = d['actors']; cv = v['cvars']
        check(f['outputIndex'] == d['outputIndex'] == i and f['image'] == f'frame-{i:05d}.png', 'Frame ordering/label mismatch')
        check(close(d['timeSeconds'], i/30, 1e-9) and close(d['phase'], i/(count-1), 1e-9), 'Animation time/phase mismatch')
        check(close(actors['deltaSeconds'], 1/30, 1e-6), 'Actual fixed timestep mismatch')
        if previous:
            for key in ('engineFrame', 'renderedViewIndex'):
                check(v[key] == previous['view'][key]+1, 'Nonconsecutive actual '+key)
            check(d['gameFrame'] == previous['view']['dynamic']['gameFrame']+1, 'Nonconsecutive GT frames')
            check(close(actors['worldTimeSeconds']-previous['view']['dynamic']['actors']['worldTimeSeconds'], 1/30, 3e-5), 'World time gap')
        check([f['imageWidth'], f['imageHeight']] == v['viewSize'] == [r['width'], r['height']], 'Actual viewport/image size mismatch')
        check(v['viewMin'] == [0, 0] and all(x >= y for x, y in zip(v['sceneExtent'], v['viewSize'])), 'Unexpected view rectangle')
        check(v['fogShowFlag'] and v['volumetricFogShowFlag'] and not v['localExposureShowFlag'] and close(v['preExposure'], 1), 'Actual exposure/show flags mismatch')
        check(v['autoExposureMethod'] == 2 and close(v['autoExposureBias'], 0) and not v['physicalCameraExposure'], 'Exposure configuration mismatch')
        eye, forward, sphere, prop, photon = trajectory(r['dynamicShot'], i, count)
        for actual, expected, label in ((v['cameraWorldCm'], eye, 'render camera'), (v['cameraForward'], forward, 'render forward'),
                (actors['camera']['positionCm'], eye, 'camera actor'), (d['sphereWorldCm'], sphere, 'sphere input'),
                (actors['geometry'][-1]['positionCm'], prop, 'opaque prop'), (d['photonDirection'], photon, 'photon input'),
                (actors['sun']['photonDirection'], photon, 'sun actor')):
            check(close(actual, expected, .002), 'Trajectory mismatch: '+label)
        check(len(actors['geometry']) == 15, 'Unexpected geometry count')
        static_geometry = [([200,0,-35],[24,20,.5]), ([900,0,280],[.3,18,6])]
        static_geometry += [([250,j*190,150],[.6,.6,3]) for j in range(-3,4)]
        static_geometry += [([-150,j*85,125],[.06,.06,2.5]) for j in range(-2,3)]
        check(all(close(g['positionCm'], p) and close(g['scale'], scale) for g,(p,scale) in zip(actors['geometry'], static_geometry)), 'Static geometry changed')
        check(close(actors['geometry'][-1]['scale'], [.35,.8,3.2]), 'Prop scale changed')
        check(len(d['sunRenderProxies']) == 1 and close(d['sunRenderProxies'][0]['photonDirection'], photon, .002), 'Actual native sun proxy mismatch')
        ours, native, vf = mode == 'Ours', mode.startswith('Native'), mode == 'NativeVF'
        check(actors['localFogActorCount'] == int(native) and actors['heightFogActorCount'] == int(vf), 'Actor mode contamination')
        check(len(d['lfvRenderProxies']) == int(native), 'LFV render proxy count mismatch')
        if native:
            l = d['lfvRenderProxies'][0]
            check(close(l['positionCm'], sphere, .002) and close(l['radiusCm'], 360, .001), 'Actual LFV proxy transform mismatch')
            check(close(actors['lfv']['positionCm'], sphere, .002) and close(actors['lfv']['scale'], [.72]*3), 'LFV actor transform mismatch')
            check(close(l['radialExtinction'], .35*16/15) and close(l['heightExtinction'], 8), 'Native medium changed')
        if ours:
            translated = [x+y for x,y in zip(sphere,d['preViewTranslationCm'])]
            check(close(d['shaderSphereTranslatedCm'], translated, .002), 'Actual shader sphere mismatch')
            check(close(d['shaderPhotonDirection'], photon, 1e-5), 'Actual shader photon mismatch')
            divisor = 1 if profile == 'High' else 2
            check(d['shaderFogSize'] == [(r[k]+divisor-1)//divisor for k in ('width','height')] and d['shaderSteps'] == 64, 'Actual shader quality mismatch')
        check(v['integrateExecutions'] == int(ours) and v['compositeExecutions'] == int(ours), 'Custom pass execution mismatch')
        expected = {'r.UEFog.Enabled':int(ours), 'r.LocalFogVolume':int(native), 'r.VolumetricFog':int(vf),
                    'r.LocalFogVolume.RenderIntoVolumetricFog':int(vf), 'r.LocalFogVolume.RenderDuringHeightFogPass':0,
                    'r.UEFog.Steps':64, 'r.UEFog.Downsample':2 if profile.startswith('Medium') else 1,
                    'r.UEFog.Reconstruction':int(profile != 'MediumB'), 'r.UEFog.LightingModel':1,
                    'r.UEFog.Extinction':.35, 'r.UEFog.DensityMode':1, 'r.UEFog.EarlyOut':0,
                    'r.UEFog.Albedo':.8, 'r.UEFog.HGg':.5, 'r.UEFog.IncidentR':4, 'r.UEFog.IncidentG':3, 'r.UEFog.IncidentB':2,
                    'r.VolumetricFog.TemporalReprojection':int(profile != 'VFNoTR'), 'r.VolumetricFog.Jitter':1,
                    'r.VolumetricFog.HistoryWeight':.9, 'r.VolumetricFog.GridPixelSize':8, 'r.VolumetricFog.GridSizeZ':128,
                    'r.AntiAliasingMethod':0, 'r.MotionBlurQuality':0, 'r.ScreenPercentage':100,
                    'r.DynamicRes.OperationMode':0, 'r.EyeAdaptation.PreExposureOverride':1, 'r.OneFrameThreadLag':0}
        for key, value in expected.items(): check(close(cv.get(key),value), 'Effective setting mismatch: '+key)
        # Independently reconstruct the expected camera basis and projection.
        right = [-forward[1], forward[0], 0]; n = math.sqrt(sum(x*x for x in right)); right = [x/n for x in right]
        up = [forward[1]*right[2]-forward[2]*right[1],forward[2]*right[0]-forward[0]*right[2],forward[0]*right[1]-forward[1]*right[0]]
        matrix = [right[0],up[0],forward[0],0,right[1],up[1],forward[1],0,right[2],up[2],forward[2],0,
                  -sum(x*y for x,y in zip(eye,right)),-sum(x*y for x,y in zip(eye,up)),-sum(x*y for x,y in zip(eye,forward)),1]
        check(close(v['worldToView'],matrix,.003), 'Actual view matrix mismatch')
        ys = math.sqrt(3)*16/9; clip = v['viewToClip']
        check(close(clip[0],ys*r['height']/r['width']) and close(clip[5],ys) and clip[11] == 1, 'Actual projection mismatch')
        previous = f
    return {'frames':count,'firstEngineFrame':frames[0]['view']['engineFrame'],'lastEngineFrame':frames[-1]['view']['engineFrame'],
            'shot':r['dynamicShot'],'mode':mode,'profile':profile,'continuous':True}


def trace_witness(out, sequence):
    with (out/'timers.csv').open(encoding='utf-8-sig',newline='') as f: timers = {r['Id']:r for r in csv.DictReader(f)}
    with (out/'gpu-events.csv').open(encoding='utf-8-sig',newline='') as f:
        rows = [dict(r, TimerName=timers[r['TimerId']]['Name']) for r in csv.DictReader(f)]
    count = len(sequence['frames']); mode = sequence['request']['mode']; result = []
    log = (out/'run.log').read_text(encoding='utf-8-sig',errors='replace')
    headers = list(re.finditer(r'GPU Profile for Frame (\d+) - (\w+) pipeline (\d+) - GPU (\d+)',log))
    profiles = []
    for n,h in enumerate(headers):
        lines = log[h.end():headers[n+1].start() if n+1 < len(headers) else len(log)].splitlines()
        names = [line.split('┃')[-2].strip() for line in lines if 'LogRHI:' in line and line.count('┃') >= 3]
        if h[2] == 'Graphics': profiles.append((h,names))
    for i, marker_name in ((0,'UEFogDynamicFirst'),(count//2,'UEFogDynamicMiddle'),(count-1,'UEFogDynamicLast')):
        markers = [r for r in rows if r['TimerName'] == marker_name]
        check(len(markers) == 1, 'Missing/ambiguous output GPU witness '+str(i))
        marker = markers[0]; t = float(marker['StartTime'])
        scenes = [r for r in rows if r['ThreadId'] == marker['ThreadId'] and r['TimerName'] == 'Scene' and float(r['StartTime']) <= t <= float(r['EndTime'])]
        check(len(scenes) == 1, 'Output GPU witness has no unique Scene ancestor')
        scene = scenes[0]
        trace_names = sorted(set(r['TimerName'] for r in rows if r['ThreadId'] == marker['ThreadId'] and float(scene['StartTime']) <= float(r['StartTime']) and float(r['EndTime']) <= float(scene['EndTime'])))
        matching = [(h,names) for h,names in profiles if names.count(marker_name) == 1]
        check(len(matching) == 1, 'ProfileGPU did not uniquely cover key output '+str(i))
        h,names = matching[0]
        check(any('LocalFogVolume.Render' in n for n in names) == (mode == 'NativeLFV'), 'Standalone LFV GPU path mismatch')
        check(any('LightScattering' in n for n in names) == (mode == 'NativeVF'), 'Native VF GPU path mismatch')
        check(any('UEFogDynamicIntegrateExecution' in n for n in names) == (mode == 'Ours'), 'Custom integrate GPU path mismatch')
        check(any('UEFogDynamicCompositeBind' in n for n in names) == (mode == 'Ours'), 'Custom composite bind GPU path mismatch')
        if mode == 'Ours':
            integrates = [n for n in names if n == 'UEFogDynamicIntegrate']
            check(len(integrates) == 1 and names.count('UEFogDynamicComposite') == 1, 'Missing/duplicate custom GPU passes')
        result.append({'outputIndex':i,'engineFrame':sequence['frames'][i]['view']['engineFrame'],'profileRhiFrame':int(h[1]),
                       'marker':marker,'scene':scene,'eventNames':sorted(set(names)),'traceEventNames':trace_names})
    return {'method':'ProfileGPU expanded event table and Insights Scene selected by the same unique first/middle/last GPU marker; frame domains kept separate', 'keyFrames':result}


def validate_capture(out):
    out = Path(out).resolve(); m = read(out/'capture.json'); s = read(out/'sequence.json')
    check(m['kind'] == 'UEFogLabDynamicCapture' and m['passed'] and m['exitConfirmed'] and m['processExitCode'] == 0, 'Incomplete capture')
    check(not m['sourceWorkingTree'], 'Capture had dirty source')
    result = validate_sequence(s,m['request'])
    for b in m['artifacts']: m3.check_bind(b)
    for f in s['frames']:
        header = (out/f['image']).read_bytes()[:24]
        check(header[:8] == b'\x89PNG\r\n\x1a\n' and
              [int.from_bytes(header[16:20],'big'),int.from_bytes(header[20:24],'big')] == [m['request']['width'],m['request']['height']], 'PNG dimensions/signature mismatch')
    check(trace_witness(out,s) == read(out/'gpu-witness.json'), 'GPU witness changed')
    return result


def run(a):
    out = Path(a.output).resolve(); r = request(a)
    check(bool(a.engine), 'Pass --engine or set UEFOGLAB_ENGINE_ROOT')
    check(m3.allowed_output(out) and not out.exists(), 'Use a NEW directory below repository Saved/, out/ or work/')
    dirty = subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).splitlines()
    check(not dirty, 'Commit implementation before capture')
    out.mkdir(parents=True); write(out/'request.json',r)
    rev = subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    editor = Path(a.engine)/'Engine/Binaries/Win64/UnrealEditor-Cmd.exe'
    args = [str(editor),str(ROOT/'UEFogLab.uproject'),'/Engine/Maps/Entry','-game','-dx12','-windowed',
            '-ResX='+str(a.width),'-ResY='+str(a.height),'-nosound','-NoSplash','-NoLiveCoding','-unattended','-RenderOffscreen',
            '-UseFixedTimeStep','-FPS=30','-FogLabCompareMode='+a.mode,'-FogLabCompareRequest='+str(out/'request.json'),
            '-trace=gpu,frame','-tracefile='+str(out/'gpu.utrace'),'-abslog='+str(out/'run.log')]
    m = {'kind':'UEFogLabDynamicCapture','schemaVersion':1,'passed':False,'exitConfirmed':False,'sourceRevision':rev,'sourceWorkingTree':dirty,
         'sourceInputs':m3.source_inputs(),'engineBuild':read(Path(a.engine)/'Engine/Build/Build.version'),'request':r,'launchArguments':args,
         'runtimeBinaries':[bind(ROOT/n) for n in ('Binaries/Win64/UnrealEditor-FogLabDemo.dll','Plugins/UEFog/Binaries/Win64/UnrealEditor-UEFog.dll')],
         'startedUtc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}
    for b in m['runtimeBinaries']:
        archive = ROOT/'Saved/M6-DYNAMIC-MVP/runtime'/Path(b['path']).name/b['sha256']; archive.parent.mkdir(parents=True,exist_ok=True)
        if not archive.exists(): shutil.copyfile(b['path'],archive)
        b['archive'] = bind(archive)
    write(out/'capture.json',m)
    try:
        with (out/'stdout.txt').open('w',encoding='utf8') as log:
            process = subprocess.run(args,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,timeout=480)
        m.update(processExitCode=process.returncode,exitConfirmed=True)
        check(process.returncode == 0 and (out/'sequence.json').is_file(), 'Unreal did not complete continuous capture')
        s = read(out/'sequence.json'); m['validation'] = validate_sequence(s,r)
        m['traceExportArguments'] = m3.export_trace(a.engine,out)
        write(out/'gpu-witness.json',trace_witness(out,s))
        m['artifacts'] = [bind(out/n) for n in ('request.json','sequence.json','gpu-witness.json','gpu.utrace','gpu-events.csv','threads.csv','timers.csv','run.log')]
        m['artifacts'] += [bind(out/f['image']) for f in s['frames']]
        m['passed'] = True
    except Exception as e:
        m['error'] = str(e)
        raise
    finally:
        write(out/'capture.json',m)
    validate_capture(out)
    print(json.dumps(m['validation']))


def main():
    p = argparse.ArgumentParser(description=__doc__); sub = p.add_subparsers(dest='action',required=True)
    q = sub.add_parser('run'); q.add_argument('--shot',required=True); q.add_argument('--mode',required=True); q.add_argument('--profile',default='')
    q.add_argument('--width',type=int,default=1280); q.add_argument('--height',type=int,default=720)
    q.add_argument('--frames',type=int,default=210); q.add_argument('--warmup',type=int,default=120); q.add_argument('--output',required=True)
    q.add_argument('--engine',default=os.environ.get('UEFOGLAB_ENGINE_ROOT',''))
    q = sub.add_parser('validate'); q.add_argument('directory')
    a = p.parse_args()
    try:
        if a.action == 'run': run(a)
        else: print(json.dumps(validate_capture(a.directory)))
    except (ValueError,KeyError,OSError,subprocess.SubprocessError) as e:
        print('REJECTED: '+str(e),file=sys.stderr); return 2
    return 0

if __name__ == '__main__': raise SystemExit(main())
