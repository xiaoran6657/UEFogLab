"""Real reconstruction CLI negatives: hash-correct raw copies and dependency replay."""
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
from fog_analysis import read_json
from check_m2_contract import rehash


def save(path,value):path.write_text(json.dumps(value,indent=2)+'\n',encoding='utf8')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('suite',type=Path);p.add_argument('--convergence',required=True,type=Path);p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    repo=Path(__file__).resolve().parent.parent;work=repo/'Saved/M2/study-negatives'/datetime.now().strftime('%Y%m%d-%H%M%S-%f');work.mkdir(parents=True)
    suite=read_json(a.suite/'suite.json');prior=read_json(a.convergence);conv_suite=read_json(Path(prior['sourceSuite'])/'suite.json')
    originals={}
    for entry in suite['runs']+conv_suite['runs']:
        root=Path(entry['path']);run=read_json(root/'run.json')
        for name in ['run.json']+[x['path'] for x in run['artifacts']]:
            path=root/name;originals[str(path)]=hashlib.sha256(path.read_bytes()).hexdigest()
    def invoke(name,suite_path,convergence_path,negative=True):
        output=work/(name+'.json');save(output,{'passed':True,'groups':['stale success']})
        figures=work/(name+'-figures');command=[sys.executable,str(repo/'tools/fog_m2_study.py'),'reconstruction',str(suite_path),'--convergence',str(convergence_path),'--output',str(output),'--figures',str(figures)]
        run=subprocess.run(command,capture_output=True,text=True,timeout=120);data=read_json(output)
        (work/(name+'.log')).write_text(run.stdout+run.stderr,encoding='utf8')
        passed=(run.returncode==2 and data.get('passed') is False and data.get('groups')==[] and bool(data.get('errors')) and not figures.exists()) if negative else run.returncode==0 and data.get('passed') is True and len(data.get('figures',[]))==4
        return {'fault':name,'passed':passed,'exitCode':run.returncode,'errors':data.get('errors'),'noFigures':not figures.exists(),'command':command}
    positive=invoke('positive',a.suite,a.convergence,False);checks=[]
    assert positive['passed'],'positive baseline failed before negative checks'
    def replace_capture(name,label,mutate):
        folder=work/name;folder.mkdir();s=copy.deepcopy(suite)
        entry=next(e for e in s['runs'] if e['name']==label);root=folder/'capture';shutil.copytree(entry['path'],root)
        mutate(root);rehash(root);entry['path']=str(root);save(folder/'suite.json',s)
        return folder
    def corrupt(channel):
        def mutate(root):
            path=root/'raw/reconstructed-st.bin';v=np.fromfile(path,dtype='<f4').reshape(-1,4)
            if channel=='S':v[:,:3]=0
            else:v[:,3]=1
            v.tofile(path)
        return mutate
    for size in ('640x360','321x181'):
        for channel in ('S','T'):
            name=size+'-reference-'+channel;s=replace_capture(name,size+'-reference',corrupt(channel));c=invoke(name,s,a.convergence)
            c['passed'] &= c.get('errors',[{}])[0].get('code')=='reference_reconstruction_mismatch';checks.append(c)
    s=replace_capture('high-S','321x181-high',corrupt('S'));c=invoke('high-S',s,a.convergence)
    c['passed'] &= c.get('errors',[{}])[0].get('code')=='full_resolution_mismatch';checks.append(c)
    def wrong_steps(root):
        for filename in ('run.json','raw/metadata.json'):
            path=root/filename;record=read_json(path);record['steps']=128;save(path,record)
    s=replace_capture('variant-label','321x181-high',wrong_steps);checks.append(invoke('variant-label',s,a.convergence))
    path=work/'dependency-false.json';bad=copy.deepcopy(prior);bad['passed']=False;save(path,bad);checks.append(invoke('dependency-false',a.suite,path))
    path=work/'dependency-summary.json';bad=copy.deepcopy(prior);bad['cases'][0]['delta512to1024']['scaledS']['max']+=.01;save(path,bad);checks.append(invoke('dependency-summary',a.suite,path))
    for kind,n in (('raw-unconverged',512),('wrong-light',1024)):
        folder=work/('dependency-'+kind);folder.mkdir();s=copy.deepcopy(conv_suite)
        entry=next(e for e in s['runs'] if e['name']==f'radial-forward-s{n}');root=folder/'capture';shutil.copytree(entry['path'],root)
        if kind=='raw-unconverged':
            path=root/'raw/integrated-st.bin';v=np.fromfile(path,dtype='<f2').reshape(-1,4);v[:,3]=1;v.tofile(path)
        else:
            for filename in ('run.json','raw/metadata.json'):
                path=root/filename;record=read_json(path);record['g']=-.5;save(path,record)
        rehash(root);entry['path']=str(root);save(folder/'suite.json',s)
        bad=copy.deepcopy(prior);bad['passed']=True;bad['sourceSuite']=str(folder);path=folder/'convergence.json';save(path,bad)
        checks.append(invoke('dependency-'+kind,a.suite,path))
    unchanged=all(hashlib.sha256(Path(path).read_bytes()).hexdigest()==sha for path,sha in originals.items())
    result={'schemaVersion':2,'passed':positive['passed'] and unchanged and all(c['passed'] for c in checks),'positive':positive,'checks':checks,'originalArtifactsUnchanged':unchanged,'work':str(work)}
    a.output.parent.mkdir(parents=True,exist_ok=True);save(a.output,result)
    print('M2 study negatives',len(checks),'passed',result['passed'])
    for c in checks:print(c['fault'],c['passed'],c['exitCode'])
    return 0 if result['passed'] else 2


if __name__=='__main__':sys.exit(main())
