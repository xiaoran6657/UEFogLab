"""M4 isolated mobile reconstruction capture/audit. Never installs negatives.
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

import mobile_reconstruction_reference as reference
from mobile_reconstruction_reference import PROFILE, MODEL, analyze as analyze_dense

PROTOCOL = {'version':1,'profileRevision':2,'readyTimeout':180,'observeSeconds':30,'minimumFrames':120,'beforeDofIndex':180,'strideBytes':16,'bufferCeilingBytes':134217728}

def collect(a):
    out = new_output(a.output)
    check(not git('status', '--porcelain'), 'Commit fog source/protocol/tool before capture')
    candidate = Path(a.candidate).resolve(); cooked = audit(candidate); m = read(candidate/'package.json'); check_candidate(candidate,m)
    old_candidate = audit(a.previous_candidate) if a.previous_candidate else None
    c = Commands(out, select(a.serial)); run_id = uuid.uuid4().hex
    r = {'kind': 'UEFogLab.MobileReconstructionRun', 'schemaVersion': 1, 'protocol': PROTOCOL,
         'runId': run_id, 'profileName': a.profile, 'fixtureName': a.fixture, 'grid': 'Full' if a.profile=='Full64' else a.grid, 'nearCm': a.near, 'extinctionPerMetre': a.extinction, 'modelKey': MODEL, 'preExposure': a.exposure,
         'candidate': bind(candidate/'package.json'), 'toolRevision': git('rev-parse', 'HEAD'),
         'tool': bind(__file__), 'configuration': bind(candidate/'source-inputs/tools/mobile_reconstruction_profile.json'), 'package': PKG, 'serial': c.serial, 'startedUtc': utc(),
         'completed': False, 'cleanupConfirmed': False}
    write(out/'run.json', r); owned = False; remote = None
    try:
        r['device'] = device_info(c)
        check(r['device']['model']=='MEIZU 21','Unassigned device model')
        unlocked(c)
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
        diagnostic = 0 if a.profile == 'Off' else 1
        command += f' -FogLabMobileRecon={a.profile} -FogLabReconFixture={a.fixture} -FogLabReconGrid={a.grid} -FogLabReconNear={a.near} -FogLabReconSigma={a.extinction} -FogLabReconReadback={diagnostic} -FogLabPreExposure={a.exposure}'
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
                foreground(c, 'ready-foreground'); print('Mobile reconstruction '+a.profile+' scene ready; observing frozen startup window', flush=True)
            if any(e.get('event') == 'capture-complete' and e.get('runId') == run_id for e in ev): break
            check(time.monotonic()-start < 180 if ready is None else time.monotonic()-ready < 120, 'Frozen startup/probe timeout')
            time.sleep(2)
        else: raise ValueError('No completed capture')
        for name, filename in [('events','events.jsonl'), ('frame','frame.png'), ('engine','engine.log'), ('reconstruction','reconstruction.json')]:
            c.own_file(name, remote+'/'+filename)
        if a.profile != 'Off': c.own_file('gpu-buffer', remote+'/reconstruction-buffer.bin')
        foreground(c, 'capture-foreground')
        check(pid in c.shell('final-pid', 'pidof', PKG).split(), 'Main process died or restarted')
        c.shell('final-process-stat', 'run-as', PKG, 'cat', '/proc/'+pid+'/stat')
        r['deviceEpochEnd'] = c.shell('device-end-time', 'date', '+%s.%N')
        c.run('logcat', ['logcat', '-d', '--pid='+pid, '-v', 'epoch', '-T', r['deviceEpochStart']])
        r.update(completed=True, finishedUtc=utc())
    except Exception as error:
        r.update(error=str(error), finishedUtc=utc())
        if remote:
            for name, filename in [('failure-events','events.jsonl'), ('failure-engine','engine.log'), ('failure-reconstruction','reconstruction.json')]:
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
    print('Mobile reconstruction '+a.profile+' passed: '+run_id, flush=True)


def bound_required(r, out, names):
    paths = [Path(b['path']).resolve() for b in r['artifacts']]
    check(len(paths) == len(set(paths)) and all(out/n in paths for n in names), 'Missing/duplicate bound raw probe evidence')
    for b in r['artifacts']: verify(b)



def validate(directory):
    out=Path(directory).resolve(); r=read(out/'run.json')
    check(r['kind']=='UEFogLab.MobileReconstructionRun' and r['schemaVersion']==1 and r['protocol']==PROTOCOL, 'Wrong probe run schema/protocol')
    check(r['package']==PKG and re.fullmatch('[0-9a-f]{32}',r['runId']) and r['profileName'] in PROFILE['profileCodes'], 'Wrong probe package/mode/runId')
    required={'commands.json','events.stdout','frame.stdout','engine.stdout','reconstruction.stdout','installed-pm.stdout','installed-paths.stdout',
              'installed-signature.stdout','process-cmdline.stdout','process-stat.stdout','final-process-stat.stdout','pid.stdout','final-pid.stdout',
              'ready-foreground.stdout','capture-foreground.stdout','cleanup-pid.stdout','cold-pid.stdout','device-start-time.stdout','device-end-time.stdout','logcat.stdout','start.stdout','install.stdout'}
    if r['profileName']!='Off': required.add('gpu-buffer.stdout')
    bound_required(r,out,required); verify(r['candidate']); m=read(r['candidate']['path']); check_candidate(Path(r['candidate']['path']).parent,m); verify(r['configuration']); check(r['modelKey']==MODEL,'wrong run model')
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
    for name in ('install','start','process-stat','final-process-stat','cleanup-stop','events','frame','reconstruction','engine','logcat'):
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
    check(ready['map']=='/Engine/Maps/Entry' and ready['gameMode']=='/Script/FogLabDemo.FogLabGameMode' and ready['cube']=='/Engine/BasicShapes/Cube.Cube' and ready['staticMeshActors']==(3 if r['fixtureName']=='ThinStripe' else 2),'Wrong known fixture scene/assets')
    last=capture['lastObservedView']; check(last['runId']==r['runId'] and last['renderedViewFamilies']>=120,'No actual game-view frame advancement')
    with Image.open(out/'frame.stdout') as im:
        im.load(); check(im.format=='PNG' and list(im.size)==[capture['width'],capture['height']] and min(im.size)>=360,'Invalid actual app PNG')

    p=read(out/'reconstruction.stdout')
    check(p.get('kind')=='UEFogLab.MobileReconstruction' and p.get('schemaVersion')==1 and p.get('modelKey')==MODEL,'wrong reconstruction schema/model')
    check(not p.get('cpuSynthetic',False),'CPU synthetic cannot establish DeviceRun')
    check(p['runId']==r['runId'] and p['pid']==r['pid'] and p['buildId']==m['buildId'] and p['sourceRevision']==m['sourceRevision'],'wrong runId/candidate reconstruction witness')
    check(p['profile']==r['profileName'] and p['fixture']==r['fixtureName'] and p['grid']==r['grid'] and p['nearCm']==r['nearCm'] and p['extinctionPerMetre']==r['extinctionPerMetre'],'wrong reconstruction profile/parameters')
    check(p['preExposure']==r['preExposure'],'actual exposure mismatch')
    check(p['rhi']=='Vulkan' and p['featureLevel']=='ES3_1' and p['shaderFormat']=='SF_VULKAN_ES31_ANDROID','wrong actual mobile Vulkan platform')
    check(p['cvars']==dict(EXPECTED_CVARS,**{'r.Mobile.ForceDepthResolve':1}) and p['oldFogPasses']==0,'wrong effective path/fog/depth configuration')
    check(p['mobilePrePostCallbacks']>0 and p['producerCallbacks']>=180 and p['observerCallbacks']>=180,'missing actual reconstruction callbacks')
    check(p['producerCallback']=='Mobile BeforeDOF.ReconProducer' and p['observerCallback']=='Mobile BeforeDOF.ReconObserver','wrong engine delegate route')
    check(p['steps']==64 and p['earlyOut']==0 and p['sourceJ']==[.35,.6,.9] and p['sphereCentreCm']==[800,0,0] and p['sphereRadiusCm']==400,'wrong Uniform reconstruction model')
    check(p['pixelSelection']=='min(V-1, ((2*f+1)*V)//(2*F)); nominal UV=(f+.5)/F' and p['guideUnits']=='view-axis cm; sky0; R32_FLOAT','wrong coordinate/guide definition')
    fixture=p['fixtureDescription']
    check(fixture['name']==('MobileReconThinStripeV1' if r['fixtureName']=='ThinStripe' else 'MobileReconPlanesV1') and fixture['nearCm']==r['nearCm'] and fixture['farCm']==1200 and fixture['meshActors']==ready['staticMeshActors'] and fixture['blendMode']==0 and fixture['shadingModels']==1,'wrong actual opaque reconstruction fixture')
    if r['fixtureName']=='ThinStripe':
        t=PROFILE['thinStripe']
        check(max(abs(x-y) for x,y in zip(fixture['stripeActorLocationCm'],t['centreCm']))<1e-6 and max(abs(x-y) for x,y in zip(fixture['stripeActorScale'],t['scale']))<1e-6,'frozen stripe world transform changed')
    check(p['viewMin']==[0,0] and p['viewSize']==p['surfaceSize']==[1552,720] and p['viewSize']==last['unscaledViewSize'],'wrong actual reconstruction view')
    texture(p,'sceneDepth','DepthStencil',129,p['viewSize'])
    texture(p,'sceneColorInput','FloatR11G11B10',122,p['viewSize'])
    numerical=numeric(p,r,out)
    engine=(out/'engine.stdout').read_text(encoding='utf-8',errors='replace')
    check(r['runId'] in engine and not re.search(r'Fatal error|Assertion failed|Log(?:Material|Shader)[^\n]*: Error:|LogTemp: Error:.*(?:Mobile reconstruction|Recon)',engine,re.I),'engine/scene/shader/reconstruction failure')
    current=audit(Path(r['candidate']['path']).parent)
    check('LevelColorationUnlitMaterial.uasset' in (Path(r['candidate']['path']).parent/'pak-list.txt').read_text(encoding='utf-8'),'opaque unlit material not cooked')
    return dict(kind='UEFogLab.MobileReconstructionAudit',schemaVersion=1,passed=True,run=bind(out/'run.json'),runId=r['runId'],
                profile=r['profileName'],fixture=r['fixtureName'],candidate=r['candidate'],apk=m['package']['apk'],metadata=bind(out/'reconstruction.stdout'),
                numerical=numerical,actualPath=p,currentCookVerification=current['currentArtifactVerification'],
                runtimeGpuEvidenceConfirmed=r['profileName']!='Off',
                checks=['exact new cooked/installed source and signer','fresh UUID/PID/kernel/foreground/survival/cleanup',
                        'actual Vulkan ES3_1 Forward HDR/preserved depth','actual R32 write/SRV read' if r['profileName']!='Off' else 'Off zero custom work/resources',
                        'actual low ST -> reconstructed ST -> returned HDR -> engine consumer' if r['profileName'] in ('Full64','HalfB64','HalfG64') else 'control'],
                limits=['fixed MEIZU21/UE5.8.4 route, ViewMin0','conditional optics is not strict all-pixel geometry',
                        'Odd is fog-grid diagnostic, not odd scene','no formal performance or GPU RNE-mode claim'])


def unlocked(c):
    # Only persist lock/screen state, not unrelated activities or app contents.
    text=c.run('unlock-preflight',['shell',"dumpsys window policy | grep -E 'showing=|screenState=|inputRestricted='"])
    lines=[s.strip() for s in text.splitlines()]
    check('showing=false' in lines and 'screenState=SCREEN_STATE_ON' in lines,'Phone locked; manual unlock required before formal launch')


def check_candidate(candidate,m):
    required={'Plugins/UEFog/Shaders/Private/UEFogMobileReconstruction.usf','Plugins/UEFog/Source/UEFog/Private/UEFogMobileReconstruction.cpp',
              'Source/FogLabDemo/FogLabMobileReconstructionFixture.cpp','tools/mobile_reconstruction_profile.json','tools/mobile_reconstruction_reference.py','tools/android_recon.py'}
    names={Path(x['path']).relative_to(ROOT).as_posix() for x in m['sourceInputs']}
    check(required<=names,'candidate lacks actual new reconstruction shader/source/collector; old Full APK cannot prove Half')
    check(read(candidate/'source-inputs/tools/mobile_reconstruction_profile.json')==PROFILE,'frozen reconstruction profile changed')


def texture(p,name,fmt,vk,size,usage=0):
    d=p[name]
    check(d['valid'] and d['format'].removeprefix('PF_')==fmt and d['platformFormat']==vk and d['samples']==1 and d['extent']==size and not d['memoryless'],'wrong actual texture format/extent: '+name)
    if usage:check(d['flags']&usage,'wrong actual texture usage: '+name)
    return d


def numeric(p,r,out):
    control=p['profile'] in ('Off','GuideProbe')
    fog=p['viewSize'] if p['profile']=='Full64' else PROFILE['grids']['Odd' if p['grid']=='Odd' else 'Half']
    check(p['fogSize']==fog,'scene/fog dimensions confused')
    check(p['allocatedFogSize']==([0,0] if control else fog) and p['allocatedReconstructionSize']==([0,0] if control else p['viewSize']),'wrong configured versus allocated resource sizes')
    keys=('integrateDispatches','reconstructDispatches','compositePasses','sampleDispatches','readbackRequests','probeWriteDispatches','probeReadDispatches')
    if p['profile']=='Off':
        check(p['status']=='control-complete' and all(p[k]==0 for k in keys) and not p['gpuCompletionConfirmed'],'Off performed custom GPU work')
        check(not (out/'gpu-buffer.stdout').exists() and all(k not in p for k in ('probeTexture','lowST','lowGuide','reconstructedST','returnedOutput')),'Off allocated custom resources/readback')
        return None # no fabricated zero-byte device readback; runtime control only
    check(p['status']=='complete' and p['gpuCompletionConfirmed'],'missing GPU completion/readback')
    check(p['completionFrame']>p['frame']>0 and p['observerFrame']==p['frame'],'wrong capture/observer/completion frame')
    check(p['bufferStride']==16 and p['dtype']=='<f4','wrong dense buffer representation')
    expected=reference.budget(p['profile'],p['viewSize'],p['fogSize'])
    check(p['bufferBytes']==expected and (out/'gpu-buffer.stdout').stat().st_size==expected,'missing/invalid dense bytes')
    check(r['device']['physicalDevices'][0]['limits']['maxStorageBufferRange']>=expected,'dense buffer exceeds actual device storage range')
    texture(p,'probeTexture','R32_FLOAT',100,[3,1],131072)
    if p['profile']=='GuideProbe':
        check([p[k] for k in keys]==[0,0,0,1,1,1,1] and all(k not in p for k in ('lowST','lowGuide','reconstructedST','returnedOutput')),'GuideProbe allocated fog/reconstruction/HDR resources')
    else:
        check([p[k] for k in keys]==[1,0 if p['profile']=='Full64' else 1,1,1,1,1,1],'wrong reconstruction work schedule')
        texture(p,'lowST','FloatRGBA',97,fog,131072);texture(p,'lowGuide','R32_FLOAT',100,fog,131072)
        texture(p,'reconstructedST','FloatRGBA',97,p['viewSize'],131072);texture(p,'returnedOutput','FloatRGBA',97,p['viewSize'],1)
        texture(p,'observerInput','FloatRGBA',97,p['viewSize'],1)
        check(p['returnedResourceEqualsObserverInput'] and p['observerInput']==p['returnedOutput'],'returned resource not consumed by engine observer')
        check(not p['overrideOutputActive'],'unfrozen terminal override path')
        check(p['fullIdentityResource']==(p['profile']=='Full64'),'wrong actual Full identity/resource chain')
    result=analyze_dense(p,out/'gpu-buffer.stdout')
    if p['profile'] not in ('Off','GuideProbe'):
        unused,low,scene=reference.parse(p,out/'gpu-buffer.stdout')
        geometry=scene[:,:,0,0]>0;cin=scene[:,:,2,:3]
        expected=np.asarray([.25,.5,2])*p['preExposure']
        check(np.any(geometry) and np.max(np.abs(cin[geometry]-expected))<=.01*max(expected) and np.max(np.abs(cin[~geometry]))<=.001,'actual linear HDR input/exposure mismatch')
        if p['fixture']=='ThinStripe':
            depth=scene[300:420,1000,0,1]
            check(len(depth)==120 and np.all(np.abs(depth-600)<=1),'frozen stripe Full/query footprint missing')
            check(np.all(np.abs(scene[300:420,999,0,1]-1200)<=1) and np.all(np.abs(scene[300:420,1001,0,1]-1200)<=1),'frozen stripe neighboring far depth changed')
            hit=(low[:,:,1,2]==1000)&(low[:,:,1,3]>=300)&(low[:,:,1,3]<420)
            if p['profile']=='Full64':check(np.count_nonzero(hit)==120,'dense Full stripe target footprint empty')
            elif p['grid']=='Half':
                check(np.count_nonzero(hit)==0,'stripe was not missed by actual low canonical depth')
                if p['profile']=='HalfG64':
                    check(np.all(scene[300:420,1000,0,2]==0) and np.all(scene[300:420,1000,0,3]>=0) and scene[350,1000,0,3]==3,'frozen stripe zero-compatible/fallback witness failed')
            result['actualThinStripeTargetPixels']=120
    return result


def quality_runs(a):
    source=Path(a.input).resolve();ref=Path(a.reference).resolve()
    p=read(source/'reconstruction.stdout');q=read(ref/'reconstruction.stdout')
    check(p['profile'] in PROFILE['quality']['supportedProfiles'],'unsupported quality profile: '+p['profile'])
    current_audit,full_audit=validate(source),validate(ref)
    result=reference.quality(p,source/'gpu-buffer.stdout',q,ref/'gpu-buffer.stdout')
    result.update(kind='UEFogLab.MobileReconstructionQuality',schemaVersion=1,actualRuntimeAuditsPassed=True,
                  currentRun=current_audit['run'],referenceRun=full_audit['run'],currentMetadata=bind(source/'reconstruction.stdout'),referenceMetadata=bind(ref/'reconstruction.stdout'))
    write(a.output,result)
    print('Actual runtime quality '+('passed' if result['passed'] else 'budget failed; all metrics preserved'),flush=True)
    return 0 if result['passed'] else 2


def weight_sum(low_guide,query):
    vh,vw=query.shape;fh,fw=low_guide.shape
    x0,x1,fx=reference.taps_axis(vw,fw);y0,y1,fy=reference.taps_axis(vh,fh)
    total=np.zeros(query.shape,dtype=np.float64)
    for x,y,spatial in [(x0,y0,(1-fy[:,None])*(1-fx[None,:])),(x1,y0,(1-fy[:,None])*fx[None,:]),
                        (x0,y1,fy[:,None]*(1-fx[None,:])),(x1,y1,fy[:,None]*fx[None,:])]:
        weights,unused=reference.depth_weights(low_guide[y[:,None],x[None,:]],query)
        total+=spatial*weights
    return total


def negative(a):
    source=Path(a.input).resolve();full=Path(a.reference).resolve()
    valid=validate(source);full_audit=validate(full);original=read(source/'run.json');p0=read(source/'reconstruction.stdout');q=read(full/'reconstruction.stdout')
    check(p0['profile']=='HalfG64' and p0['fixture']=='ThinStripe' and p0['grid']=='Half' and p0['extinctionPerMetre']>0 and valid['numerical']['activeLowSamples']>=3,'nineteen negatives require actual nonzero ThinStripe HalfG source')
    check(q['profile']=='Full64','low-source substitution requires actual Full64 reference')
    for name in ('viewSize','fixture','nearCm','extinctionPerMetre','preExposure','worldToClip','clipToWorld','sourceRevision','buildId'):
        check(p0[name]==q[name],'negative source/reference pair mismatch: '+name)
    rows,low,scene=reference.parse(p0,source/'gpu-buffer.stdout');unused,full_low,full_scene=reference.parse(q,full/'gpu-buffer.stdout')
    check(np.array_equal(scene[:,:,0,:2],full_scene[:,:,0,:2]) and np.array_equal(scene[:,:,2],full_scene[:,:,2]),'negative actual Full input/depth mismatch')
    f=p0['fogSize'];v=p0['viewSize'];nf=math.prod(f)
    expected_low,unused=reference.analytic_st(f,reference.decode_axis(low[:,:,1,0],p0['worldToClip']),p0['extinctionPerMetre'],p0['clipToWorld'])
    expected_rec,compatible,fallback=reference.reconstruct(low[:,:,0],low[:,:,1,1],scene[:,:,0,1],True)
    bilinear,unused,unused=reference.reconstruct(low[:,:,0],low[:,:,1,1],scene[:,:,0,1],False)
    sums=weight_sum(low[:,:,1,1],scene[:,:,0,1])
    out=new_output(a.output);cases=[]
    expected={'wrong-runid':'wrong runId/candidate reconstruction witness','wrong-gpu-frame':'wrong GPU runId/frame marker',
              'missing-completion':'missing GPU completion','missing-bytes':'missing/invalid dense bytes','desktop-sm5':'wrong actual mobile Vulkan',
              'gles':'wrong actual mobile Vulkan','wrong-exposure':'actual exposure mismatch','vacuum-st':'low-grid optical S/T mismatch',
              'uncomposited-output':'actual returned HDR composition mismatch','missing-cm-to-m':'low-grid optical S/T mismatch',
              'wrong-consumer-resource':'returned resource not consumed','scene-fog-confusion':'scene/fog dimensions confused',
              'canonical-neighbor':'canonical depth pixel mismatch','guide-metres':'guide view-axis centimetre mismatch',
              'guide-sky-class':'guide sky classification mismatch','substituted-low-ST':'low-grid optical S/T mismatch',
              'bilinear-as-guided':'actual low-input reconstruction mismatch','unnormalized-guided':'actual low-input reconstruction mismatch',
              'wrong-fallback':'reconstruction compatibility/fallback mismatch'}
    for name in PROFILE['oldSemanticCases']+PROFILE['newSemanticCases']:
        dest=out/name;dest.mkdir();r=copy.deepcopy(original);p=copy.deepcopy(p0);data=np.array(rows,dtype='<f4',copy=True)
        lo=data[4:4+2*nf].reshape(f[1],f[0],2,4);sc=data[4+2*nf:].reshape(v[1],v[0],4,4)
        witness={};coherent_hdr=False
        for file in source.iterdir():
            if (file.suffix in ('.stdout','.stderr') or file.name=='commands.json') and file.name!='gpu-buffer.stdout':shutil.copyfile(file,dest/file.name)
        if name=='wrong-runid':p['runId']=uuid.uuid4().hex
        elif name=='wrong-gpu-frame':data[0,2]+=1
        elif name=='missing-completion':p['gpuCompletionConfirmed']=False
        elif name=='desktop-sm5':p.update(featureLevel='SM5',shaderFormat='SF_VULKAN_SM5')
        elif name=='gles':p.update(rhi='OpenGL',shaderFormat='GLSL_ES3_1_ANDROID')
        elif name=='wrong-exposure':p['preExposure']=1 if p['preExposure']==2 else 2
        elif name=='wrong-consumer-resource':p['returnedResourceEqualsObserverInput']=False
        elif name=='scene-fog-confusion':p['fogSize']=p['viewSize'][:];data[2,2:4]=p['viewSize']
        elif name=='vacuum-st':lo[:,:,0]=[0,0,0,1]
        elif name=='missing-cm-to-m':
            wrong,unused=reference.analytic_st(f,reference.decode_axis(lo[:,:,1,0],p0['worldToClip']),p0['extinctionPerMetre']*100,p0['clipToWorld'])
            lo[:,:,0]=reference.half_store(wrong)
        elif name=='uncomposited-output':
            sc[:,:,3]=reference.half_store(sc[:,:,2]);sc[:,:,3,3]=1
        elif name=='canonical-neighbor':
            y,x=(174,499);px,py=map(int,lo[y,x,1,2:4]);check((px,py)==(999,349),'frozen canonical neighbor witness moved')
            wrong_z=float(scene[py,px+1,0,0]);wrong_axis=float(scene[py,px+1,0,1])
            check(wrong_z!=float(lo[y,x,1,0]),'canonical wrong-pixel source has no actual depth difference')
            witness=dict(low=[x,y],correctPixel=[px,py],wrongPixel=[px+1,py],oldNative=float(lo[y,x,1,0]),wrongNative=wrong_z)
            lo[y,x,1]=[wrong_z,wrong_axis,px+1,py]
            wrong,unused=reference.analytic_st(f,reference.decode_axis(lo[:,:,1,0],p0['worldToClip']),p0['extinctionPerMetre'],p0['clipToWorld'])
            lo[y,x,0]=reference.half_store(wrong[y,x])
        elif name=='guide-metres':lo[:,:,1,1]/=100
        elif name=='guide-sky-class':
            y,x=np.argwhere((lo[:,:,1,0]==0)&(lo[:,:,0,3]<.999))[0];witness=dict(low=[int(x),int(y)],originalGuide=0,wrongGuide=600)
            lo[y,x,1,1]=600
        elif name=='substituted-low-ST':
            lo[:,:,0]=full_low[:,:,0].reshape(-1,4)[:nf].reshape(f[1],f[0],4)
        elif name=='bilinear-as-guided':sc[:,:,1]=reference.half_store(bilinear);coherent_hdr=True
        elif name=='unnormalized-guided':
            wrong=np.where((sums>reference.EPS)[...,None],expected_rec*sums[...,None],expected_rec)
            sc[:,:,1]=reference.half_store(wrong);coherent_hdr=True
        elif name=='wrong-fallback':
            y,x=350,1000;check(sc[y,x,0,2]==0 and sc[y,x,0,3]==3 and sc[y,x,1,3]<.999,'actual nonzero frozen fallback source missing')
            witness=dict(pixel=[x,y],originalFallback=3,wrongFallback=-1,originalST=sc[y,x,1].tolist())
            sc[y,x,0,3]=-1;sc[y,x,1]=[0,0,0,1];coherent_hdr=True
        if coherent_hdr:
            values=sc[:,:,1].astype(np.float64)
            sc[:,:,3,:3]=reference.half_store(values[:,:,:3]*p0['preExposure']+values[:,:,3,None]*sc[:,:,2,:3]);sc[:,:,3,3]=1
        if name in ('vacuum-st','missing-cm-to-m','substituted-low-ST'):
            errors=np.max(np.abs(lo[:,:,0]-expected_low),axis=2);mask=(expected_low[:,:,3]<.999)&(errors>.001)
            check(np.any(mask),'low numeric negative has no nontrivial actual-source witness: '+name)
            y,x=np.argwhere(mask)[0];witness=dict(low=[int(x),int(y)],absoluteSTError=float(errors[y,x]),violatingActiveCells=int(mask.sum()))
        if name in ('bilinear-as-guided','unnormalized-guided'):
            ratio=np.max(np.abs(sc[:,:,1]-expected_rec)/reference.storage_bound(expected_rec),axis=2)
            mask=(expected_rec[:,:,3]<.999)&(ratio>1)
            if name=='unnormalized-guided':mask &= (compatible>0)&(compatible<4)&(sums>reference.EPS)
            check(np.any(mask),'reconstruction negative has no actual-source witness: '+name)
            y,x=np.argwhere(mask)[0];witness=dict(pixel=[int(x),int(y)],errorToOwnBound=float(ratio[y,x]),guideWeightSum=float(sums[y,x]))
        if name=='uncomposited-output':
            hdr=scene[:,:,1,:3].astype(np.float64)*p0['preExposure']+scene[:,:,1,3,None].astype(np.float64)*scene[:,:,2,:3]
            ratio=np.max(np.abs(sc[:,:,3,:3]-hdr)/reference.storage_bound(hdr),axis=2)
            check(np.any((scene[:,:,1,3]<.999)&(ratio>1)),'uncomposited output lacks active witness')
            y,x=np.argwhere((scene[:,:,1,3]<.999)&(ratio>1))[0];witness=dict(pixel=[int(x),int(y)],errorToOwnBound=float(ratio[y,x]))
        raw=dest/'gpu-buffer.stdout'
        if name=='missing-bytes':raw.write_bytes(b'')
        else:data.tofile(raw)
        write(dest/'reconstruction.stdout',p)
        commands=read(dest/'commands.json')
        for step in commands:
            for key in ('stdout','stderr'):
                local=dest/Path(step[key]['path']).name
                if local.exists():step[key]=bind(local)
        write(dest/'commands.json',commands)
        r['installation']['packageManager']=bind(dest/'installed-pm.stdout')
        r['artifacts']=[bind(dest/Path(b['path']).name) if (dest/Path(b['path']).name).exists() else b for b in r['artifacts']]
        write(dest/'run.json',r)
        argv=[sys.executable,str(Path(__file__).resolve()),'validate','--input',str(dest),'--output',str(dest/'cli.json')]
        proc=subprocess.run(argv,capture_output=True,timeout=180)
        (dest/'cli.stdout').write_bytes(proc.stdout);(dest/'cli.stderr').write_bytes(proc.stderr)
        result=read(dest/'cli.json')
        check(proc.returncode==2 and result['passed'] is False and expected[name] in ' '.join(result['errors']),'wrong semantic rejection: '+name)
        cases.append(dict(case=name,exitCode=proc.returncode,errors=result['errors'],witness=witness,result=bind(dest/'cli.json'),argv=argv))
        del data
    write(out/'negative.json',dict(kind='UEFogLab.MobileReconstructionNegative',schemaVersion=1,passed=True,hostMutationsOnly=True,
                                 sourceRunId=original['runId'],sourceAudit=valid['run'],referenceAudit=full_audit['run'],cases=cases))
    print('Nineteen real-source hash-synchronized reconstruction semantic negatives rejected',flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__);subs=parser.add_subparsers(dest='action',required=True)
    run=subs.add_parser('run');run.add_argument('--candidate',required=True);run.add_argument('--previous-candidate',default='');run.add_argument('--serial',default='')
    run.add_argument('--profile',choices=list(PROFILE['profileCodes']),required=True);run.add_argument('--fixture',choices=['Planes','ThinStripe'],default='Planes')
    run.add_argument('--grid',choices=['Half','Odd'],default='Half');run.add_argument('--near',type=int,choices=[400,600],default=600)
    run.add_argument('--extinction',type=float,choices=[0,.35,.70],default=.35);run.add_argument('--exposure',type=int,choices=[1,2],default=1)
    for name in ('validate','negative','quality'):
        p=subs.add_parser(name);p.add_argument('--input',required=True)
        if name in ('negative','quality'):p.add_argument('--reference',required=True)
    for p in subs.choices.values():p.add_argument('--output',required=True)
    a=parser.parse_args()
    try:
        if a.action=='run':collect(a)
        elif a.action=='negative':negative(a)
        else:
            check(allowed(a.output) and not Path(a.output).exists(),'require new ignored analysis output')
            if a.action=='quality':return quality_runs(a)
            write(a.output,validate(a.input));print('Mobile reconstruction runtime audit passed',flush=True)
        return 0
    except Exception as error:
        if a.action in ('validate','quality') and allowed(a.output) and not Path(a.output).exists():
            write(a.output,dict(kind='UEFogLab.MobileReconstructionAudit',passed=False,errors=[str(error)]))
        print('REJECTED: '+str(error),file=sys.stderr);return 2


if __name__=='__main__':sys.exit(main())
