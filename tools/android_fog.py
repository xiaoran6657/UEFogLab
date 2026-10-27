"""M4 isolated mobile Uniform Full64 device capture/audit. Never installs negatives.
Uses dedicated PRE ADB/hash helpers; old probe/desktop validators are unchanged.
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

from mobile_fog_reference import PROFILE, MODEL, analyze as analyze_samples, ray, decode_depth, medium_length, optics, half, compose

PROTOCOL = {'version':1,'readyTimeout':180,'observeSeconds':30,'minimumFrames':120,'beforeDofIndex':180,'sampleCount':25,'bufferBytes':1632,'STAbsolute':.001,'HDRAbsoluteFloor':.0002,'HDR_ULP16':1}

def collect(a):
    out = new_output(a.output)
    check(not git('status', '--porcelain'), 'Commit fog source/protocol/tool before capture')
    candidate = Path(a.candidate).resolve(); cooked = audit(candidate); m = read(candidate/'package.json'); check_candidate(candidate,m)
    old_candidate = audit(a.previous_candidate) if a.previous_candidate else None
    c = Commands(out, select(a.serial)); run_id = uuid.uuid4().hex
    r = {'kind': 'UEFogLab.MobileUniformFogRun', 'schemaVersion': 1, 'protocol': PROTOCOL,
         'runId': run_id, 'mode': a.mode, 'nearCm': a.near, 'extinctionPerMetre': a.extinction, 'modelKey': MODEL, 'preExposure': a.exposure,
         'candidate': bind(candidate/'package.json'), 'toolRevision': git('rev-parse', 'HEAD'),
         'tool': bind(__file__), 'profile': bind(candidate/'source-inputs/tools/mobile_fog_profile.json'), 'package': PKG, 'serial': c.serial, 'startedUtc': utc(),
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
        diagnostic = 1 if a.mode == 'Full' else 0
        command += f' -FogLabMobileFog={a.mode} -FogLabFogNear={a.near} -FogLabFogSigma={a.extinction} -FogLabFogReadback={diagnostic} -FogLabPreExposure={a.exposure}'
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
                foreground(c, 'ready-foreground'); print('Mobile fog '+a.mode+' scene ready; observing frozen startup window', flush=True)
            if any(e.get('event') == 'capture-complete' and e.get('runId') == run_id for e in ev): break
            check(time.monotonic()-start < 180 if ready is None else time.monotonic()-ready < 120, 'Frozen startup/probe timeout')
            time.sleep(2)
        else: raise ValueError('No completed capture')
        for name, filename in [('events','events.jsonl'), ('frame','frame.png'), ('engine','engine.log'), ('fog','fog.json')]:
            c.own_file(name, remote+'/'+filename)
        if a.mode == 'Full': c.own_file('gpu-buffer', remote+'/fog-buffer.bin')
        foreground(c, 'capture-foreground')
        check(pid in c.shell('final-pid', 'pidof', PKG).split(), 'Main process died or restarted')
        c.shell('final-process-stat', 'run-as', PKG, 'cat', '/proc/'+pid+'/stat')
        r['deviceEpochEnd'] = c.shell('device-end-time', 'date', '+%s.%N')
        c.run('logcat', ['logcat', '-d', '--pid='+pid, '-v', 'epoch', '-T', r['deviceEpochStart']])
        r.update(completed=True, finishedUtc=utc())
    except Exception as error:
        r.update(error=str(error), finishedUtc=utc())
        if remote:
            for name, filename in [('failure-events','events.jsonl'), ('failure-engine','engine.log'), ('failure-fog','fog.json')]:
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
    print('Mobile fog '+a.mode+' passed: '+run_id, flush=True)


def bound_required(r, out, names):
    paths = [Path(b['path']).resolve() for b in r['artifacts']]
    check(len(paths) == len(set(paths)) and all(out/n in paths for n in names), 'Missing/duplicate bound raw probe evidence')
    for b in r['artifacts']: verify(b)



def check_candidate(candidate,m):
    required={'Plugins/UEFog/Shaders/Private/UEFogMobileFog.usf','Plugins/UEFog/Source/UEFog/Private/UEFogMobileFog.cpp',
              'Source/FogLabDemo/FogLabMobileFogFixture.cpp','tools/mobile_fog_profile.json','tools/mobile_fog_reference.py','tools/android_fog.py'}
    names={Path(x['path']).relative_to(ROOT).as_posix() for x in m['sourceInputs']}
    check(required<=names,'candidate lacks new mobile fog shader/source/tool snapshot; PRE APK cannot prove fog')
    check(read(candidate/'source-inputs/tools/mobile_fog_profile.json')==PROFILE,'frozen fog profile differs from current analysis')


def numeric(p,r,out):
    check(p['gpuCompletionConfirmed'] is True and p['status']=='complete','missing GPU completion/readback')
    check(p['completionFrame']>p['frame']>0 and p['observerFrame']==p['frame'],'wrong fog scheduling/completion frame')
    check([p[k] for k in ('integrateDispatches','compositePasses','sampleDispatches','readbackRequests')]==[1,1,1,1],'wrong fog work schedule')
    check(p['bufferBytes']==1632 and p['bufferStride']==16 and p['dtype']=='<f4','wrong fog sample layout')
    check(p['returnedResourceEqualsObserverInput'] is True,'returned resource not consumed by engine observer')
    for name in ('stTexture','returnedOutput','observerInput'):
        d=p[name]
        check(d['valid'] and d['format'].removeprefix('PF_')=='FloatRGBA' and d['platformFormat']==97 and d['samples']==1 and d['extent']==p['viewSize'] and not d['memoryless'],'wrong actual RGBA16F route: '+name)
    check(p['stTexture']['uavSupported'] and p['stTexture']['flags']&131072 and p['returnedOutput']['rtSupported'] and p['returnedOutput']['flags']&1,'missing actual ST UAV or composition RT usage')
    check(p['observerInput']==p['returnedOutput'],'observer received a different returned descriptor')
    check(not p['overrideOutputActive'],'unfrozen terminal override path')
    return analyze_samples(p,(out/'gpu-buffer.stdout').read_bytes())

def validate(directory):
    out=Path(directory).resolve(); r=read(out/'run.json')
    check(r['kind']=='UEFogLab.MobileUniformFogRun' and r['schemaVersion']==1 and r['protocol']==PROTOCOL, 'Wrong probe run schema/protocol')
    check(r['package']==PKG and re.fullmatch('[0-9a-f]{32}',r['runId']) and r['mode'] in ('Full','Off'), 'Wrong probe package/mode/runId')
    required={'commands.json','events.stdout','frame.stdout','engine.stdout','fog.stdout','installed-pm.stdout','installed-paths.stdout',
              'installed-signature.stdout','process-cmdline.stdout','process-stat.stdout','final-process-stat.stdout','pid.stdout','final-pid.stdout',
              'ready-foreground.stdout','capture-foreground.stdout','cleanup-pid.stdout','cold-pid.stdout','device-start-time.stdout','device-end-time.stdout','logcat.stdout','start.stdout','install.stdout'}
    if r['mode']=='Full': required.add('gpu-buffer.stdout')
    bound_required(r,out,required); verify(r['candidate']); m=read(r['candidate']['path']); check_candidate(Path(r['candidate']['path']).parent,m); verify(r['profile']); check(r['modelKey']==MODEL,'wrong run model')
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
    for name in ('install','start','process-stat','final-process-stat','cleanup-stop','events','frame','fog','engine','logcat'):
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

    p=read(out/'fog.stdout')
    check(p.get('kind')=='UEFogLab.MobileUniformFog' and p.get('schemaVersion')==1 and p.get('modelKey')==MODEL,'wrong mobile fog schema/model')
    check(not p.get('cpuSynthetic',False),'CPU synthetic cannot establish DeviceRun')
    check(p['runId']==r['runId'] and p['pid']==r['pid'] and p['buildId']==m['buildId'] and p['sourceRevision']==m['sourceRevision'],'wrong runId/candidate fog witness')
    check(p['mode']==r['mode'] and p['nearCm']==r['nearCm'] and p['extinctionPerMetre']==r['extinctionPerMetre'],'wrong fog mode/parameters')
    check(p['preExposure']==r['preExposure'],'actual exposure mismatch')
    check(p['rhi']=='Vulkan' and p['featureLevel']=='ES3_1' and p['shaderFormat']=='SF_VULKAN_ES31_ANDROID','wrong actual mobile Vulkan platform')
    expected=dict(EXPECTED_CVARS,**{'r.Mobile.ForceDepthResolve':1})
    check(p['cvars']==expected and p['oldFogPasses']==0,'wrong effective path/fog/depth configuration')
    check(p['mobilePrePostCallbacks']>0 and p['producerCallbacks']>=180 and p['observerCallbacks']>=180,'missing actual mobile fog callbacks')
    check(p['producerCallback']=='Mobile BeforeDOF.Producer' and p['observerCallback']=='Mobile BeforeDOF.Observer','wrong engine delegate route')
    check(p['pixelSelection']==PROFILE['pixelSelection'] and p['steps']==64 and p['earlyOut']==0 and p['sourceJ']==[.35,.6,.9] and p['sphereCentreCm']==[800,0,0] and p['sphereRadiusCm']==400,'wrong model/sample rule')
    check(p['fixture']['name']=='MobileFogPlanesV1' and p['fixture']['nearCm']==r['nearCm'] and p['fixture']['farCm']==1200 and p['fixture']['blendMode']==0 and p['fixture']['shadingModels']==1,'wrong opaque/unlit fog fixture')
    check(p['viewMin']==[0,0] and p['viewSize']==p['surfaceSize']==[1552,720] and p['viewSize']==last['unscaledViewSize'],'wrong actual fog view')
    depth,color=p['sceneDepth'],p['sceneColorInput']
    check(depth['valid'] and depth['format'].removeprefix('PF_')=='DepthStencil' and depth['platformFormat']==129 and depth['samples']==1 and not depth['memoryless'] and depth['extent']==p['viewSize'],'invalid preserved native depth')
    check(color['valid'] and color['format'].removeprefix('PF_')=='FloatR11G11B10' and color['platformFormat']==122 and color['samples']==1 and color['extent']==p['viewSize'],'wrong actual HDR input')
    result=None
    if r['mode']=='Off':
        check(p['status']=='control-complete' and [p[k] for k in ('integrateDispatches','compositePasses','sampleDispatches','readbackRequests')]==[0,0,0,0] and not p['gpuCompletionConfirmed'] and not (out/'gpu-buffer.stdout').exists() and 'stTexture' not in p and 'returnedOutput' not in p,'Off performed fog work')
    else: result=numeric(p,r,out)
    engine=(out/'engine.stdout').read_text(encoding='utf-8',errors='replace')
    check(r['runId'] in engine and not re.search(r'Fatal error|Assertion failed|Log(?:Material|Shader)[^\n]*: Error:|LogTemp: Error:.*Mobile fog',engine,re.I),'engine/scene/shader/fog failure')
    live=audit(Path(r['candidate']['path']).parent)
    check('LevelColorationUnlitMaterial.uasset' in (Path(r['candidate']['path']).parent/'pak-list.txt').read_text(encoding='utf-8'),'opaque unlit material not cooked')
    return {'kind':'UEFogLab.MobileUniformFogAudit','schemaVersion':1,'passed':True,'modelKey':MODEL,'run':bind(out/'run.json'),'runId':r['runId'],'mode':r['mode'],'candidate':r['candidate'],'apk':m['package']['apk'],'fog':bind(out/'fog.stdout'),'numerical':result,'actualPath':p,'currentCookVerification':live['currentArtifactVerification'],
            'checks':['exact new fog cooked/installed candidate','fresh UUID/PID/kernel/foreground/survival/cleanup','actual Vulkan ES3_1 Forward HDR/MSAA1/preserved depth','Off zero fog work' if r['mode']=='Off' else 'full-view ST -> actual RGBA16F PS return -> engine observer with fresh GPU bytes','25-point geometry/ST/quantized composition' if result else 'CPU control record'],
            'limits':['only MEIZU21 UE5.8.4 fixed Vulkan Forward HDR route','numerical coverage25 frozen points, not whole-image acceptance','oldFogPasses0 is structural, not a measured GPU count','no reconstruction/tiers/Manual/shadows/temporal/performance']}


def negative(a):
    source=Path(a.input).resolve(); valid=validate(source); original=read(source/'run.json')
    check(original['mode']=='Full' and original['extinctionPerMetre']>0 and valid['numerical']['activeSamples']>=3,'negatives require nonzero real Full with active contribution')
    out=new_output(a.output); cases=[]
    expected={'wrong-runid':'wrong runId/candidate','wrong-gpu-frame':'wrong GPU runId/frame marker','missing-completion':'missing GPU completion',
              'missing-bytes':'missing/invalid mobile fog bytes','desktop-sm5':'wrong actual mobile Vulkan','gles':'wrong actual mobile Vulkan',
              'wrong-exposure':'exposure mismatch','vacuum-st':'optical S/T mismatch','uncomposited-output':'returned HDR composition mismatch',
              'missing-cm-to-m':'optical S/T mismatch','wrong-consumer-resource':'returned resource not consumed'}
    for name in PROFILE['negativeCases']:
        dest=out/name;dest.mkdir();r=copy.deepcopy(original)
        for f in source.iterdir():
            if f.suffix in ('.stdout','.stderr') or f.name=='commands.json':shutil.copyfile(f,dest/f.name)
        p=read(dest/'fog.stdout'); raw=dest/'gpu-buffer.stdout'
        if name=='wrong-runid':p['runId']=uuid.uuid4().hex
        elif name=='missing-completion':p['gpuCompletionConfirmed']=False
        elif name=='missing-bytes':raw.write_bytes(b'')
        elif name=='desktop-sm5':p.update(featureLevel='SM5',shaderFormat='SF_VULKAN_SM5')
        elif name=='gles':p.update(rhi='OpenGL',shaderFormat='GLSL_ES3_1_ANDROID')
        elif name=='wrong-exposure':p['preExposure']=1 if p['preExposure']==2 else 2
        elif name=='wrong-consumer-resource':p['returnedResourceEqualsObserverInput']=False
        else:
            data=np.fromfile(raw,dtype='<f4').reshape(102,4)
            if name=='wrong-gpu-frame':data[0,2]+=1
            elif name=='vacuum-st':data[3::4]=[0,0,0,1]
            elif name=='uncomposited-output':
                data[5::4]=np.asarray([[half(float(x)) for x in row] for row in data[4::4]],dtype='<f4');data[5::4,3]=1
            else:
                for i,uv in enumerate(PROFILE['sampleUV']):
                    unused,d=ray(uv,p['viewSize'],p['clipToWorld'],p['projectionSignY'])
                    z=float(data[2+4*i,0]);distance=decode_depth(z,d,p['worldToClip']) if z else None
                    wrong=tuple(half(x) for x in optics(medium_length(d,distance)*100,p['extinctionPerMetre']))
                    data[3+4*i]=wrong
                    data[5+4*i]=[*[half(x) for x in compose(wrong,data[4+4*i],p['preExposure'])],1]
            data.tofile(raw)
        write(dest/'fog.stdout',p)
        r['installation']['packageManager']=bind(dest/'installed-pm.stdout')
        r['artifacts']=[bind(dest/Path(b['path']).name) if (dest/Path(b['path']).name).exists() else b for b in r['artifacts']]
        write(dest/'run.json',r)
        proc=subprocess.run([sys.executable,str(Path(__file__)),'validate','--input',str(dest),'--output',str(dest/'cli.json')],capture_output=True,timeout=180)
        (dest/'cli.stdout').write_bytes(proc.stdout);(dest/'cli.stderr').write_bytes(proc.stderr)
        result=read(dest/'cli.json')
        check(proc.returncode==2 and result['passed'] is False and expected[name] in ' '.join(result['errors']),'wrong semantic rejection: '+name)
        cases.append({'case':name,'exitCode':proc.returncode,'errors':result['errors'],'result':bind(dest/'cli.json')})
    write(out/'negative.json',{'kind':'UEFogLab.MobileUniformFogNegative','schemaVersion':1,'passed':True,'sourceRunId':original['runId'],'cases':cases})
    print('Eleven real-source hash-synchronized fog semantic negatives rejected',flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__);subs=parser.add_subparsers(dest='action',required=True)
    run=subs.add_parser('run');run.add_argument('--candidate',required=True);run.add_argument('--previous-candidate',default='')
    run.add_argument('--serial',default='');run.add_argument('--mode',choices=['Off','Full'],required=True)
    run.add_argument('--near',type=int,choices=[400,600],default=400);run.add_argument('--extinction',type=float,choices=[0,.35,.70],default=.35);run.add_argument('--exposure',type=int,choices=[1,2],default=1)
    for name in ('validate','negative'):
        p=subs.add_parser(name);p.add_argument('--input',required=True)
    for p in subs.choices.values():p.add_argument('--output',required=True)
    a=parser.parse_args()
    try:
        if a.action=='run':collect(a)
        elif a.action=='negative':negative(a)
        else:
            check(allowed(a.output) and not Path(a.output).exists(),'require new ignored validation output')
            write(a.output,validate(a.input));print('Mobile Uniform fog audit passed',flush=True)
        return 0
    except Exception as e:
        if a.action=='validate' and allowed(a.output) and not Path(a.output).exists():write(a.output,{'kind':'UEFogLab.MobileUniformFogAudit','passed':False,'errors':[str(e)]})
        print('REJECTED: '+str(e),file=sys.stderr);return 2

if __name__=='__main__':sys.exit(main())
