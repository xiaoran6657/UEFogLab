"""Exercise real analysis/launch CLIs using hashed copies of genuine GPU captures."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime
from fog_analysis import read_json


def rehash(root):
    p=root/'run.json';r=read_json(p)
    for item in r['artifacts']:
        data=(root/item['path']).read_bytes();item.update(bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
    p.write_text(json.dumps(r,indent=2)+'\n')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('capture',type=Path);p.add_argument('--suite',required=True,type=Path);p.add_argument('--powershell',default=shutil.which('pwsh'));p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    assert a.powershell,'PowerShell7 required'
    repo=Path(__file__).resolve().parent.parent
    work=repo/'Saved/M2/negatives'/datetime.now().strftime('%Y%m%d-%H%M%S-%f');work.mkdir(parents=True)
    analysis=repo/'tools/fog_m2_analysis.py';checks=[]
    original={x['path']:hashlib.sha256((a.capture/x['path']).read_bytes()).hexdigest() for x in read_json(a.capture/'run.json')['artifacts']}
    faults={
        'schema':lambda r,m:m.update(schemaVersion=1),
        'model':lambda r,m:m.update(modelKey='Prescribed'),
        'missing-g':lambda r,m:m.pop('g'),
        'effective-g':lambda r,m:m.update(g=-.5),
        'zero-direction':lambda r,m:m.update(photonDirectionWorld=[0,0,0]),
        'direction-normalization':lambda r,m:m.update(photonDirectionWorld=[1,1,0]),
        'illegal-g':lambda r,m:m.update(g=.81),
        'mixed-fixed-source':lambda r,m:m.update(sourceFunction=[.35,.6,.9]),
        'requested-normalization':lambda r,m:r.update(photonDirectionRequestedWorld=[0,1,0]),
        'actual-weight':lambda r,m:m.update(incidentWeightRGB=[8,6,4]),
    }
    for name,mutate in faults.items():
        root=work/name;shutil.copytree(a.capture,root)
        r=read_json(root/'run.json');m=read_json(root/'raw/metadata.json');mutate(r,m)
        (root/'run.json').write_text(json.dumps(r)+'\n');(root/'raw/metadata.json').write_text(json.dumps(m)+'\n');rehash(root)
        output=work/(name+'.json');output.write_text('{"passed":true}')
        command=[sys.executable,str(analysis),str(root),'--output',str(output)]
        result=subprocess.run(command,capture_output=True,text=True,timeout=90);data=read_json(output)
        checks.append({'fault':name,'exitCode':result.returncode,'rejected':result.returncode==2 and data.get('passed') is False and bool(data.get('errors')),'reason':data.get('errors'),'command':command})
    # A renamed suite entry with internally matched steps must fail label binding.
    root=work/'step-label';shutil.copytree(a.capture,root)
    r=read_json(root/'run.json');m=read_json(root/'raw/metadata.json');r['steps']=m['steps']=128
    (root/'run.json').write_text(json.dumps(r)+'\n');(root/'raw/metadata.json').write_text(json.dumps(m)+'\n');rehash(root)
    suite=work/'suite';suite.mkdir();s=read_json(a.suite/'suite.json')
    for entry in s['runs']:
        if entry['name']=='forward':entry['path']=str(root)
    (suite/'suite.json').write_text(json.dumps(s)+'\n');output=work/'step-label.json';output.write_text('{"passed":true}')
    result=subprocess.run([sys.executable,str(analysis),str(suite),'--suite','--output',str(output)],capture_output=True,text=True,timeout=90);data=read_json(output)
    checks.append({'fault':'step-label','exitCode':result.returncode,'rejected':result.returncode==2 and data.get('passed') is False and 'label/config' in str(data.get('errors')),'reason':data.get('errors')})
    # M1's real public CLI must reject schema2 rather than silently using fixed J.
    output=work/'old-model.json';output.write_text('{"passed":true}')
    result=subprocess.run([sys.executable,str(repo/'tools/fog_analysis.py'),str(a.capture),'--output',str(output)],capture_output=True,text=True,timeout=90);data=read_json(output)
    checks.append({'fault':'M1-rejects-M2','exitCode':result.returncode,'rejected':result.returncode==2 and data.get('passed') is False,'reason':data.get('errors')})
    script_faults=[('zero-photon',['-PhotonDirection','0,0,0']),('nonfinite-photon',['-PhotonDirection','NaN,1,0']),('short-photon',['-PhotonDirection','1,0']),('invalid-g',['-HGg','0.81']),('nonfinite-g',['-HGg','NaN']),('invalid-albedo',['-Albedo','1.1']),('invalid-weight',['-IncidentWeight','17,0,0']),('prescribed-explicit',['-LightingModel','Prescribed','-HGg','0.5']),('invalid-label',['-Label','../escape'])]
    for name,args in script_faults:
        target=work/('request-'+name)
        command=[a.powershell,'-NoProfile','-File',str(repo/'tools/Readback.ps1'),'-LightingModel','ManualDirectionalHG','-Output',str(target)]+args
        if name=='prescribed-explicit':
            command=[a.powershell,'-NoProfile','-File',str(repo/'tools/Readback.ps1'),'-Output',str(target)]+args
        result=subprocess.run(command,capture_output=True,text=True,timeout=30)
        checks.append({'fault':'request-'+name,'exitCode':result.returncode,'rejected':result.returncode!=0 and not target.exists(),'reason':(result.stderr+result.stdout)[-1600:]})
    unchanged=all(hashlib.sha256((a.capture/name).read_bytes()).hexdigest()==sha for name,sha in original.items())
    data={'schemaVersion':2,'passed':unchanged and all(c['rejected'] for c in checks),'originalArtifactsUnchanged':unchanged,'work':str(work),'checks':checks}
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(data,indent=2)+'\n');print('M2 CLI negatives',len(checks),'passed',data['passed']);return 0 if data['passed'] else 2


if __name__=='__main__':sys.exit(main())
