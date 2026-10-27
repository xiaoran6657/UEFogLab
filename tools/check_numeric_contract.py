"""Negative semantic/integrity checks, using in-memory manifests; raw runs remain intact."""
import argparse
import copy
import json
from pathlib import Path
import sys
import fog_analysis as f

p=argparse.ArgumentParser(description=__doc__);p.add_argument('run',type=Path);p.add_argument('--output',required=True,type=Path);args=p.parse_args()
original=f.read_json
baseline=original(args.run/'run.json')
assert f.validate(args.run)['passed']
faults={'camera':lambda x:x.update(camera='Inside'), 'fixture':lambda x:x.update(fixture='Diagnostic'), 'offset':lambda x:x.update(originOffset=10000000), 'steps':lambda x:x.update(steps=x['steps']+1), 'artifactHash':lambda x:x['artifacts'][0].update(sha256='0'*64)}
results=[]
for name,mutate in faults.items():
    data=copy.deepcopy(baseline);mutate(data)
    def read(path): return data if Path(path).name=='run.json' else original(path)
    f.read_json=read
    rejected=False;message=''
    try: f.validate(args.run)
    except (AssertionError,KeyError,ValueError,OSError) as error: rejected=True;message=str(error)
    finally:f.read_json=original
    results.append({'fault':name,'rejected':rejected,'reason':message})
result={'passed':all(x['rejected'] for x in results),'run':str(args.run),'checks':results}
args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf8')
print(json.dumps(result,indent=2));sys.exit(0 if result['passed'] else 2)
