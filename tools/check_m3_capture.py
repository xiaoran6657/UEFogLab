"""Real CLI faults for M3 capture integrity and provenance gates."""
import argparse
import copy
from pathlib import Path
import shutil
import subprocess
import sys
from m3_compare import ROOT, read, write, bind, validate_capture

def main():
    p = argparse.ArgumentParser(); p.add_argument('capture'); p.add_argument('--output', required=True); a = p.parse_args()
    validate_capture(a.capture)
    out = Path(a.output).resolve(); out.mkdir(parents=True, exist_ok=True)
    rows = []
    for name in ('missing-artifact', 'request-mismatch', 'fake-revision', 'config-light-mismatch'):
        case = out/name
        shutil.copytree(a.capture, case)
        m = read(case/'capture.json')
        m['artifacts'] = [bind(case/Path(b['path']).name) for b in m['artifacts']]
        write(case/'capture.json', m)
        validate_capture(case)  # Each relocated control is valid before mutation.
        if name == 'missing-artifact':
            m['artifacts'] = [b for b in m['artifacts'] if Path(b['path']).name != 'frame.png']
        elif name == 'request-mismatch':
            m['request'] = copy.deepcopy(m['request']); m['request']['frames'] += 1
        elif name == 'fake-revision':
            m['sourceRevision'] = '0'*40
        else:
            c = read(m['config']['path']); c['sunIntensity'] = 42
            write(case/'modified-config.json', c); m['config'] = bind(case/'modified-config.json')
        write(case/'capture.json', m)
        cmd = [sys.executable, str(ROOT/'tools/m3_compare.py'), 'validate', str(case)]
        result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        rows.append({'case':name, 'command':cmd, 'exitCode':result.returncode, 'stderr':result.stderr, 'passed':result.returncode != 0})
    report = {'kind':'UEFogLabComparisonCaptureChecks', 'schemaVersion':1, 'passed':all(x['passed'] for x in rows), 'checks':rows}
    write(out/'report.json', report)
    print('Capture checks:',len(rows),'passed:',report['passed'])
    return 0 if report['passed'] else 2

if __name__ == '__main__':
    raise SystemExit(main())
