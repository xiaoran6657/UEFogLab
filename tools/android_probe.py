"""PRE-03 isolated mobile GPU path probe. Never installs a negative fixture.

Uses PRE-02's dedicated-package ADB helpers; this has its own run/probe schema
and independent geometric/numeric checks, not the old fog numerical schemas.
"""
import argparse
import copy
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid
import numpy as np
from PIL import Image

from android_baseline import ROOT, allowed, audit, bind, check, git, read, utc, verify, write
from android_run import (PKG, POLICY, EXPECTED_CVARS, Commands, select, installed,
                         device_info, foreground, events_from, proc_identity, new_output)

LOCATIONS = np.array([[.35,.45],[.4,.5],[.45,.55],[.6,.45],[.65,.5],[.7,.55],
                      [.2,.1],[.5,.1],[.8,.1]], dtype=np.float64)
PROTOCOL = {'version': 1, 'readyTimeout': 180, 'observeSeconds': 30, 'minimumFrames': 120,
            'probeBeforeDofIndex': 180, 'depthAbsCm': 1, 'depthRelative': .01,
            'halfAbs': .001, 'floatAbs': .00001, 'colorAbs': .001, 'colorRelative': .01}


def collect(a):
    out = new_output(a.output)
    check(not git('status', '--porcelain'), 'Commit probe source/protocol/tool before capture')
    candidate = Path(a.candidate).resolve(); cooked = audit(candidate); m = read(candidate/'package.json')
    old_candidate = audit(a.previous_candidate) if a.previous_candidate else None
    c = Commands(out, select(a.serial)); run_id = uuid.uuid4().hex
    r = {'kind': 'UEFogLab.AndroidPathProbeRun', 'schemaVersion': 1, 'protocol': PROTOCOL,
         'runId': run_id, 'mode': a.mode, 'nearCm': a.near, 'scale': a.scale, 'preExposure': a.exposure,
         'candidate': bind(candidate/'package.json'), 'toolRevision': git('rev-parse', 'HEAD'),
         'tool': bind(__file__), 'package': PKG, 'serial': c.serial, 'startedUtc': utc(),
         'completed': False, 'cleanupConfirmed': False}
    write(out/'run.json', r); owned = False; remote = None
    try:
        r['device'] = device_info(c)
        old = installed(c, 'before')
        if old:
            trusted = [m['package']['apk']['sha256']]
            if old_candidate:
                check(old_candidate['identity']['certificateSha256'] == cooked['identity']['certificateSha256'], 'Previous candidate signer differs')
                trusted.append(old_candidate['installationSet'][0]['sha256'])
            check(old['apk']['sha256'] in trusted and old['certificateSha256'] == cooked['identity']['certificateSha256'], 'Unknown installed package origin/signature; refuse overwrite')
        owned = True; r['beforeInstallation'] = old
        c.shell('initial-stop', 'am', 'force-stop', PKG)
        c.run('install', ['install', '-r', m['package']['apk']['path']])
        r['installation'] = installed(c, 'installed')
        check(r['installation']['apk']['sha256'] == m['package']['apk']['sha256'], 'Installed SHA mismatch')
        private = c.shell('private-root', 'run-as', PKG, 'pwd')
        check(re.fullmatch(r'/data/user/\d+/'+re.escape(PKG), private), 'Unexpected private app root')
        # Reuse the existing startup witness's strictly scoped private UUID root.
        remote = private+'/files/M4-PRE-02/'+run_id
        c.shell('mkdir', 'run-as', PKG, 'mkdir', '-p', remote)
        c.shell('cold-stop', 'am', 'force-stop', PKG)
        check(not c.shell('cold-pid', 'pidof', PKG, ok=False), 'Package survived cold stop')
        r['deviceEpochStart'] = c.shell('device-start-time', 'date', '+%s.%N')
        command = m['package']['commandLine']+' -FogLabMobileRun='+run_id+' -FogLabMobileOutput='+remote+' -abslog='+remote+'/engine.log'
        command += f' -FogLabMobileProbe={a.mode} -FogLabProbeNear={a.near} -FogLabProbeScale={a.scale} -FogLabPreExposure={a.exposure}'
        r['commandLine'], r['privateDirectory'], r['launchUtc'] = command, remote, utc()
        start = time.monotonic()
        answer = c.shell('start', 'am', 'start', '-W', '-n', PKG+'/'+cooked['identity']['activity'], '--es', 'cmdline', command)
        check('Status: ok' in answer and 'LaunchState: COLD' in answer, 'Not an actual successful COLD launch')
        ready = None; pid = None
        for index in range(160):
            ev = events_from(c.own_file('poll-'+str(index), remote+'/events.jsonl', ok=False))
            launch = next((e for e in ev if e.get('event') == 'launch' and e.get('runId') == run_id), None)
            if launch and pid is None:
                pid = str(int(launch['pid'])); r['pid'] = int(pid)
                check(pid in c.shell('pid', 'pidof', PKG).split(), 'Witness PID not in live package PID set')
                c.shell('process-cmdline', 'run-as', PKG, 'cat', '/proc/'+pid+'/cmdline')
                stat = c.shell('process-stat', 'run-as', PKG, 'cat', '/proc/'+pid+'/stat')
                actual_pid, r['processStartTicks'] = proc_identity(stat)
                check(actual_pid == int(pid), 'Wrong kernel process')
            if any(e.get('event') == 'scene-ready' and e.get('runId') == run_id for e in ev) and ready is None:
                ready = time.monotonic(); r['readyObservedSeconds'] = ready-start
                foreground(c, 'ready-foreground'); print('Probe '+a.mode+' scene ready; observing frozen startup window', flush=True)
            if any(e.get('event') == 'capture-complete' and e.get('runId') == run_id for e in ev): break
            check(time.monotonic()-start < 180 if ready is None else time.monotonic()-ready < 120, 'Frozen startup/probe timeout')
            time.sleep(2)
        else: raise ValueError('No completed capture')
        for name, filename in [('events','events.jsonl'), ('frame','frame.png'), ('engine','engine.log'), ('probe','probe.json')]:
            c.own_file(name, remote+'/'+filename)
        if a.mode == 'On': c.own_file('gpu-buffer', remote+'/probe-buffer.bin')
        foreground(c, 'capture-foreground')
        check(pid in c.shell('final-pid', 'pidof', PKG).split(), 'Main process died or restarted')
        c.shell('final-process-stat', 'run-as', PKG, 'cat', '/proc/'+pid+'/stat')
        r['deviceEpochEnd'] = c.shell('device-end-time', 'date', '+%s.%N')
        c.run('logcat', ['logcat', '-d', '--pid='+pid, '-v', 'epoch', '-T', r['deviceEpochStart']])
        r.update(completed=True, finishedUtc=utc())
    except Exception as error:
        r.update(error=str(error), finishedUtc=utc())
        if remote:
            for name, filename in [('failure-events','events.jsonl'), ('failure-engine','engine.log'), ('failure-probe','probe.json')]:
                try: c.own_file(name, remote+'/'+filename, ok=False)
                except Exception: pass
        raise
    finally:
        if owned:
            try:
                c.shell('cleanup-stop', 'am', 'force-stop', PKG)
                r['cleanupConfirmed'] = not c.shell('cleanup-pid', 'pidof', PKG, ok=False)
            except Exception as error: r['cleanupError'] = str(error)
        r['artifacts'] = [bind(p) for p in sorted(out.iterdir()) if p.is_file() and p.name not in ('run.json','validation.json')]
        write(out/'run.json', r)
    result = validate(out); write(out/'validation.json', result)
    print('Mobile probe '+a.mode+' passed: '+run_id, flush=True)


def bound_required(r, out, names):
    paths = [Path(b['path']).resolve() for b in r['artifacts']]
    check(len(paths) == len(set(paths)) and all(out/n in paths for n in names), 'Missing/duplicate bound raw probe evidence')
    for b in r['artifacts']: verify(b)


def numeric(p, r, out):
    check(p['gpuCompletionConfirmed'] is True and p['status'] == 'complete', 'Missing GPU completion/readback')
    check(p['writeDispatches'] == p['sampleDispatches'] == p['readbackRequests'] == 1, 'Wrong GPU diagnostic work count')
    check(p['completionFrame'] > p['frame'] > 0 and p['beforeDofCallbacks'] >= 180, 'Wrong probe scheduling/completion frame')
    check(p['bufferBytes'] == 320 and p['bufferStride'] == 16 and p['dtype'] == '<f4', 'Invalid structured readback layout')
    known = p['knownTexture']
    check(known['format'].removeprefix('PF_') == 'FloatRGBA' and known['platformFormat'] == 97 and known['extent'] == [2,1] and
          known['samples'] == 1 and known['uavSupported'] and known['sampleSupported'], 'Wrong tested RGBA16F resource route')
    data = np.fromfile(out/'gpu-buffer.stdout', dtype='<f4')
    check(data.size == 80 and np.isfinite(data).all(), 'Missing/invalid GPU readback bytes')
    data = data.reshape(20,4).astype(np.float64)
    expected = np.array([.25*r['scale'], .5*r['scale'], 2*r['scale'], 1])
    check(np.max(abs(data[0]-expected)) <= .001 and data[0,2] > 1, 'Known RGBA16F write/sample mismatch')
    tags = int(r['runId'][:5],16)
    marker = [tags&1023, (tags>>10)&1023, p['frame'], r['scale']]
    check(np.max(abs(data[1]-marker)) <= .00001, 'Wrong runId/frame GPU marker')
    check(np.all(data[2::2,3] == p['frame']) and np.all(abs(data[2::2,2]-p['preExposure']) <= .00001), 'Wrong GPU sample frame/exposure')
    inverse = np.array(p['clipToWorld']).reshape(4,4); projection = np.array(p['worldToClip']).reshape(4,4)
    check(np.max(abs(inverse@projection-np.eye(4))) < 1e-6, 'Inconsistent actual view matrices')
    camera = np.array(p['cameraCm'],dtype=float); size = np.array(p['viewSize']); origin = np.array(p['viewMin'])
    check(np.max(abs(camera)) < 1e-5, 'Unexpected fixture camera')
    near = float(r['nearCm'])
    boxes = [(np.array([near,-240,-60]),np.array([near+2,0,60])),
             (np.array([1200,0,-120]),np.array([1202,700,120]))]
    checks = []
    for i, uv in enumerate(LOCATIONS):
        pixel = origin+np.floor(uv*size).astype(int)
        view_uv = (pixel-origin+.5)/size
        ndc = np.array([view_uv[0]*2-1,(1-view_uv[1]*2)/p['projectionSignY'],1,1])
        point = ndc@inverse; point = point[:3]/point[3]
        direction = point-camera; direction /= np.linalg.norm(direction)
        hits = []
        for box_index,(lo,hi) in enumerate(boxes):
            entry,leave=0.,math.inf
            for axis in range(3):
                if abs(direction[axis])<1e-12:
                    if camera[axis]<lo[axis] or camera[axis]>hi[axis]: leave=-math.inf
                else:
                    a=(lo[axis]-camera[axis])/direction[axis]; b=(hi[axis]-camera[axis])/direction[axis]
                    entry=max(entry,min(a,b)); leave=min(leave,max(a,b))
            if leave >= entry: hits.append((entry,box_index))
        hit = min(hits) if hits else None
        device_z, axis_depth = data[2+2*i,:2]
        if i >= 6:
            check(hit is None and abs(device_z) <= 1e-7, 'Sky classification mismatch')
            checks.append({'sample':i,'pixel':pixel.tolist(),'class':'sky','deviceZ':device_z})
            continue
        check(hit is not None and hit[1] == (0 if i<3 else 1), 'Frozen sample does not hit expected non-edge plane')
        distance, box_index = hit; position = camera+distance*direction
        clip=np.append(position,1)@projection; expected_z=clip[2]/clip[3]
        # Camera is exactly +X at origin. View-axis distance is X; Euclidean ray
        # distance is computed independently from the ray/AABB intersection.
        expected_axis = position[0]; measured_ray = axis_depth/direction[0]
        axis_error=abs(axis_depth-expected_axis); ray_error=abs(measured_ray-distance)
        check(axis_error <= max(1,.01*expected_axis) and ray_error <= max(1,.01*distance), 'Plane depth/ray-distance mismatch')
        check(abs(device_z-expected_z) <= max(1e-6,.01*abs(expected_z)), 'Raw DeviceZ does not match actual projection')
        expected_color=np.array([.25,.5,2])*p['preExposure']; color=data[3+2*i,:3]
        check(np.all(abs(color-expected_color) <= np.maximum(.001,.01*expected_color)) and color[2] > 1, 'Actual pre-tonemap linear HDR SceneColor mismatch')
        checks.append({'sample':i,'pixel':pixel.tolist(),'class':'near' if box_index==0 else 'far',
                       'expectedAxisCm':expected_axis,'observedAxisCm':axis_depth,'axisErrorCm':axis_error,
                       'expectedRayCm':distance,'observedRayCm':measured_ray,'rayErrorCm':ray_error,
                       'expectedDeviceZ':expected_z,'deviceZ':device_z,'sceneColorRGB':color.tolist()})
    return {'knownValue': data[0].tolist(), 'gpuMarker': data[1].tolist(), 'samples':checks}


def validate(directory):
    out=Path(directory).resolve(); r=read(out/'run.json')
    check(r['kind']=='UEFogLab.AndroidPathProbeRun' and r['schemaVersion']==1 and r['protocol']==PROTOCOL, 'Wrong probe run schema/protocol')
    check(r['package']==PKG and re.fullmatch('[0-9a-f]{32}',r['runId']) and r['mode'] in ('On','Off'), 'Wrong probe package/mode/runId')
    required={'commands.json','events.stdout','frame.stdout','engine.stdout','probe.stdout','installed-pm.stdout','installed-paths.stdout',
              'installed-signature.stdout','process-cmdline.stdout','process-stat.stdout','final-process-stat.stdout','pid.stdout','final-pid.stdout',
              'ready-foreground.stdout','capture-foreground.stdout','cleanup-pid.stdout','cold-pid.stdout','device-start-time.stdout','device-end-time.stdout','logcat.stdout','start.stdout','install.stdout'}
    if r['mode']=='On': required.add('gpu-buffer.stdout')
    bound_required(r,out,required); verify(r['candidate']); m=read(r['candidate']['path'])
    check(r['completed'] and r['cleanupConfirmed'], 'Incomplete run/cleanup')
    installation=r['installation']; verify(installation['apk'])
    check(installation['apk']['sha256']==m['package']['apk']['sha256'] and installation['certificateSha256']==m['package']['identity']['certificateSha256'], 'Installed candidate/signer mismatch')
    check(Path(installation['packageManager']['path']).resolve()==out/'installed-pm.stdout','Unbound PackageManager')
    pm=(out/'installed-pm.stdout').read_text()
    check(re.search(r'\bversionCode='+str(m['package']['identity']['versionCode'])+r'\b',pm) and 'versionName='+m['package']['identity']['versionName'] in pm,'PackageManager version mismatch')
    check(re.findall(r'^package:(.+)$',(out/'installed-paths.stdout').read_text(),re.M)==installation['devicePaths'] and len(installation['devicePaths'])==1,'Installation set mismatch')
    signing=(out/'installed-signature.stdout').read_text()
    check('Verified using v2 scheme (APK Signature Scheme v2): true' in signing and installation['certificateSha256'] in signing,'Installed signature witness missing')
    for name in ('process-stat','final-process-stat'):
        check(proc_identity((out/(name+'.stdout')).read_text())==(r['pid'],r['processStartTicks']) and r['processStartTicks']>0,'Wrong/stale kernel PID identity')
    check((out/'process-cmdline.stdout').read_bytes().split(b'\0')[0].decode()==PKG,'Not the package main process')
    for name in ('pid','final-pid'): check(str(r['pid']) in (out/(name+'.stdout')).read_text().split(),'Main process restart/death')
    for name in ('cold-pid','cleanup-pid'): check(not (out/(name+'.stdout')).read_text().strip(),'Package did not stop')
    for name in ('ready-foreground','capture-foreground'):
        check(re.search(r'(?:topResumedActivity|mResumedActivity|Resumed:).*'+re.escape(PKG)+'/',(out/(name+'.stdout')).read_text()),'Not own foreground application')
    commands=read(out/'commands.json'); by_name={s['name']:s for s in commands}
    check(len(by_name)==len(commands),'Duplicate commands')
    for name in ('install','start','process-stat','final-process-stat','cleanup-stop','events','frame','probe','engine','logcat'):
        check(by_name.get(name,{}).get('exitCode')==0,'Missing/failed own command '+name)
    check('LaunchState: COLD' in (out/'start.stdout').read_text() and 'Status: ok' in (out/'start.stdout').read_text(),'Not a cold launch')
    lc=by_name['logcat']['command']
    check('--pid='+str(r['pid']) in lc and '-T' in lc and lc[lc.index('-T')+1]==r['deviceEpochStart'],'Unscoped logcat')
    check((out/'device-start-time.stdout').read_text().strip()==r['deviceEpochStart'] and (out/'device-end-time.stdout').read_text().strip()==r['deviceEpochEnd'] and
          30 <= float(r['deviceEpochEnd'])-float(r['deviceEpochStart']) < 360,'Wrong device time window')
    events=[json.loads(line) for line in (out/'events.stdout').read_text(encoding='utf-8-sig').splitlines()]
    check(events and all(e['runId']==r['runId'] and e['pid']==r['pid'] and e['processStartTicks']==r['processStartTicks'] and
          e['declaredBuildId']==m['buildId'] and e['declaredSourceRevision']==m['sourceRevision'] for e in events),'Wrong run/candidate/process startup witness')
    def one(name):
        a=[e for e in events if e['event']==name]; check(len(a)==1,'Missing/duplicate '+name); return a[0]
    launch,ready,capture=one('launch'),one('scene-ready'),one('capture-complete')
    check(launch['commandLine'].strip()==r['commandLine'].strip() and launch['engineVersion'].startswith('5.8.4-58902052'),'Wrong actual command/engine')
    check(0 <= r['readyObservedSeconds'] <= 180 and capture['secondsAfterSceneReady'] >= 30 and capture['saved'] and capture['hasFocus'],'Frozen startup survival gate failed')
    check(ready['map']=='/Engine/Maps/Entry' and ready['gameMode']=='/Script/FogLabDemo.FogLabGameMode' and ready['cube']=='/Engine/BasicShapes/Cube.Cube' and ready['staticMeshActors']==2,'Wrong known fixture scene/assets')
    last=capture['lastObservedView']; check(last['runId']==r['runId'] and last['renderedViewFamilies']>=120,'No actual game-view frame advancement')
    with Image.open(out/'frame.stdout') as im:
        im.load(); check(im.format=='PNG' and list(im.size)==[capture['width'],capture['height']] and min(im.size)>=360,'Invalid actual app PNG')
    p=read(out/'probe.stdout')
    check(p.get('kind')=='UEFogLab.AndroidPathProbe' and p.get('schemaVersion')==1,'Wrong GPU probe schema')
    check(p['runId']==r['runId'] and p['pid']==r['pid'] and p['buildId']==m['buildId'] and p['sourceRevision']==m['sourceRevision'],'Wrong runId/candidate GPU witness')
    check(p['mode']==r['mode'] and p['scale']==r['scale'],'Wrong probe mode/value')
    check(p['rhi']=='Vulkan' and p['featureLevel']=='ES3_1' and p['shaderFormat']=='SF_VULKAN_ES31_ANDROID','Wrong actual mobile Vulkan platform')
    expected=dict(EXPECTED_CVARS, **{'r.Mobile.ForceDepthResolve':1})
    check(p['cvars']==expected and p['oldFogPasses']==0,'Wrong effective path/fog/depth configuration')
    check(p['mobilePrePostCallbacks']>0 and p['beforeDofCallbacks']>=180 and p['callback']=='Mobile BeforeDOF','Missing actual mobile callbacks')
    check(p['fixture']['name']=='MobilePlanesV1' and p['fixture']['nearCm']==r['nearCm'] and p['fixture']['farCm']==1200 and
          'Color' in p['fixture']['materialVectorParameters'] and p['fixture']['blendMode']==0 and p['fixture']['shadingModels']==1,'Wrong actual opaque/unlit fixture/material input')
    check(p['preExposure']==r['preExposure'] and p['viewMin']==[0,0] and p['viewSize']==last['unscaledViewSize'] and
          p['surfaceSize']==p['viewSize'],'Wrong actual view/exposure')
    check(p['sceneDepth']['valid'] and p['sceneDepth']['samples']==1 and p['sceneDepth']['extent']==p['sceneColor']['extent'] and
          min(p['sceneDepth']['extent'])>1,'Dummy/invalid native depth input')
    check(p['sceneColor']['valid'] and p['sceneColor']['samples']==1 and p['sceneColor']['format'].removeprefix('PF_') in ('FloatRGBA','FloatR11G11B10','FloatRGB'),'Not native HDR SceneColor input')
    result=None
    if r['mode']=='Off':
        check(p['status']=='control-complete' and p['writeDispatches']==p['sampleDispatches']==p['readbackRequests']==0 and not p['gpuCompletionConfirmed'] and
              not (out/'gpu-buffer.stdout').exists() and 'knownTexture' not in p,'Probe-off performed diagnostic GPU work')
    else: result=numeric(p,r,out)
    engine=(out/'engine.stdout').read_text(encoding='utf8',errors='replace')
    check(r['runId'] in engine and not re.search(r'Fatal error|Assertion failed|Log(?:Material|Shader)[^\n]*: Error:',engine,re.I),'Engine/scene/shader failure')
    live=audit(Path(r['candidate']['path']).parent)
    pak=(Path(r['candidate']['path']).parent/'pak-list.txt').read_text(encoding='utf8')
    check('LevelColorationUnlitMaterial.uasset' in pak,'Opaque unlit scene material not cooked')
    return {'kind':'UEFogLab.AndroidPathProbeAudit','schemaVersion':1,'passed':True,'run':bind(out/'run.json'),
            'runId':r['runId'],'mode':r['mode'],'candidate':r['candidate'],'apk':m['package']['apk'],
            'probe':bind(out/'probe.stdout'),'gpu':result,'initialPath':p,
            'currentCookVerification':live['currentArtifactVerification'],
            'checks':['exact installed/cooked candidate','fresh UUID/PID/kernel/foreground','frozen survival gate','mobile callback/path',
                      'no old fog work','off zero diagnostic control' if r['mode']=='Off' else 'GPU completed dynamic marker/known half values/native depth/actual linear HDR'],
            'limits':['only MEIZU21 Vulkan Forward HDR/MSAA1 with forced preserved native depth',
                      'RGBA16F compute UAV->SRV tested; RT execution and R32_FLOAT not tested','not fog optics/quality/performance']}


def negative(a):
    source=Path(a.input).resolve(); validate(source); original=read(source/'run.json')
    check(original['mode']=='On','Negative suite requires completed On data'); out=new_output(a.output); cases=[]
    for name in ('missing-completion','missing-readback','wrong-runid','wrong-frame','desktop-sm5','gles'):
        dest=out/name; dest.mkdir(); r=copy.deepcopy(original)
        for f in source.iterdir():
            if f.suffix in ('.stdout','.stderr') or f.name=='commands.json': shutil.copyfile(f,dest/f.name)
        p=read(dest/'probe.stdout')
        if name=='missing-completion': p['gpuCompletionConfirmed']=False
        elif name=='missing-readback': (dest/'gpu-buffer.stdout').write_bytes(b'')
        elif name=='wrong-runid': p['runId']=uuid.uuid4().hex
        elif name=='wrong-frame':
            data=np.fromfile(dest/'gpu-buffer.stdout',dtype='<f4').reshape(20,4)
            data[1,2]+=1; data.tofile(dest/'gpu-buffer.stdout')
        elif name=='desktop-sm5': p.update(featureLevel='SM5',shaderFormat='SF_VULKAN_SM5')
        else: p.update(rhi='OpenGL',shaderFormat='GLSL_ES3_1_ANDROID')
        write(dest/'probe.stdout',p)
        r['installation']['packageManager']=bind(dest/'installed-pm.stdout')
        r['artifacts']=[bind(dest/Path(b['path']).name) if (dest/Path(b['path']).name).exists() else b for b in r['artifacts']]
        write(dest/'run.json',r)
        proc=subprocess.run([sys.executable,str(Path(__file__)),'validate','--input',str(dest),'--output',str(dest/'cli.json')],capture_output=True,timeout=180)
        (dest/'cli.stdout').write_bytes(proc.stdout); (dest/'cli.stderr').write_bytes(proc.stderr)
        value=read(dest/'cli.json')
        expected={'missing-completion':'Missing GPU completion','missing-readback':'Missing/invalid GPU readback',
                  'wrong-runid':'Wrong runId/candidate GPU witness','wrong-frame':'Wrong runId/frame GPU marker',
                  'desktop-sm5':'Wrong actual mobile Vulkan platform','gles':'Wrong actual mobile Vulkan platform'}
        check(proc.returncode==2 and value['passed'] is False and expected[name] in ' '.join(value['errors']),'Wrong negative rejection: '+name)
        cases.append({'case':name,'exitCode':proc.returncode,'errors':value['errors'],'result':bind(dest/'cli.json')})
    write(out/'negative.json',{'kind':'UEFogLab.AndroidProbeNegative','passed':True,'cases':cases}); print('Six semantic GPU-probe negatives rejected',flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__); subs=parser.add_subparsers(dest='action',required=True)
    run=subs.add_parser('run'); run.add_argument('--candidate',required=True); run.add_argument('--previous-candidate',default='')
    run.add_argument('--serial',default=''); run.add_argument('--mode',choices=['Off','On'],required=True)
    run.add_argument('--near',type=int,choices=[400,600],default=400); run.add_argument('--scale',type=int,choices=[1,2],default=1); run.add_argument('--exposure',type=int,choices=[1,2],default=1)
    for name in ('validate','negative'):
        p=subs.add_parser(name); p.add_argument('--input',required=True)
    for p in subs.choices.values(): p.add_argument('--output',required=True)
    a=parser.parse_args()
    try:
        if a.action=='run': collect(a)
        elif a.action=='negative': negative(a)
        else:
            check(allowed(a.output) and not Path(a.output).exists(),'Require a new ignored validation output')
            write(a.output,validate(a.input)); print('Mobile path probe audit passed',flush=True)
        return 0
    except Exception as error:
        if a.action=='validate' and allowed(a.output) and not Path(a.output).exists(): write(a.output,{'kind':'UEFogLab.AndroidPathProbeAudit','passed':False,'errors':[str(error)]})
        print('REJECTED: '+str(error),file=sys.stderr); return 2

if __name__=='__main__': sys.exit(main())
