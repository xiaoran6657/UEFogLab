"""Compare the preregistered full-resolution 256/512/1024 sequence."""
import argparse
import json
from pathlib import Path
import sys
import numpy as np
from fog_analysis import load_capture, read_json, stats, validate


def assert_matched(first,other):
    _,r0,m0,a0=first; _,r1,m1,a1=other
    for key in ('fixture','camera','width','height','originOffset','constrainedAspect'):
        assert r0[key]==r1[key],f'unmatched {key}'
    for key in ('viewMin','viewSize','fogSize','densityMode','extinctionPerMetre','earlyOut','preExposure','sourceFunction','cameraWorldCm','sphereWorldCm','sphereRadiusCm','viewToClip','clipToTranslatedWorld'):
        assert m0[key]==m1[key],f'unmatched {key}'
    assert np.array_equal(a0['scene-data.bin'],a1['scene-data.bin']),'scene depth/exposure changed'
    assert np.array_equal(a0['fog-ray-depth.bin'],a1['fog-ray-depth.bin']),'integration ray mapping changed'
    def sources(r): return {x['path']:x['sha256'] for x in r['sourceInputs'] if x['path'].startswith(('Config/','Plugins/','Source/')) or x['path']=='UEFogLab.uproject'}
    assert sources(r0)==sources(r1),'rendering source drift'


def load_sequence(paths,density):
    frames=[]
    for steps in (256,512,1024):
        frame=load_capture(paths[f'd{density}-s{steps}'])
        _,run,meta,_=frame
        assert meta['steps']==steps and meta['densityMode']==density and meta['downsample']==1,'sequence label/config mismatch'
        assert run['fixture']=='Diagnostic' and run['camera']=='Outside' and run['originOffset']==0
        assert meta['viewSize']==[321,181] and meta['viewMin']==[0,0] and meta['preExposure']==1
        assert abs(meta['extinctionPerMetre']-.35)<1e-6
        frames.append(frame)
    for frame in frames[1:]:assert_matched(frames[0],frame)
    return frames


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('suite',type=Path);p.add_argument('--output',required=True,type=Path);args=p.parse_args()
    entries=read_json(args.suite/'suite.json'); assert entries['stage']=='Convergence'
    paths={x['name']:Path(x['path']) for x in entries['runs']}
    result={'schemaVersion':1,'protocol':'docs/evidence/M1-validation-protocol.md','maxDeltaLimit':5e-4,'meanDeltaLimit':5e-5,'cases':[]}
    for density in (0,1,2):
        frames=load_sequence(paths,density)
        st=[x[3]['integrated-st.bin'] for x in frames]
        d0=stats(st[0]-st[1]);d1=stats(st[1]-st[2]);anchor=validate(frames[-1][0])
        passed=d1['max']<=5e-4 and d1['mean']<=5e-5 and anchor['passed']
        result['cases'].append({'densityMode':density,'passed':passed,'delta256to512':d0,'delta512to1024':d1,'cpuAnchor1024':anchor,'reference':str(frames[-1][0]),'sourceRevisions':[f[1]['sourceRevision'] for f in frames],'runs':[str(f[0]) for f in frames]})
    result['passed']=all(x['passed'] for x in result['cases'])
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n',encoding='utf8')
    for c in result['cases']:print(c['densityMode'],'PASS' if c['passed'] else 'FAIL',c['delta512to1024']['max'],c['delta512to1024']['mean'],'CPU',c['cpuAnchor1024']['ST']['max'])
    return 0 if result['passed'] else 2


if __name__=='__main__':sys.exit(main())
