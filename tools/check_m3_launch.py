"""Exercise comparison launch rejection through real Python and UE CLIs."""
from pathlib import Path
import argparse
import os
import copy
import subprocess
import sys
from m3_compare import ROOT, read, write, request, bind

def main():
    p = argparse.ArgumentParser(); p.add_argument('--output', required=True)
    p.add_argument('--engine', default=os.environ.get('UEFOGLAB_ENGINE_ROOT', '')); a = p.parse_args()
    out = Path(a.output).resolve(); out.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, args in [('unknown-mode', ['--mode','Bad']), ('profile-conflict',['--mode','Off','--profile','High']),
                       ('bad-size',['--mode','Off','--width','0']), ('bad-zero',['--mode','Off','--zero-local'])]:
        cmd = [sys.executable, str(ROOT/'tools/m3_compare.py'), 'run', '--output', str(out/name), *args]
        result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        rows.append({'case':name, 'command':cmd, 'exitCode':result.returncode, 'stderr':result.stderr, 'passed':result.returncode != 0})
    base = request(argparse.Namespace(config=ROOT/'tools/m3_config.json', mode='NativeVF', profile='VFTR', camera='A', width=640, height=360, frames=120, zero_local=False, output=out/'unreachable'))
    faults = [('unknown-mode', {'mode':'Bad'}, ['-FogLabCompareMode=Bad']),
              ('duplicate-mode', {}, ['-FogLabCompareMode=NativeVF', '-FogLabCompareMode=Off']),
              ('legacy-conflict', {}, ['-FogLabCompareMode=NativeVF', '-FogLabEnabled=1']),
              ('bad-density', {'nativeDensity':-1}, ['-FogLabCompareMode=NativeVF']),
              ('bad-size', {'width':0}, ['-FogLabCompareMode=NativeVF']),
              ('nonuniform-scale', {'scaleY':.73}, ['-FogLabCompareMode=NativeVF'])]
    editor = str(Path(a.engine)/'Engine/Binaries/Win64/UnrealEditor-Cmd.exe')
    for name, fields, args in faults:
        case = out/('ue-'+name); case.mkdir(exist_ok=True)
        r = copy.deepcopy(base); r.update(fields); write(case/'request.json', r)
        cmd = [editor, str(ROOT/'UEFogLab.uproject'), '/Engine/Maps/Entry', '-game','-NullRHI','-unattended','-nosound','-NoLiveCoding',
               '-ResX=640','-ResY=360', '-FogLabCompareRequest='+str(case/'request.json'), '-abslog='+str(case/'run.log'), *args]
        with (case/'stdout.txt').open('w', encoding='utf8') as log:
            result = subprocess.run(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=90)
        logtext = (case/'run.log').read_text(encoding='utf-8-sig', errors='replace')
        rows.append({'case':'ue-'+name,'command':cmd,'exitCode':result.returncode,'log':bind(case/'run.log'),
                     'passed':result.returncode != 0 and 'UEFog launch rejected:' in logtext})
    report = {'kind':'UEFogLabComparisonLaunchChecks','schemaVersion':1,'passed':all(r['passed'] for r in rows),'checks':rows}
    write(out/'report.json',report)
    print('Launch checks:',len(rows),'passed:',report['passed'])
    return 0 if report['passed'] else 2

if __name__ == '__main__':
    raise SystemExit(main())
