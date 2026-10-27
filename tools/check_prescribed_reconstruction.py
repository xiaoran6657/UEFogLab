"""Actual legacy-depth and hash-correct semantic faults through the M1 CLI."""
import argparse
import copy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import numpy as np
from fog_analysis import load_capture,read_json


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path,value):path.write_bytes((json.dumps(value,indent=2)+'\n').encode('utf8'))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('suite',type=Path)
    p.add_argument('--convergence',required=True,type=Path);p.add_argument('--legacy-suite',required=True,type=Path)
    p.add_argument('--legacy-convergence',required=True,type=Path);p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    repo=Path(__file__).resolve().parent.parent
    work=repo/'Saved/M1-FIX-01/negatives'/datetime.now().strftime('%Y%m%d-%H%M%S-%f');work.mkdir(parents=True)
    suite=read_json(a.suite/'suite.json');legacy=read_json(a.legacy_suite/'suite.json')
    legacy_paths={e['name']:Path(e['path']) for e in legacy['runs']}
    originals={}
    for entry in suite['runs']+legacy['runs']:
        source=Path(entry['path']);load_capture(source)
        for file in ['run.json']+[x['path'] for x in read_json(source/'run.json')['artifacts']]:
            path=source/file;originals[str(path)]=digest(path)
    def invoke(name,suite_path,prior,code=None):
        output=work/(name+'.json');save(output,{'passed':True,'groups':['stale success']})
        figures=work/(name+'-figures')
        cmd=[sys.executable,str(repo/'tools/fog_reconstruction.py'),str(suite_path),'--convergence',str(prior),'--output',str(output),'--figures',str(figures)]
        r=subprocess.run(cmd,cwd=repo,capture_output=True,text=True,timeout=150)
        (work/(name+'.log')).write_text(r.stdout+r.stderr,encoding='utf8')
        d=read_json(output)
        good=(r.returncode==0 and d.get('passed') is True and len(list(figures.glob('*.png')))==4) if code is None else (
            r.returncode==2 and d.get('passed') is False and d.get('groups')==[] and not figures.exists()
            and any(e.get('code')==code for e in d.get('errors',[])))
        return {'case':name,'passed':good,'exitCode':r.returncode,'loaderPassed':True,'noFigures':not figures.exists(),
                'errors':d.get('errors'),'command':cmd,'output':str(output)}
    positive=invoke('positive',a.suite,a.convergence);assert positive['passed'],'new positive failed'
    checks=[invoke('legacy-whole-suite',a.legacy_suite,a.legacy_convergence,'canonical_depth_mismatch')]
    def replace(name,label,path):
        folder=work/name;folder.mkdir();s=copy.deepcopy(suite)
        next(e for e in s['runs'] if e['name']==label)['path']=str(path.resolve());save(folder/'suite.json',s)
        return folder
    for suffix in ('medium-bilinear','medium-guided','low-bilinear','low-guided'):
        label='640x360-'+suffix
        path=replace('legacy-'+suffix,label,legacy_paths[label])
        checks.append(invoke('legacy-'+suffix,path,a.convergence,'canonical_depth_mismatch'))
    def faulty(name,label,mutate,code):
        source=Path(next(e['path'] for e in suite['runs'] if e['name']==label));capture=work/(name+'-capture');shutil.copytree(source,capture)
        mutate(capture)
        run=read_json(capture/'run.json')
        for item in run['artifacts']:
            f=capture/item['path'];item.update(bytes=f.stat().st_size,sha256=digest(f))
        save(capture/'run.json',run);load_capture(capture) # prove semantic, not hash/format rejection
        return invoke(name,replace(name+'-suite',label,capture),a.convergence,code)
    def damage_reconstruction(channel):
        def mutate(capture):
            file=capture/'raw/reconstructed-st.bin';v=np.fromfile(file,dtype='<f4').reshape(-1,4)
            if channel=='S':v[:,:3]=0
            else:v[:,3]=1
            v.tofile(file)
        return mutate
    for size in ('640x360','321x181'):
        for channel in ('S','T'):
            checks.append(faulty(size+'-high-'+channel,size+'-high',damage_reconstruction(channel),'full_resolution_mismatch'))
    def damage_ray(capture):
        file=capture/'raw/fog-ray-depth.bin';v=np.fromfile(file,dtype='<f4');v[v<1e7]+=10;v.tofile(file)
    for size in ('640x360','321x181'):
        checks.append(faulty(size+'-bad-ray',size+'-low-guided',damage_ray,'canonical_depth_mismatch'))
    def damage_scene(capture):
        file=capture/'raw/scene-data.bin';v=np.fromfile(file,dtype='<f4').reshape(-1,4);v[v[:,0]>1e-7,1]+=10;v.tofile(file)
    checks.append(faulty('bad-scene-conversion','640x360-medium-guided',damage_scene,'canonical_depth_mismatch'))
    def wrong_label(capture):
        for file in ('run.json','raw/metadata.json'):
            f=capture/file;r=read_json(f);r['steps']=128;save(f,r)
    checks.append(faulty('matched-wrong-high-label','321x181-high',wrong_label,'invalid_reconstruction_input'))
    unchanged=all(digest(Path(path))==sha for path,sha in originals.items())
    result={'passed':unchanged and all(c['passed'] for c in checks),'evidenceType':'real CLI; actual legacy captures and hash-correct modified copies, not new GPU faults',
            'work':str(work),'positive':positive,'checks':checks,'originalCapturesUnchanged':unchanged}
    a.output.parent.mkdir(parents=True,exist_ok=True);save(a.output,result)
    print('Prescribed reconstruction CLI negatives',len(checks),'passed',result['passed'])
    for c in checks:print(c['case'],c['passed'],c['exitCode'])
    return 0 if result['passed'] else 2


if __name__=='__main__':sys.exit(main())
