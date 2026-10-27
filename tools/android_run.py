"""M4-PRE-02: exact dedicated APK installation and one fresh cold-start witness.

Only the dedicated package is installed/started/stopped. No uninstall, data clear,
global logcat clear, device settings, renderer probe, or performance measurement.
Raw receipts contain the device serial and must stay in ignored output folders.
"""
import argparse
import os
import copy
import json
import re
import shlex
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

from android_baseline import ROOT, allowed, audit, bind, check, git, read, utc, verify, write

PKG = 'com.uefoglab.mobilebaseline'
SDK = Path(os.environ.get('ANDROID_SDK_ROOT', os.environ.get('ANDROID_HOME', '')))
JAVA = Path(os.environ.get('JAVA_HOME', ''))/'bin/java.exe'
POLICY = {'readyTimeoutSeconds': 180, 'observeSeconds': 30,
          'minimumRenderedViewFamilies': 120, 'afterReadyTimeoutSeconds': 120,
          'singleCommandTimeoutSeconds': 60}
EXPECTED_CVARS = {'r.Mobile.ShadingPath': 0, 'r.MobileHDR': 1,
                  'r.Mobile.AntiAliasing': 0, 'r.MSAACount': 1,
                  'r.DynamicRes.OperationMode': 0, 'r.UEFog.Enabled': 0,
                  'r.Fog': 0, 'r.VolumetricFog': 0,
                  'r.LocalFogVolume': 0, 'r.SupportLocalFogVolumes': 0}


def new_output(path):
    out = Path(path).resolve()
    check(allowed(out) and not out.exists(), 'Require a new ignored Saved/work/out directory')
    out.mkdir(parents=True)
    return out


class Commands:
    def __init__(self, out, serial):
        self.out, self.serial, self.steps = out, serial, []

    def run(self, name, args, ok=True, adb=True):
        command = ([str(SDK/'platform-tools/adb.exe'), '-s', self.serial] if adb else []) + list(map(str, args))
        start = utc()
        try:
            p = subprocess.run(command, capture_output=True, timeout=POLICY['singleCommandTimeoutSeconds'],
                               creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
        except subprocess.TimeoutExpired:
            # subprocess.run kills and waits for its own child, never unrelated adb/UE processes.
            raise ValueError('Timed out own command: '+name)
        stdout, stderr = self.out/(name+'.stdout'), self.out/(name+'.stderr')
        stdout.write_bytes(p.stdout); stderr.write_bytes(p.stderr)
        self.steps.append({'name': name, 'command': command, 'startedUtc': start,
                           'finishedUtc': utc(), 'exitCode': p.returncode,
                           'stdout': bind(stdout), 'stderr': bind(stderr)})
        write(self.out/'commands.json', self.steps)
        check(not ok or p.returncode == 0, 'Command failed: '+name)
        return p.stdout.decode('utf-8-sig', errors='replace').strip()

    def shell(self, name, *args, ok=True):
        # adb shell concatenates argv; quote the complete remote shell command once.
        return self.run(name, ['shell', shlex.join(map(str, args))], ok=ok)

    def own_file(self, name, remote, ok=True):
        return self.run(name, ['exec-out', 'run-as', PKG, 'cat', remote], ok=ok)


def select(serial):
    p = subprocess.run([str(SDK/'platform-tools/adb.exe'), 'devices'], capture_output=True, timeout=30, check=True)
    devices = [line.split()[:2] for line in p.stdout.decode().splitlines()[1:] if line.strip()]
    if serial:
        check([serial, 'device'] in devices, 'Explicit device is disconnected or unauthorized')
    else:
        check(len(devices) == 1 and devices[0][1] == 'device', 'Require one online authorized device or explicit --serial')
        serial = devices[0][0]
    return serial


def installed(c, name):
    paths = c.shell(name+'-paths', 'pm', 'path', PKG)
    entries = re.findall(r'^package:(.+)$', paths, re.M)
    if not entries:
        return None
    check(len(entries) == 1 and entries[0].endswith('/base.apk'), 'Only one self-contained base APK is allowed')
    c.run(name+'-pull', ['pull', entries[0], str(c.out/(name+'.apk'))])
    apk = bind(c.out/(name+'.apk'))
    signing = c.run(name+'-signature', [JAVA, '-jar', SDK/'build-tools/36.0.0/lib/apksigner.jar',
                    'verify', '--verbose', '--print-certs', apk['path']], adb=False)
    cert = re.search(r'Signer #1 certificate SHA-256 digest: ([0-9a-fA-F]{64})', signing)
    check(cert and 'Verified using v2 scheme (APK Signature Scheme v2): true' in signing, 'Installed APK signature verification failed')
    pm = c.shell(name+'-pm', 'dumpsys', 'package', PKG)
    return {'apk': apk, 'devicePaths': entries, 'certificateSha256': cert[1].lower(),
            'packageManager': bind(c.out/(name+'-pm.stdout'))}


def proc_identity(text):
    end = text.rfind(')')
    fields = text[end+2:].split()
    check(end > 0 and len(fields) > 19, 'Missing current process kernel start time')
    return int(text.split(' ', 1)[0]), int(fields[19])


def foreground(c, name):
    # Persist only resumed-activity lines belonging to our package, never another app's data.
    expr = '(topResumedActivity|mResumedActivity|Resumed:).*'+re.escape(PKG)
    return c.run(name, ['shell', 'dumpsys activity activities '+shlex.quote(PKG)+
                       ' | grep -E '+shlex.quote(expr)], ok=False)


def events_from(text):
    result = []
    for line in text.splitlines():
        try:
            result.append(json.loads(line))
        except json.JSONDecodeError:
            pass  # A live append can be partial; final validation reads strict JSONL.
    return result


def device_info(c):
    info = {'alias': 'MEIZU21-LAB'}
    for name, prop in [('model', 'ro.product.model'), ('abi', 'ro.product.cpu.abi'),
                       ('android', 'ro.build.version.release'), ('api', 'ro.build.version.sdk'),
                       ('fingerprint', 'ro.build.fingerprint')]:
        info[name] = c.shell('device-'+name, 'getprop', prop)
    vk = c.shell('device-vulkan', 'cmd', 'gpu', 'vkjson')
    data = json.loads(vk)
    info['vulkan'] = bind(c.out/'device-vulkan.stdout')
    info['physicalDevices'] = [d['properties'] for d in data['devices']]
    check(info['model'] == 'MEIZU 21' and info['abi'] == 'arm64-v8a', 'Unexpected device/model/ABI')
    return info


def collect(a):
    out = new_output(a.output)
    check(not git('status', '--porcelain'), 'Commit source/tool/protocol before formal capture')
    candidate = Path(a.candidate).resolve(); cooked = audit(candidate); m = read(candidate/'package.json')
    check(cooked['identity']['applicationId'] == PKG, 'Wrong dedicated package')
    previous = audit(a.previous_candidate) if a.previous_candidate else None
    c = Commands(out, select(a.serial))
    run_id = uuid.uuid4().hex
    receipt = {'kind': 'UEFogLab.AndroidColdStart', 'schemaVersion': 1, 'runId': run_id,
               'policy': POLICY, 'toolRevision': git('rev-parse', 'HEAD'), 'tool': bind(__file__),
               'candidate': bind(candidate/'package.json'), 'startedUtc': utc(),
               'serial': c.serial, 'package': PKG, 'completed': False}
    write(out/'run.json', receipt)
    owned = False
    try:
        receipt['device'] = device_info(c)
        old = installed(c, 'before')
        if old:
            trusted = [m['package']['apk']['sha256']]
            if previous:
                check(previous['identity']['certificateSha256'] == cooked['identity']['certificateSha256'], 'Previous candidate signing lineage differs')
                trusted += [previous['installationSet'][0]['sha256']]
            check(old['apk']['sha256'] in trusted and old['certificateSha256'] == cooked['identity']['certificateSha256'],
                  'Existing package has unknown origin/signature; refuse overwrite')
        owned = True
        receipt['beforeInstallation'] = old
        c.shell('initial-stop', 'am', 'force-stop', PKG)
        c.run('install', ['install', '-r', m['package']['apk']['path']])
        receipt['installation'] = installed(c, 'installed')
        check(receipt['installation']['apk']['sha256'] == m['package']['apk']['sha256'], 'Installed SHA differs from candidate')
        private = c.shell('private-root', 'run-as', PKG, 'pwd')
        check(re.fullmatch(r'/data/user/\d+/'+re.escape(PKG), private), 'Unexpected private app root')
        remote = private+'/files/M4-PRE-02/'+run_id
        c.shell('mkdir', 'run-as', PKG, 'mkdir', '-p', remote)
        c.shell('cold-stop', 'am', 'force-stop', PKG)
        check(not c.shell('cold-pid', 'pidof', PKG, ok=False), 'Package did not stop before cold start')
        receipt['deviceEpochStart'] = c.shell('device-start-time', 'date', '+%s.%N')
        check(re.fullmatch(r'\d+\.\d+', receipt['deviceEpochStart']), 'Missing device wall-clock window')
        command_line = m['package']['commandLine']+' -FogLabMobileRun='+run_id+' -FogLabMobileOutput='+remote+' -abslog='+remote+'/engine.log'
        receipt['commandLine'], receipt['privateDirectory'] = command_line, remote
        receipt['launchUtc'] = utc(); clock = time.monotonic()
        started = c.shell('start', 'am', 'start', '-W', '-n', PKG+'/'+cooked['identity']['activity'], '--es', 'cmdline', command_line)
        check('Status: ok' in started and 'LaunchState: COLD' in started, 'Activity did not report a successful cold launch')
        # UE may briefly fork an own helper with the same process name. Do not
        # select the first pidof result: bind the this-run witness to /proc.
        launched = None
        for index in range(90):
            ev = events_from(c.own_file('launch-poll-'+str(index), remote+'/events.jsonl', ok=False))
            launched = next((e for e in ev if e.get('event') == 'launch' and e.get('runId') == run_id), None)
            if launched: break
            check(time.monotonic()-clock < POLICY['readyTimeoutSeconds'], 'Launch witness timeout')
            time.sleep(2)
        check(launched is not None, 'Missing current-run launch witness')
        receipt['pid'] = int(launched['pid']); pid_text = str(receipt['pid'])
        observed = c.shell('pid', 'pidof', PKG)
        check(pid_text in observed.split(), 'Witness PID is not a live package process')
        cmdline = c.shell('process-cmdline', 'run-as', PKG, 'cat', '/proc/'+pid_text+'/cmdline')
        check(cmdline.split('\x00')[0] == PKG, 'Witness PID is not the dedicated application main process')
        stat = c.shell('process-stat', 'run-as', PKG, 'cat', '/proc/'+pid_text+'/stat')
        pid, ticks = proc_identity(stat)
        receipt['processStartTicks'] = ticks
        check(pid == receipt['pid'], 'Kernel process identity changed')
        ready_at = None
        for index in range(160):
            text = c.own_file('poll-'+str(index), remote+'/events.jsonl', ok=False)
            ev = events_from(text)
            if any(e.get('event') == 'scene-ready' and e.get('runId') == run_id for e in ev) and ready_at is None:
                ready_at = time.monotonic(); receipt['readyObservedSeconds'] = ready_at-clock
                foreground(c, 'ready-foreground')
                print('Scene ready; observing >=30 seconds and >=120 rendered view families', flush=True)
            if any(e.get('event') == 'capture-complete' and e.get('runId') == run_id for e in ev):
                break
            check(time.monotonic()-clock < POLICY['readyTimeoutSeconds'] if ready_at is None else
                  time.monotonic()-ready_at < POLICY['afterReadyTimeoutSeconds'], 'Frozen scene-ready/observation timeout')
            time.sleep(2)
        else:
            raise ValueError('No capture within bounded polling window')
        c.own_file('events', remote+'/events.jsonl')
        c.own_file('frame', remote+'/frame.png')
        c.own_file('engine', remote+'/engine.log')
        foreground(c, 'capture-foreground')
        check(pid_text in c.shell('final-pid', 'pidof', PKG).split(), 'Process died/restarted during observation')
        c.shell('final-process-stat', 'run-as', PKG, 'cat', '/proc/'+pid_text+'/stat')
        receipt['deviceEpochEnd'] = c.shell('device-end-time', 'date', '+%s.%N')
        # --pid AND an actual device-clock window; never clear the global log buffer.
        c.run('logcat', ['logcat', '-d', '--pid='+pid_text, '-v', 'epoch', '-T', receipt['deviceEpochStart']], ok=False)
        receipt.update(completed=True, finishedUtc=utc())
    except Exception as error:
        receipt.update(error=str(error), finishedUtc=utc())
        raise
    finally:
        if owned:
            try:
                c.shell('cleanup-stop', 'am', 'force-stop', PKG)
                receipt['cleanupConfirmed'] = not c.shell('cleanup-pid', 'pidof', PKG, ok=False)
            except Exception as error:
                receipt['cleanupConfirmed'] = False; receipt['cleanupError'] = str(error)
        receipt['artifacts'] = [bind(p) for p in sorted(out.iterdir()) if p.is_file() and p.name not in ('run.json', 'validation.json')]
        write(out/'run.json', receipt)
    result = validate(out)
    write(out/'validation.json', result)
    print('Cold start passed: '+run_id, flush=True)


def validate(directory):
    out = Path(directory).resolve(); r = read(out/'run.json')
    check(r.get('kind') == 'UEFogLab.AndroidColdStart' and r.get('schemaVersion') == 1, 'Wrong startup schema')
    check(r['policy'] == POLICY and r['package'] == PKG and re.fullmatch('[0-9a-f]{32}', r['runId']), 'Wrong protocol/package/runId')
    required = {'commands.json', 'events.stdout', 'frame.stdout', 'engine.stdout', 'process-stat.stdout',
                'final-process-stat.stdout', 'ready-foreground.stdout', 'capture-foreground.stdout',
                'cold-pid.stdout', 'cleanup-pid.stdout', 'installed-paths.stdout', 'installed-pm.stdout', 'process-cmdline.stdout',
                'installed-signature.stdout', 'install.stdout', 'start.stdout', 'pid.stdout', 'final-pid.stdout'}
    required |= {'device-start-time.stdout', 'device-end-time.stdout', 'logcat.stdout'}
    paths = [Path(b['path']).resolve() for b in r['artifacts']]
    check(len(paths) == len(set(paths)) and all(out/name in paths for name in required), 'Missing/duplicate bound raw startup evidence')
    for artifact in r['artifacts']:
        verify(artifact)
    verify(r['candidate']); m = read(r['candidate']['path'])
    check(r['completed'] and r['cleanupConfirmed'], 'Incomplete run or unconfirmed package cleanup')
    installed_apk = r['installation']['apk']; verify(installed_apk)
    check(installed_apk['sha256'] == m['package']['apk']['sha256'], 'Installed SHA mismatch')
    check(r['installation']['certificateSha256'] == m['package']['identity']['certificateSha256'], 'Installed signer mismatch')
    pm = Path(r['installation']['packageManager']['path']).read_text(encoding='utf8')
    check(Path(r['installation']['packageManager']['path']).resolve() == out/'installed-pm.stdout', 'Unbound PackageManager evidence')
    check(re.search(r'\bversionCode='+str(m['package']['identity']['versionCode'])+r'\b', pm) and
          'versionName='+m['package']['identity']['versionName'] in pm, 'PackageManager version mismatch')
    check(re.findall(r'^package:(.+)$', (out/'installed-paths.stdout').read_text(), re.M) == r['installation']['devicePaths'], 'Installed base/split path mismatch')
    signing = (out/'installed-signature.stdout').read_text()
    check('Verified using v2 scheme (APK Signature Scheme v2): true' in signing and
          'Signer #1 certificate SHA-256 digest: '+r['installation']['certificateSha256'] in signing, 'Installed signature evidence mismatch')
    commands = read(out/'commands.json')
    by_name = {s['name']: s for s in commands}
    check(len(by_name) == len(commands), 'Duplicate command evidence')
    for name in ('install', 'start', 'pid', 'final-pid', 'process-stat', 'final-process-stat', 'cleanup-stop'):
        check(by_name.get(name, {}).get('exitCode') == 0, 'Missing/failed own command: '+name)
    check('Success' in (out/'install.stdout').read_text() and
          'LaunchState: COLD' in (out/'start.stdout').read_text() and 'Status: ok' in (out/'start.stdout').read_text(), 'Missing actual installation/cold-start return')
    check(not (out/'cold-pid.stdout').read_text().strip() and not (out/'cleanup-pid.stdout').read_text().strip(), 'Cold-start/cleanup PID remained alive')
    check(all(str(r['pid']) in (out/(name+'.stdout')).read_text().split() for name in ('pid', 'final-pid')), 'Observed package PID changed')
    check((out/'process-cmdline.stdout').read_bytes().split(b'\0')[0].decode() == PKG, 'Kernel main-process name mismatch')
    check((out/'device-start-time.stdout').read_text().strip() == r['deviceEpochStart'] and
          (out/'device-end-time.stdout').read_text().strip() == r['deviceEpochEnd'] and
          0 < float(r['deviceEpochEnd'])-float(r['deviceEpochStart']) < 360, 'Invalid device log time window')
    log_command = by_name.get('logcat', {}).get('command', [])
    check('--pid='+str(r['pid']) in log_command and '-T' in log_command and
          log_command[log_command.index('-T')+1] == r['deviceEpochStart'], 'Unscoped PID/logcat time window')
    events = [json.loads(line) for line in (out/'events.stdout').read_text(encoding='utf-8-sig').splitlines()]
    check(events and all(e.get('runId') == r['runId'] for e in events), 'Wrong runId or stale startup log')
    for e in events:
        check(e.get('kind') == 'UEFogLab.AndroidStartupWitness' and e.get('schemaVersion') == 1, 'Wrong event schema')
        check(e['pid'] == r['pid'] and e['processStartTicks'] == r['processStartTicks'] > 0 and e['kernelClockTicksPerSecond'] > 0,
              'Stale PID/kernel process identity')
        check(e['declaredBuildId'] == m['buildId'] and e['declaredSourceRevision'] == m['sourceRevision'], 'Wrong executed candidate declaration')
    def one(name):
        selected = [e for e in events if e['event'] == name]
        check(len(selected) == 1, 'Missing/duplicate this-run '+name)
        return selected[0]
    launch, ready, capture = one('launch'), one('scene-ready'), one('capture-complete')
    check(launch['commandLine'].strip() == r['commandLine'].strip() and '-FogLabMobileRun='+r['runId'] in launch['commandLine'], 'Actual command line mismatch')
    check(launch['engineVersion'].startswith('5.8.4-58902052'), 'Wrong executed engine version')
    check(launch['monotonicSeconds'] <= ready['monotonicSeconds'] < capture['monotonicSeconds'] and
          0 <= r['readyObservedSeconds'] <= POLICY['readyTimeoutSeconds'], 'Invalid scene-ready time window')
    check(ready['map'] == '/Engine/Maps/Entry' and ready['gameMode'] == '/Script/FogLabDemo.FogLabGameMode' and
          ready['cube'] == '/Engine/BasicShapes/Cube.Cube' and ready['staticMeshActors'] >= 10, 'Actual scene/assets mismatch')
    check(capture['saved'] and capture['hasFocus'] and capture['secondsAfterSceneReady'] >= POLICY['observeSeconds'], 'Insufficient focused post-ready observation/capture')
    views = [e for e in events if e['event'] == 'view']
    check(views and views[0]['renderedViewFamilies'] == 1 and views[0]['monotonicSeconds'] >= ready['monotonicSeconds'], 'Missing first rendered game view')
    last = capture['lastObservedView']
    check(last['runId'] == r['runId'] and last['pid'] == r['pid'] and last['processStartTicks'] == r['processStartTicks'] and
          last['renderedViewFamilies'] >= POLICY['minimumRenderedViewFamilies'] and
          last['monotonicSeconds']-ready['monotonicSeconds'] >= POLICY['observeSeconds']-0.5, 'Insufficient actual rendered frames/time')
    for v in views+[last]:
        check(v['rhi'] == 'Vulkan' and v['featureLevel'] == 'ES3_1' and v['shaderFormat'] == 'SF_VULKAN_ES31_ANDROID', 'Wrong actual mobile RHI/shader platform')
        check(v['cvars'] == EXPECTED_CVARS, 'Wrong effective Forward/HDR/AA/fog configuration')
        check(v['unscaledViewMin'] == [0, 0] and all(x > 0 for x in v['unscaledViewSize']) and
              v['viewFamilyRenderTargetSize'] == v['unscaledViewSize'], 'Unexpected initial view dimensions')
    for name in ('process-stat', 'final-process-stat'):
        check(proc_identity((out/(name+'.stdout')).read_text()) == (r['pid'], r['processStartTicks']), 'Kernel process identity changed')
    for name in ('ready-foreground', 'capture-foreground'):
        check(re.search(r'(?:topResumedActivity|mResumedActivity|Resumed:).*'+re.escape(PKG)+'/', (out/(name+'.stdout')).read_text()), 'Application not resumed in foreground')
    from PIL import Image, ImageStat
    with Image.open(out/'frame.stdout') as im:
        im.load()
        check(im.format == 'PNG' and list(im.size) == [capture['width'], capture['height']] and
              capture['imageBytes'] == (out/'frame.stdout').stat().st_size and min(im.size) >= 360, 'Missing/invalid this-run PNG')
        check(max(ImageStat.Stat(im.convert('RGB')).stddev) > 10, 'Scene image is blank/constant')
    engine = (out/'engine.stdout').read_text(encoding='utf8', errors='replace')
    check(r['runId'] in engine and m['buildId'] in engine, 'Missing this-run engine log')
    check(not re.search(r'(?:Fatal error|Assertion failed|Failed to load.*(?:Cube|shader)|Missing.*(?:shader|material)|Log(?:Material|Shader)[^\n]*: Error:)', engine, re.I), 'Scene shader/material/startup error')
    # Freshly re-open/verify the actual cooked APK and its content, not a cached audit boolean.
    cooked = audit(Path(r['candidate']['path']).parent)
    return {'kind': 'UEFogLab.AndroidColdStartAudit', 'schemaVersion': 1, 'passed': True,
            'run': bind(out/'run.json'), 'runId': r['runId'], 'candidate': r['candidate'],
            'sourceRevision': m['sourceRevision'], 'buildId': m['buildId'], 'apk': m['package']['apk'],
            'pid': r['pid'], 'processStartTicks': r['processStartTicks'], 'deviceAlias': r['device']['alias'],
            'readyObservedSeconds': r['readyObservedSeconds'], 'secondsAfterSceneReady': capture['secondsAfterSceneReady'],
            'renderedViewFamilies': last['renderedViewFamilies'], 'initialView': views[0],
            'image': bind(out/'frame.stdout'), 'cookAudit': cooked['currentArtifactVerification'],
            'limits': ['CPU render-thread view-family count is not GPU completion or FPS',
                       'Visual geometry/material inspection is separate', 'No resource/depth/HDR probe or fog adaptation']}


def negative(a):
    source = Path(a.input).resolve(); validate(source)
    out = new_output(a.output); cases = []
    for name in ('wrong-runid', 'stale-log', 'installed-sha', 'no-scene-ready'):
        dest = out/name; dest.mkdir()
        r = copy.deepcopy(read(source/'run.json'))
        # Small evidence copies only; immutable APK/cook artifacts retain their original bindings.
        for p in source.iterdir():
            if p.suffix in ('.stdout', '.stderr') or p.name == 'commands.json':
                shutil.copyfile(p, dest/p.name)
        r['installation']['packageManager'] = bind(dest/'installed-pm.stdout')
        if name == 'wrong-runid':
            r['runId'] = uuid.uuid4().hex
        elif name == 'installed-sha':
            # Hash metadata agrees with these local bytes; reject the semantic
            # candidate/installation mismatch. This file is NEVER installed.
            (dest/'mismatch.apk').write_bytes(b'Isolated local SHA mismatch fixture; never installed.\n')
            r['installation']['apk'] = bind(dest/'mismatch.apk')
        else:
            ev = [json.loads(line) for line in (dest/'events.stdout').read_text(encoding='utf8').splitlines()]
            if name == 'stale-log':
                for e in ev:
                    e['processStartTicks'] -= 1000
            else:
                ev = [e for e in ev if e['event'] != 'scene-ready']
            (dest/'events.stdout').write_text(''.join(json.dumps(e)+'\n' for e in ev), encoding='utf8')
        r['artifacts'] = [bind(dest/Path(b['path']).name) if (dest/Path(b['path']).name).exists() else b for b in r['artifacts']]
        write(dest/'run.json', r)
        p = subprocess.run([sys.executable, str(Path(__file__)), 'validate', '--input', str(dest), '--output', str(dest/'cli.json')],
                           capture_output=True, timeout=180)
        (dest/'cli.stdout').write_bytes(p.stdout); (dest/'cli.stderr').write_bytes(p.stderr)
        result = read(dest/'cli.json')
        check(p.returncode == 2 and result['passed'] is False, 'Negative CLI unexpectedly accepted: '+name)
        expected = {'wrong-runid': 'Wrong runId', 'stale-log': 'Stale PID/kernel',
                    'installed-sha': 'Installed SHA mismatch', 'no-scene-ready': 'Missing/duplicate this-run scene-ready'}
        check(expected[name] in ' '.join(result['errors']), 'Negative rejected for the wrong reason: '+name)
        cases.append({'case': name, 'exitCode': p.returncode, 'result': bind(dest/'cli.json'), 'errors': result['errors']})
    write(out/'negative.json', {'kind': 'UEFogLab.AndroidStartupNegative', 'passed': True, 'cases': cases})
    print('Four real local negative CLI cases rejected', flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__); subs = p.add_subparsers(dest='action', required=True)
    run = subs.add_parser('run'); run.add_argument('--candidate', required=True); run.add_argument('--previous-candidate', default=''); run.add_argument('--serial', default='')
    for name in ('validate', 'negative'):
        sub = subs.add_parser(name); sub.add_argument('--input', required=True)
    for sub in subs.choices.values():
        sub.add_argument('--output', required=True)
    a = p.parse_args()
    try:
        if a.action == 'run': collect(a)
        elif a.action == 'negative': negative(a)
        else:
            check(allowed(a.output) and not Path(a.output).exists(), 'Require a new ignored validation output file')
            write(a.output, validate(a.input)); print('Cold-start audit passed', flush=True)
        return 0
    except Exception as error:
        if a.action == 'validate' and allowed(a.output) and not Path(a.output).exists():
            write(a.output, {'kind': 'UEFogLab.AndroidColdStartAudit', 'passed': False, 'errors': [str(error)]})
        print('REJECTED: '+str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
