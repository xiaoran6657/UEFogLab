"""Exercise the reference gate through real CLI calls and fully hashed copies.

No engine/GPU invocation and no modification of archived captures.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
from fog_analysis import load_capture, read_json


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def invoke(repo, suite, convergence, output, figures, log):
    command=[sys.executable,str(repo/'tools/fog_reconstruction.py'),str(suite),
             '--convergence',str(convergence),'--output',str(output),'--figures',str(figures)]
    completed=subprocess.run(command,cwd=repo,capture_output=True,text=True,timeout=120)
    log.write_text(completed.stdout+completed.stderr,encoding='utf8')
    return completed.returncode


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('suite',type=Path)
    parser.add_argument('--convergence',required=True,type=Path)
    parser.add_argument('--work-dir',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    repo=Path(__file__).resolve().parents[1]
    work=args.work_dir.resolve()
    assert any(work.is_relative_to(repo/name) for name in ('Saved','work','out')), 'work-dir must be in repository ignored outputs'
    work.mkdir(parents=True,exist_ok=False)
    suite=read_json(args.suite/'suite.json')
    entries={item['name']:Path(item['path']) for item in suite['runs']}
    sources={size:entries[size+'-reference'] for size in ('640x360','321x181')}
    snapshots={}
    for size,source in sources.items():
        load_capture(source)
        run=read_json(source/'run.json')
        snapshots[size]={item['path']:digest(source/item['path']) for item in run['artifacts']}
        snapshots[size]['run.json']=digest(source/'run.json')

    positive=work/'positive.json'
    positive_exit=invoke(repo,args.suite,args.convergence,positive,work/'positive-figures',work/'positive.log')
    assert positive_exit==0, 'positive CLI failed'
    actual=read_json(positive)
    baseline=read_json(args.suite/'reconstruction.json')
    comparison=copy.deepcopy(actual)
    gates={}
    baseline_groups={group['size']:group for group in baseline['groups']}
    for group in comparison['groups']:
        gates[group['size']]=group['referenceReconstructionSanity']
        # Older R1 baselines predate this additive field; repaired captures use
        # the current full baseline rather than removing a field only on one side.
        if 'referenceReconstructionSanity' not in baseline_groups[group['size']]:
            group.pop('referenceReconstructionSanity')
    positive_unchanged=comparison==baseline
    assert positive_unchanged, 'archived metrics or counterexamples changed'
    assert all(gate['passed'] and gate['metrics']['max']==0 for gate in gates.values())
    figure_matches={p.name:digest(p)==digest(args.suite/'figures'/p.name)
                    for p in (work/'positive-figures').glob('*.png')}
    assert len(figure_matches)==4 and all(figure_matches.values()), 'positive figures changed'

    cases=[]
    for size,mutation in (('640x360','vacuum'),('321x181','vacuum'),
                          ('321x181','scattering-only'),('321x181','transmittance-only')):
        name=size+'-'+mutation
        case=work/name
        raw=case/'capture/raw'
        raw.mkdir(parents=True)
        source=sources[size]
        run=read_json(source/'run.json')
        for item in run['artifacts']:
            destination=case/'capture'/item['path']
            destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source/item['path'],destination)
        data_path=raw/'reconstructed-st.bin'
        modified=np.fromfile(data_path,dtype='<f4').reshape(-1,4)
        if mutation in ('vacuum','scattering-only'): modified[:,:3]=0
        if mutation in ('vacuum','transmittance-only'): modified[:,3]=1
        modified.tofile(data_path)
        for item in run['artifacts']:
            if item['path']=='raw/reconstructed-st.bin':
                item['sha256']=digest(data_path)
                item['bytes']=data_path.stat().st_size
        (case/'capture/run.json').write_text(json.dumps(run,indent=2)+'\n',encoding='utf8')
        # The copy must pass the real loader: rejection must be semantic, not a
        # stale digest, broken format, missing file or substituted loader.
        loaded=load_capture(case/'capture')
        mismatch=float(np.max(abs(loaded[3]['reconstructed-st.bin']-loaded[3]['integrated-st.bin'])))
        assert mismatch>1e-5
        changed_suite=copy.deepcopy(suite)
        for item in changed_suite['runs']:
            if item['name']==size+'-reference': item['path']=str((case/'capture').resolve())
        (case/'suite.json').write_text(json.dumps(changed_suite,indent=2)+'\n',encoding='utf8')
        output=case/'result.json'
        # A failed rerun must also replace a prior successful result.
        output.write_text(json.dumps({'passed':True,'sentinel':'stale success'}),encoding='utf8')
        exit_code=invoke(repo,case,args.convergence,output,case/'figures',case/'cli.log')
        failure=read_json(output)
        rejected=(exit_code!=0 and failure.get('passed') is False and failure.get('groups')==[]
                  and any(error.get('code')=='reference_reconstruction_mismatch' and error.get('size')==size
                          for error in failure.get('errors',[]))
                  and not (case/'figures').exists())
        cases.append({'name':name,'copy':str(case/'capture'),'loaderPassed':True,
                      'maxReferenceMismatch':mismatch,'exitCode':exit_code,
                      'rejected':rejected,'failureReport':str(output),'log':str(case/'cli.log')})

    originals_unchanged=all(digest(sources[size]/name)==sha
                            for size,snapshot in snapshots.items() for name,sha in snapshot.items())
    result={'schemaVersion':1,'passed':originals_unchanged and all(case['rejected'] for case in cases),
            'analysisToolSha256':digest(repo/'tools/fog_reconstruction.py'),
            'checkToolSha256':digest(Path(__file__).resolve()),
            'baselineResultSha256':digest(args.suite/'reconstruction.json'),
            'positive':{'exitCode':positive_exit,'metricsAndCounterexamplesUnchanged':positive_unchanged,
                        'referenceReconstructionSanity':gates,'figureHashesUnchanged':figure_matches,
                        'result':str(positive)},
            'negativeCases':cases,'originalCapturesUnchanged':originals_unchanged}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf8')
    print('positive metrics/figures unchanged; reference gate max=0 at both sizes')
    for case in cases:print(case['name'],'PASS' if case['rejected'] else 'FAIL','CLI exit',case['exitCode'])
    print('original captures unchanged:',originals_unchanged)
    return 0 if result['passed'] else 2


if __name__=='__main__': sys.exit(main())
