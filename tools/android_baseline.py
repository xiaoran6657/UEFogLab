"""M4-PRE-01: a fresh real Android cook/package and an auditable offline gate.

No adb install/start, device probe, SDK installation or engine editing lives here.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PROFILE = 'VulkanMobileForwardHDR'
CONFIG = ROOT/'tools/android_baseline_config.json'


def check(value, message):
    if not value:
        raise ValueError(message)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf8')


def digest(stream):
    h = hashlib.sha256()
    for block in iter(lambda: stream.read(1024*1024), b''):
        h.update(block)
    return h.hexdigest()


def bind(path):
    path = Path(path).resolve()
    with path.open('rb') as stream:
        sha = digest(stream)
    return {'path': str(path), 'bytes': path.stat().st_size, 'sha256': sha}


def verify(b):
    check(bind(b['path']) == b, 'Artifact identity mismatch: '+b['path'])


def allowed(path):
    return any(Path(path).resolve().is_relative_to(ROOT/n) for n in ('Saved', 'work', 'out'))


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def inputs():
    return [bind(ROOT/p) for p in git('ls-files', 'UEFogLab.uproject', 'Source', 'Plugins/UEFog', 'Config', 'tools').splitlines()]


def utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def plain_tree(path):
    """Reject reparse points before walking/moving this project's stage tree."""
    path = Path(path)
    check(not path.is_symlink() and not path.is_junction(), 'Stage contains a symlink/junction')
    if path.is_dir():
        for entry in os.scandir(path):
            check(not (getattr(entry.stat(follow_symlinks=False), 'st_file_attributes', 0) & 0x400), 'Stage contains a reparse point')
            if entry.is_dir(follow_symlinks=False):
                plain_tree(entry.path)


def prepare_stage(out):
    # Installed UEDeployAndroid.cs hardcodes this location for embedded OBB
    # and UECommandLine.txt. A custom UAT stagingdirectory loses those inputs.
    stage = ROOT/'Saved/StagedBuilds/Android_ASTC'
    check(stage.resolve().is_relative_to(ROOT/'Saved'), 'Shared stage escaped the workspace')
    previous = []
    for source in (stage, stage.with_suffix('.obb'), stage.with_suffix('.patch.obb')):
        if source.exists():
            plain_tree(source)
            destination = out/'prior-stage'/source.name
            check(source.resolve().is_relative_to(ROOT/'Saved') and destination.resolve().is_relative_to(out), 'Unsafe stage preservation move')
            check(not destination.exists(), 'Prior-stage destination already exists')
            destination.parent.mkdir(exist_ok=True)
            shutil.move(str(source), str(destination))
            previous.append(str(destination))
    check(not stage.exists() and not stage.with_suffix('.obb').exists(), 'Shared stage was not invalidated')
    return {'path': str(stage), 'freshBeforeUAT': True, 'preservedPriorPaths': previous, 'snapshot': str(out/'stage/Android_ASTC')}


def inventory(directory):
    return [bind(p) for p in sorted(Path(directory).rglob('*')) if p.is_file()]


def run(command, out, name, steps, timeout=180):
    log = out/(name+'.txt'); started = utc(); clock = time.monotonic()
    with log.open('w', encoding='utf8') as output:
        process = subprocess.Popen([str(x) for x in command], cwd=ROOT, stdout=output, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        step = {'name': name, 'command': [str(x) for x in command], 'pid': process.pid, 'startedUtc': started,
                'stdout': str(log), 'exitConfirmed': False}
        steps.append(step); write(out/'processes.json', steps)
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            # This Popen still owns the live root PID; stop only its child tree.
            if os.name == 'nt' and process.poll() is None:
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, timeout=30)
            else:
                process.kill()
            code = process.wait(timeout=30)
            step['timedOut'] = True
        step.update(exitCode=code, exitConfirmed=True, finishedUtc=utc(), elapsedSeconds=time.monotonic()-clock)
        write(out/'processes.json', steps)
    check(code == 0 and not step.get('timedOut'), f'{name} failed; inspect {log}')
    return log.read_text(encoding='utf-8-sig', errors='replace')


def parse_pak(text):
    paths = re.findall(r'"([^"\r\n]+)"\s+offset:\s*\d+,\s*size:\s*\d+', text)
    check(paths, 'UnrealPak did not list nonempty real content')
    return sorted(set(p.replace('\\', '/') for p in paths))


def dependency_graph(text):
    check('--- Begin CachedDependsNodes ---' in text, 'Missing real dependency graph dump')
    graph = {}; node = None; category = None
    for line in text.split('--- Begin CachedDependsNodes ---', 1)[1].splitlines():
        if line.startswith('--- End CachedDependsNodes'):
            break
        if line.startswith('\t/'):
            node = line.strip(); graph.setdefault(node, []); category = None
        elif line.startswith('\t\t\t/') and node and category == 'Package':
            target = line.strip().split()[0]
            if re.search(r'\bGame\b', line):
                graph[node].append(target)
        elif line.startswith('\t\t') and not line.startswith('\t\t\t'):
            category = line.strip()
    check(graph, 'Empty asset dependency graph')
    return graph


def closure(graph, root):
    check(root in graph and graph[root], 'Cube has no dependency witness')
    pending = [root]; seen = set(); code = set()
    while pending:
        p = pending.pop()
        if p.startswith('/Script/'):
            code.add(p); continue
        if p in seen:
            continue
        seen.add(p); pending += graph.get(p, [])
    check(any('Material' in p for p in seen), 'Recursive Cube material closure missing')
    return sorted(seen), sorted(code)


def package_path(package):
    check(package.startswith('/Engine/'), 'Unexpected runtime asset mount in Cube closure: '+package)
    return 'Engine/Content/'+package[len('/Engine/'):]


def parse_identity(badging, xml, signing, config):
    package = re.search(r"package: name='([^']+)' versionCode='([^']+)' versionName='([^']+)'", badging)
    check(package and package.groups() == (config['applicationId'], str(config['versionCode']), config['versionName']), 'Actual APK application/version mismatch')
    minimum = re.search(r"^(?:minSdkVersion|sdkVersion):'(\d+)'", badging, re.M)
    target = re.search(r"^targetSdkVersion:'(\d+)'", badging, re.M)
    check(minimum and target and int(minimum[1]) == config['minSdk'] and int(target[1]) == config['targetSdk'], 'Actual APK SDK mismatch')
    abi = re.search(r'^native-code:(.+)$', badging, re.M)
    check(abi and re.findall(r"'([^']+)'", abi[1]) == [config['abi']], 'APK must contain only arm64 ABI')
    activity = re.search(r"launchable-activity: name='([^']+)'", badging)
    check(activity is not None, 'APK launch Activity missing')
    compile_sdk = re.search(r'compileSdkVersion\([^)]*\)=(?:\(type 0x10\)0x([0-9a-f]+)|(\d+))', xml)
    compile_value = int(compile_sdk[1], 16) if compile_sdk and compile_sdk[1] else int(compile_sdk[2]) if compile_sdk else None
    check(compile_value == config['compileSdk'], 'Actual manifest compile SDK mismatch')
    cert = re.search(r'Signer #1 certificate SHA-256 digest: ([0-9a-fA-F]+)', signing)
    check(cert and len(cert[1]) == 64 and 'Verified using v2 scheme (APK Signature Scheme v2): true' in signing, 'APK v2/certificate verification missing')
    check('CN=Android Debug' in signing, 'Expected isolated development debug signature')
    check(re.search(r'android:debuggable\([^)]*\)=(?:true|\(type 0x12\)0xffffffff)', xml), 'Development debuggable manifest missing/false')
    orientations = re.findall(r'android:screenOrientation\([^)]*\)=(?:\(type 0x10\)0x)?([0-9a-f]+)', xml)
    check(orientations and all(int(v, 16) == 0 for v in orientations), 'APK activities are not landscape')
    return {'applicationId': package[1], 'versionCode': int(package[2]), 'versionName': package[3],
            'activity': activity[1], 'abi': config['abi'], 'minSdk': int(minimum[1]), 'targetSdk': int(target[1]),
            'compileSdk': compile_value, 'certificateSha256': cert[1].lower(), 'signatureV2Verified': True,
            'orientation': 'landscape',
            'signing': 'development debug keystore in isolated ANDROID_USER_HOME; not release signing'}


def extract_member(archive, member, target):
    check(not target.exists(), 'Preserve existing extracted output')
    target.parent.mkdir(parents=True, exist_ok=True)
    with archive.open(member) as source, target.open('wb') as dest:
        for block in iter(lambda: source.read(1024*1024), b''):
            dest.write(block)


def collect(out, engine, sdk, java, config, steps):
    apks = list((out/'archive').rglob('*.apk'))
    check(len(apks) == 1 and not list((out/'archive').rglob('*.obb')), 'Need exactly one self-contained archived APK')
    apk = apks[0]; tools = sdk/'build-tools/36.0.0'
    badging = run([tools/'aapt2.exe', 'dump', 'badging', apk], out, 'apk-badging', steps)
    xml = run([tools/'aapt2.exe', 'dump', 'xmltree', '--file', 'AndroidManifest.xml', apk], out, 'apk-manifest', steps)
    signing = run([java/'bin/java.exe', '-jar', tools/'lib/apksigner.jar', 'verify', '--verbose', '--print-certs', apk], out, 'apk-signature', steps)
    identity = parse_identity(badging, xml, signing, config)
    with zipfile.ZipFile(apk) as z:
        write(out/'apk-entries.json', [{'name': i.filename, 'bytes': i.file_size, 'compressedBytes': i.compress_size, 'crc32': i.CRC} for i in z.infolist()])
        members = [n for n in z.namelist() if n.startswith('assets/') and n.endswith('.obb.png')]
        check(len(members) == 1, 'APK has no unique embedded cooked OBB')
        obb = out/'embedded'/Path(members[0]).name
        extract_member(z, members[0], obb)
        command_members = [n for n in z.namelist() if n.endswith('UECommandLine.txt')]
        check(len(command_members) == 1, 'APK command line asset missing')
        commandline = z.read(command_members[0]).decode('utf-8-sig').strip()
        (out/'apk-commandline.txt').write_text(commandline, encoding='utf8')
        libs = [n for n in z.namelist() if n.startswith('lib/') and n.endswith('.so')]
        check(libs and all(n.startswith('lib/arm64-v8a/') for n in libs), 'Unexpected installed native ABI')
        unreal_libs = [n for n in libs if n.endswith('/libUnreal.so')]
        check(len(unreal_libs) == 1, 'Compiled game library missing')
        markers = [b'FogLabGameMode', b'FogLabDemo']; found = set(); carry = b''
        with z.open(unreal_libs[0]) as stream:
            for block in iter(lambda: stream.read(1024*1024), b''):
                block = carry+block
                for marker in markers:
                    if marker in block or marker.decode().encode('utf-16le') in block:
                        found.add(marker.decode())
                carry = block[-128:]
        check(found == {'FogLabGameMode', 'FogLabDemo'}, 'C++ game module/class missing from installed native library')
    paks = []
    with zipfile.ZipFile(obb) as z:
        write(out/'obb-entries.json', [{'name': i.filename, 'bytes': i.file_size, 'crc32': i.CRC} for i in z.infolist()])
        for name in z.namelist():
            if name.endswith('.pak'):
                target = (out/'embedded'/name).resolve()
                check(target.is_relative_to(out/'embedded'), 'Unsafe OBB member path')
                extract_member(z, name, target); paks.append(target)
    check(len(paks) == 1, 'Expected one auditable cooked pak')
    pak = paks[0]
    listing = run([engine/'Engine/Binaries/Win64/UnrealPak.exe', pak, '-List'], out, 'pak-list', steps)
    paths = parse_pak(listing); write(out/'pak-entries.json', paths)
    run([engine/'Engine/Binaries/Win64/UnrealPak.exe', pak, '-Extract', out/'pak-configs', '-Filter=*UEFogLab/Config/*.ini'], out, 'pak-configs-extract', steps)
    registries = list((out/'cooked').rglob('DevelopmentAssetRegistry.bin'))
    check(len(registries) == 1, 'This cook has no unique DevelopmentAssetRegistry')
    run([engine/'Engine/Binaries/Win64/UnrealEditor-Cmd.exe', ROOT/'UEFogLab.uproject', '-run=DumpAssetRegistry',
         '-Path='+str(registries[0]), '-OutDir='+str(out/'registry-dump'), '-DependencyDetails', '-PackageName',
         '-LinesPerPage=100000', '-unattended', '-NullRHI', '-nosound', '-abslog='+str(out/'registry-dump.log')],
        out, 'registry-dump-process', steps, 300)
    dumps = sorted((out/'registry-dump').glob('Page_*.txt')); check(dumps, 'Registry dump produced no pages')
    graph = dependency_graph('\n'.join(p.read_text(encoding='utf-8-sig') for p in dumps))
    packages, native = closure(graph, config['cube'])
    closure_data = {'root': config['cube'], 'packages': packages, 'nativeCodeDependencies': native,
                    'edges': {p: graph.get(p, []) for p in packages}, 'rule': 'recursive Game package references; Script dependencies are compiled classes'}
    write(out/'cube-dependencies.json', closure_data)
    return {'apk': bind(apk), 'embeddedObb': bind(obb), 'pak': bind(pak), 'identity': identity,
            'commandLine': commandline, 'developmentRegistry': bind(registries[0]),
            'registryPages': [bind(p) for p in dumps], 'compiledClassMarkers': sorted(found),
            'projectConfigs': inventory(out/'pak-configs')}


def ini_values(path):
    result = {}; section = ''
    for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith(';'):
            continue
        if line.startswith('[') and line.endswith(']'):
            section = line[1:-1]; result.setdefault(section, {})
        elif '=' in line:
            key, value = line.split('=', 1); result[section][key] = value
    return result


def current_artifacts(out, m, config, engine, sdk, java):
    """Requery the actual signed APK and its entire derived-container chain.

    Tool locations come from trusted CLI/build arguments, not commands supplied
    by a candidate manifest. Preserve replay output separately from old evidence.
    """
    engine, sdk, java = (Path(p).resolve() for p in (engine, sdk, java))
    check(read(engine/'Engine/Build/Build.version') == m['engineBuild'], 'Current verification engine differs from candidate')
    check(Path(m['processEnvironment']['ANDROID_HOME']).resolve() == sdk and Path(m['processEnvironment']['JAVA_HOME']).resolve() == java, 'Select the candidate SDK/JBR explicitly for verification')
    p = m['package']; scratch = ROOT/'Saved/M4-PRE/AuditReplay'/uuid.uuid4().hex
    scratch.mkdir(parents=True); steps = []; tools = sdk/'build-tools/36.0.0'
    signing = run([java/'bin/java.exe', '-jar', tools/'lib/apksigner.jar', 'verify', '--verbose', '--print-certs', p['apk']['path']], scratch, 'current-apk-signature', steps)
    badging = run([tools/'aapt2.exe', 'dump', 'badging', p['apk']['path']], scratch, 'current-apk-badging', steps)
    xml = run([tools/'aapt2.exe', 'dump', 'xmltree', '--file', 'AndroidManifest.xml', p['apk']['path']], scratch, 'current-apk-manifest', steps)
    identity = parse_identity(badging, xml, signing, config)
    check(identity == p['identity'] and 'Number of signers: 1' in signing, 'Current APK identity/certificate differs from recorded candidate')
    with zipfile.ZipFile(p['embeddedObb']['path']) as z:
        members = [n for n in z.namelist() if n.endswith('.pak')]
        check(len(members) == 1, 'Current OBB has no unique pak')
        with z.open(members[0]) as stream:
            check(digest(stream) == p['pak']['sha256'], 'Current OBB member hash differs from derived pak')
    # Do not accept a correctly hashed text list or extracted ini as a substitute
    # for querying the actual pak that is inside this current signed APK.
    listing = run([engine/'Engine/Binaries/Win64/UnrealPak.exe', p['pak']['path'], '-List'], scratch, 'current-pak-list', steps)
    check(parse_pak(listing) == parse_pak((out/'pak-list.txt').read_text(encoding='utf8')), 'Current pak list differs from recorded content witness')
    run([engine/'Engine/Binaries/Win64/UnrealPak.exe', p['pak']['path'], '-Extract', scratch/'pak-configs', '-Filter=*UEFogLab/Config/*.ini'], scratch, 'current-project-configs', steps)
    # An isolated evidence copy may share immutable, hash-bound extracted files
    # with its original. Compare the logical pak paths, not physical parents.
    old = {}
    for b in p['projectConfigs']:
        parts = Path(b['path']).as_posix().rsplit('/pak-configs/', 1)
        check(len(parts) == 2 and parts[1].startswith('UEFogLab/Config/'), 'Recorded configuration lacks its logical pak path')
        check(parts[1] not in old, 'Duplicate recorded project configuration')
        old[parts[1]] = (b['bytes'], b['sha256'])
    fresh = {Path(b['path']).relative_to(scratch/'pak-configs').as_posix(): (b['bytes'], b['sha256']) for b in inventory(scratch/'pak-configs')}
    check(fresh == old and fresh, 'Current pak project configurations differ from recorded extracted inputs')
    result = {'kind': 'UEFogLab.AndroidCurrentArtifactVerification', 'schemaVersion': 1, 'passed': True,
              'candidate': bind(out/'package.json'), 'apk': p['apk'], 'obb': p['embeddedObb'], 'pak': p['pak'],
              'currentIdentity': identity, 'obbPakMember': members[0], 'steps': steps,
              'toolRevision': git('rev-parse', 'HEAD'), 'tool': bind(__file__),
              'artifacts': [bind(f) for f in sorted(scratch.glob('*.txt'))] + inventory(scratch/'pak-configs')}
    write(scratch/'verification.json', result)
    return bind(scratch/'verification.json')


def audit(directory, engine=os.environ.get('UEFOGLAB_ENGINE_ROOT', ''), sdk=os.environ.get('ANDROID_SDK_ROOT', os.environ.get('ANDROID_HOME', '')), java=os.environ.get('JAVA_HOME', '')):
    out = Path(directory).resolve(); m = read(out/'package.json')
    check(m.get('kind') == 'UEFogLab.AndroidBaselineCook' and m.get('schemaVersion') == 1, 'Not an Android cooked baseline manifest')
    check(m.get('requestedProfile') == PROFILE, 'Wrong profile: require Vulkan Mobile Forward + HDR')
    check(m.get('uatCompleted') is True and m.get('steps'), 'Missing this-run cook/stage/package completion witness')
    config = read(CONFIG)
    check(m['profile'] == config, 'Profile configuration mismatch')
    check(re.fullmatch('[0-9a-f]{40}', m['sourceRevision']) and not m['sourceWorkingTree'], 'Invalid/dirty source revision')
    names = git('ls-tree', '-r', '--name-only', m['sourceRevision'], '--', 'UEFogLab.uproject', 'Source', 'Plugins/UEFog', 'Config', 'tools').splitlines()
    check({Path(b['path']).relative_to(ROOT).as_posix() for b in m['sourceInputs']} == set(names) and len(m['sourceInputs']) == len(names), 'Incomplete frozen source inputs')
    archive = {entry['relative']: entry['file'] for entry in m['sourceArchive']}
    check(set(archive) == set(names) and len(m['sourceArchive']) == len(names), 'Missing exact frozen source byte archive')
    for b in m['sourceInputs']:
        relative = Path(b['path']).relative_to(ROOT).as_posix()
        snapshot = archive[relative]; verify(snapshot)
        check(Path(snapshot['path']).resolve() == out/'source-inputs'/relative and snapshot['bytes'] == b['bytes'] and snapshot['sha256'] == b['sha256'], 'Exact source byte archive mismatch')
        captured = Path(snapshot['path']).read_bytes()
        raw = subprocess.check_output(['git', 'show', m['sourceRevision']+':'+relative], cwd=ROOT)
        # git autocrlf may normalize a file with mixed line endings. Preserve
        # exact input bytes separately and prove their canonical Git contents.
        check(captured == raw if b'\0' in raw else captured.replace(b'\r\n', b'\n') == raw.replace(b'\r\n', b'\n'), 'Input not bound to frozen Git revision: '+relative)
    required = {'sdk-validation.txt', 'java-version.txt', 'gradle-version.txt', 'uat.txt', 'apk-badging.txt',
                'apk-manifest.txt', 'apk-signature.txt', 'apk-commandline.txt', 'pak-list.txt', 'pak-configs-extract.txt', 'registry-dump-process.txt',
                'apk-entries.json', 'obb-entries.json', 'pak-entries.json', 'cube-dependencies.json',
                'stage-files.json', 'archive-files.json', 'cook-files.json'}
    check(len(m['artifacts']) == len(required) and {Path(b['path']).resolve() for b in m['artifacts']} == {out/n for n in required}, 'Missing/duplicate required cook/content witness bindings')
    for b in m['artifacts']:
        verify(b)
    for b in m['environmentInputs']:
        verify(b)
    check(all(m['engineBuild'].get(k) == v for k, v in config['engine'].items()), 'Wrong installed engine')
    uat = next(s for s in m['steps'] if s['name'] == 'uat')
    expected_steps = {'sdk-validation', 'java-version', 'gradle-version', 'uat', 'apk-badging', 'apk-manifest', 'apk-signature', 'pak-list', 'pak-configs-extract', 'registry-dump-process'}
    check(len(m['steps']) == len(expected_steps) and {s['name'] for s in m['steps']} == expected_steps, 'Missing/duplicate tool process witnesses')
    check(uat['exitCode'] == 0 and uat['exitConfirmed'] and all(s['exitCode'] == 0 and s['exitConfirmed'] for s in m['steps']), 'Unconfirmed/failed tool process')
    args = uat['command']; lower = [a.lower() for a in args]
    check(all(a in lower for a in ('-build', '-cook', '-stage', '-package', '-archive', '-pak', '-skipiostore', '-platform=android', '-cookflavor=astc', '-clientconfig=development')), 'Not a real target cook/stage/package command')
    for key, value in config['hostCookOverrides'].items():
        check('-ini:Engine:[/Script/AndroidRuntimeSettings.AndroidRuntimeSettings]:'+key+'='+value in args, 'Missing isolated host-cooker shader override: '+key)
    check(any(a.startswith('-AdditionalCookerOptions=') and '-SkipZenStore' in a and '-CookDir=' in a for a in args), 'Missing loose cook writer/explicit runtime asset directory')
    check(not any(a.startswith(('-skipcook', '-skipstage', '-prepak', '-cookonthefly', '-skipcookonthefly')) for a in lower), 'Forbidden reuse/COTF command')
    check('-CookOutputDir='+str(out/'cooked/Android_ASTC') in args and not any(a.startswith('-stagingdirectory=') for a in lower), 'Cook/stage strategy mismatch')
    check(m['sharedStage']['path'] == str(ROOT/'Saved/StagedBuilds/Android_ASTC') and m['sharedStage']['freshBeforeUAT'] is True and m['sharedStage']['snapshot'] == str(out/'stage/Android_ASTC'), 'Missing fresh shared-stage/snapshot witness')
    log = (out/'uat.txt').read_text(encoding='utf-8-sig', errors='replace')
    for marker in ('COOK COMMAND STARTED', 'COOK COMMAND COMPLETED', 'STAGE COMMAND COMPLETED', 'PACKAGE COMMAND COMPLETED', 'ARCHIVE COMMAND COMPLETED'):
        check(marker in log, 'Missing actual UAT phase: '+marker)
    check(re.search(r'-run=Cook\b', log, re.I) and re.search(r'-TargetPlatform=Android_ASTC\b', log, re.I), 'Missing actual Android ASTC cooker invocation')
    actual_formats = re.findall(r"Shader Library 'Global' \(([^)]+)\) Stats:", log)
    check(actual_formats and all(v == config['cookShaderTarget']+'-'+config['shaderBackend'] for v in actual_formats), 'Cook actually emitted a different/additional global shader platform')
    check('##PlatformValidate: Android VALID r27c' in (out/'sdk-validation.txt').read_text(encoding='utf-8-sig'), 'Actual SDK validation missing/invalid')
    check('21.0.10' in (out/'java-version.txt').read_text(encoding='utf8') and 'Gradle 8.13' in (out/'gradle-version.txt').read_text(encoding='utf8'), 'Wrong JBR/Gradle runtime')
    p = m['package']; verify(p['apk']); verify(p['pak']); verify(p['embeddedObb']); verify(p['developmentRegistry'])
    stage_paks = [b for b in read(out/'stage-files.json') if b['path'].endswith('.pak')]
    check(len(stage_paks) == 1 and stage_paks[0]['sha256'] == p['pak']['sha256'], 'Installed pak differs from this-run staged pak')
    for filename in ('stage-files.json', 'archive-files.json', 'cook-files.json'):
        entries = read(out/filename); check(entries, 'Empty real '+filename)
        for b in entries:
            verify(b)
    for b in p['registryPages']:
        verify(b)
    for b in p['projectConfigs']:
        verify(b)
    project_config = out/'pak-configs/UEFogLab/Config'
    base = ini_values(project_config/'DefaultEngine.ini')
    check(base['/Script/EngineSettings.GameMapsSettings']['GameDefaultMap'] == config['map'] and base['/Script/EngineSettings.GameMapsSettings']['GlobalDefaultGameMode'] == '/Script/FogLabDemo.FogLabGameMode', 'Installed startup map/C++ GameMode config mismatch')
    android = ini_values(project_config/'Android/AndroidEngine.ini')
    settings = android['/Script/AndroidRuntimeSettings.AndroidRuntimeSettings']
    for key, value in dict(config['hostCookOverrides'], PackageName=config['applicationId'], bPackageDataInsideApk='True', bEnableBundle='False', Orientation='Landscape').items():
        check(settings.get(key) == value, 'Installed Android configuration mismatch: '+key)
    check(android['/Script/Engine.RendererSettings']['r.Mobile.ShadingPath'] == '0' and android['/Script/Engine.RendererSettings']['r.MobileHDR'] == 'True', 'Installed Forward/HDR request mismatch')
    for key in ('r.UEFog.Enabled', 'r.Fog', 'r.VolumetricFog', 'r.LocalFogVolume', 'r.SupportLocalFogVolumes'):
        check(android['SystemSettings'][key] == '0', 'Installed baseline fog not disabled: '+key)
    parse_identity((out/'apk-badging.txt').read_text(encoding='utf8'), (out/'apk-manifest.txt').read_text(encoding='utf8'),
                   (out/'apk-signature.txt').read_text(encoding='utf8'), config)
    paths = parse_pak((out/'pak-list.txt').read_text(encoding='utf8'))
    normalized = [x.lstrip('./') for x in paths]
    check(any(x.endswith('Engine/Content/Maps/Entry.umap') for x in normalized), 'Cooked startup map missing from installed pak')
    pages = '\n'.join(Path(b['path']).read_text(encoding='utf-8-sig') for b in p['registryPages'])
    packages, native = closure(dependency_graph(pages), config['cube'])
    required = []
    for package in packages:
        stem = package_path(package)
        matches = [x for x in normalized if x.endswith(stem+'.uasset') or x.endswith(stem+'.umap')]
        check(matches, 'Recursive Cube runtime dependency absent from installed pak: '+package)
        required.append({'package': package, 'entries': matches})
    shaders = [x for x in normalized if 'shaderarchive-' in x.lower() or 'globalshadercache-' in x.lower()]
    check(shaders and all(
        (x.lower().endswith('globalshadercache-'+config['shaderBackend'].lower()+'.bin') if 'globalshadercache-' in x.lower()
         else x.lower().endswith('-'+config['cookShaderTarget'].lower()+'-'+config['shaderBackend'].lower()+'.ushaderbytecode'))
        for x in shaders), 'Wrong/extra cooked shader platform')
    check(any('globalshadercache-' in x.lower() or 'shaderarchive-global-' in x.lower() for x in shaders), 'Mobile global shader library missing')
    check(any('shaderarchive-uefoglab-' in x.lower() for x in shaders), 'Mobile material shader library missing')
    with zipfile.ZipFile(p['apk']['path']) as z:
        members = [n for n in z.namelist() if n.startswith('assets/') and n.endswith('.obb.png')]
        check(len(members) == 1, 'APK lacks cooked embedded content')
        with z.open(members[0]) as stream:
            check(digest(stream) == p['embeddedObb']['sha256'], 'Embedded content differs from audited OBB')
        cmd = z.read(next(n for n in z.namelist() if n.endswith('UECommandLine.txt'))).decode('utf-8-sig').strip()
        check(cmd == p['commandLine'] and m['buildId'] in cmd and m['sourceRevision'] in cmd and '-vulkan' in cmd and '-FogLabEnabled=0' in cmd, 'Packaged startup/build identity mismatch')
    live = current_artifacts(out, m, config, engine, sdk, java)
    checks = ['exact source/config revision', 'pinned environment + actual SDK VALID', 'fresh real cook/stage/package/archive',
              'current single APK + fresh v2 verification + package/SDK/ABI identity', 'APK/OBB/pak complete member hash chain',
              'startup map + recursive Cube material closure', 'only target mobile global/material shader libraries',
              'compiled C++ GameMode markers + frozen startup arguments']
    return {'kind': 'UEFogLab.AndroidBaselineCookAudit', 'schemaVersion': 1, 'passed': True, 'errors': [],
            'candidate': bind(out/'package.json'), 'sourceRevision': m['sourceRevision'], 'buildId': m['buildId'],
            'checks': checks, 'identity': p['identity'], 'installationSet': [p['apk']], 'contentLayout': config['contentLayout'],
            'pakEntries': len(paths), 'requiredAssets': required, 'nativeCodeDependencies': native, 'shaderLibraries': shaders,
            'configuredGameMode': '/Script/FogLabDemo.FogLabGameMode', 'runtimeClassExecuted': 'not_tested',
            'currentArtifactVerification': live,
            'limits': {'installed': False, 'deviceRun': 'not_tested', 'effectiveMobileRenderer': 'not_tested', 'fogAdaptation': 'not_implemented', 'performance': 'not_measured'}}


def build(a):
    check(a.profile == PROFILE, 'Wrong profile: require VulkanMobileForwardHDR')
    out = Path(a.output).resolve()
    check(allowed(out) and not out.exists(), 'Output must be a new repository Saved/work/out directory')
    check(not git('status', '--porcelain'), 'Commit implementation/config before real cook')
    config = read(CONFIG); engine = Path(a.engine).resolve(); sdk = Path(os.environ['ANDROID_HOME']).resolve(); java = Path(os.environ['JAVA_HOME']).resolve()
    check(all(read(engine/'Engine/Build/Build.version').get(k) == v for k, v in config['engine'].items()), 'Engine changed from frozen 5.8.4 candidate')
    check(Path(os.environ['GRADLE_USER_HOME']).resolve() == sdk.parent/'GradleHome' and Path(os.environ['ANDROID_USER_HOME']).resolve() == sdk.parent/'UserHome', 'Select isolated Gradle/Android user directories')
    out.mkdir(parents=True); revision = git('rev-parse', 'HEAD'); build_id = 'M4PRE01-'+revision[:12]+'-'+uuid.uuid4().hex[:12]
    m = {'kind': 'UEFogLab.AndroidBaselineCook', 'schemaVersion': 1, 'passed': False, 'uatCompleted': False,
         'requestedProfile': a.profile, 'profile': config, 'buildId': build_id, 'startedUtc': utc(),
         'sourceRevision': revision, 'sourceWorkingTree': [], 'sourceInputs': inputs(),
         'engineBuild': read(engine/'Engine/Build/Build.version'), 'processEnvironment': {n: os.environ[n] for n in ('ANDROID_HOME', 'NDKROOT', 'JAVA_HOME', 'GRADLE_USER_HOME', 'ANDROID_USER_HOME')},
         'steps': [], 'artifacts': [], 'environmentInputs': [bind(p) for p in (
             engine/'Engine/Build/Build.version', engine/'Engine/Config/Android/Android_SDK.json', java/'release',
             sdk/'ndk/27.2.12479018/source.properties', sdk/'platforms/android-36/package.xml',
             sdk/'build-tools/36.0.0/source.properties', sdk/'cmake/3.22.1/source.properties')]}
    m['sourceArchive'] = []
    for b in m['sourceInputs']:
        relative = Path(b['path']).relative_to(ROOT).as_posix()
        destination = out/'source-inputs'/relative; destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(b['path'], destination)
        m['sourceArchive'].append({'relative': relative, 'file': bind(destination)})
    write(out/'package.json', m)
    try:
        run([engine/'Engine/Build/BatchFiles/Build.bat', '-Mode=ValidatePlatforms', '-OutputSDKs', '-AllPlatforms', '-project='+str(ROOT/'UEFogLab.uproject'), '-log='+str(out/'sdk-validation.log')], out, 'sdk-validation', m['steps'])
        java_text = run([java/'bin/java.exe', '-version'], out, 'java-version', m['steps'])
        check('21.0.10' in java_text, 'Pinned JBR21.0.10 is required')
        gradles = list((sdk.parent/'GradleHome/wrapper/dists/gradle-8.13-all').glob('*/gradle-8.13/bin/gradle.bat'))
        check(len(gradles) == 1, 'Pinned Gradle 8.13 cache missing/ambiguous')
        gradle_text = run([gradles[0], '--version', '--no-daemon'], out, 'gradle-version', m['steps'])
        check('Gradle 8.13' in gradle_text, 'Wrong Gradle runtime')
        print('SDK/JBR/Gradle checked; starting real cook/stage/package: '+build_id, flush=True)
        startup = '-vulkan -FogLabEnabled=0 -FogLabBaselineBuild='+build_id+' -FogLabBaselineSource='+revision
        m['sharedStage'] = prepare_stage(out); write(out/'package.json', m)
        command = [engine/'Engine/Build/BatchFiles/RunUAT.bat', 'BuildCookRun', '-project='+str(ROOT/'UEFogLab.uproject'),
                   '-target=UEFogLab', '-platform=Android', '-clientconfig=Development', '-cookflavor=ASTC',
                   '-build', '-cook', '-stage', '-pak', '-skipiostore', '-package', '-archive',
                   '-archivedirectory='+str(out/'archive'),
                   '-CookOutputDir='+str(out/'cooked/Android_ASTC'), '-map=/Engine/Maps/Entry',
                   '-cmdline='+startup, '-nop4', '-unattended', '-utf8output']
        # Android target settings read host GEngineIni in the Windows cooker.
        # Do not move these Android-only settings into desktop DefaultEngine.ini.
        command += ['-ini:Engine:[/Script/AndroidRuntimeSettings.AndroidRuntimeSettings]:'+key+'='+value
                    for key, value in config['hostCookOverrides'].items()]
        command += ['-AdditionalCookerOptions=-SkipZenStore -CookDir='+str(engine/'Engine/Content/BasicShapes')]
        run(command, out, 'uat', m['steps'], 7200); m['uatCompleted'] = True
        shared_stage = Path(m['sharedStage']['path']); plain_tree(shared_stage)
        shutil.copytree(shared_stage, out/'stage/Android_ASTC')
        write(out/'stage-files.json', inventory(out/'stage'))
        write(out/'archive-files.json', inventory(out/'archive'))
        write(out/'cook-files.json', inventory(out/'cooked'))
        print('UAT exited0; auditing actual APK, embedded content, dependency graph and signatures', flush=True)
        m['package'] = collect(out, engine, sdk, java, config, m['steps'])
        m['artifacts'] = [bind(p) for p in sorted(out.glob('*.txt'))] + [bind(out/n) for n in ('apk-entries.json', 'obb-entries.json', 'pak-entries.json', 'cube-dependencies.json', 'stage-files.json', 'archive-files.json', 'cook-files.json')]
        m.update(passed=True, finishedUtc=utc()); write(out/'package.json', m)
        result = audit(out, engine, sdk, java); write(out/'audit.json', result)
        print('Cooked Android baseline passed: '+str(out/'audit.json'), flush=True)
    except Exception as e:
        m.update(passed=False, error=str(e), finishedUtc=utc()); write(out/'package.json', m)
        write(out/'audit.json', {'kind': 'UEFogLab.AndroidBaselineCookAudit', 'schemaVersion': 1, 'passed': False, 'errors': [str(e)], 'checks': [], 'installationSet': []})
        raise


def main():
    p = argparse.ArgumentParser(description=__doc__); sub = p.add_subparsers(dest='action', required=True)
    b = sub.add_parser('build'); b.add_argument('--profile', default=PROFILE); b.add_argument('--output', required=True); b.add_argument('--engine', default=os.environ.get('UEFOGLAB_ENGINE_ROOT', ''))
    v = sub.add_parser('validate'); v.add_argument('directory'); v.add_argument('--output', required=True)
    v.add_argument('--engine', default=os.environ.get('UEFOGLAB_ENGINE_ROOT', '')); v.add_argument('--sdk', default=os.environ.get('ANDROID_SDK_ROOT', os.environ.get('ANDROID_HOME', ''))); v.add_argument('--java', default=os.environ.get('JAVA_HOME', ''))
    a = p.parse_args()
    try:
        if a.action == 'build':
            build(a)
        else:
            check(allowed(a.output), 'Audit output must be below repository Saved/work/out')
            write(a.output, audit(a.directory, a.engine, a.sdk, a.java)); print('Cook/content/identity audit passed')
        return 0
    except Exception as e:
        if a.action == 'validate' and allowed(a.output):
            write(a.output, {'kind': 'UEFogLab.AndroidBaselineCookAudit', 'schemaVersion': 1, 'passed': False, 'errors': [str(e)], 'checks': [], 'installationSet': []})
        print('REJECTED:', str(e), file=sys.stderr); return 2


if __name__ == '__main__':
    raise SystemExit(main())
