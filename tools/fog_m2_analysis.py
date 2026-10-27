"""Schema2 GPU validation against the independent manual directional/HG oracle."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import numpy as np
from fog_analysis import load_capture_checked,read_json,rays,stats,decoded
from fog_lighting import validate_lighting,reference,make_lighting,source_function,MODEL,LIGHT_FIELDS


def manual_model(run,meta):
    validate_lighting(run)
    assert 'photonDirectionRequestedWorld' in run,'missing raw requested direction'
    assert 'photonDirectionRequestedWorld' not in meta,'effective snapshot cannot masquerade as a request'
    validate_lighting(meta,run)


def load_capture(path):
    return load_capture_checked(path,manual_model)


def rendering_sources(run):
    return {x['path']:x['sha256'] for x in run['sourceInputs']
            if x['path'].startswith(('Config/','Plugins/','Source/')) or x['path']=='UEFogLab.uproject'}


def validate(path):
    frame=load_capture(path);root,run,m,a=frame;d,low,geom_low,depth=decoded(frame)
    ref,length,j=reference(m,d,low);geometry_ref,_,_=reference(m,d,geom_low)
    observed=a['integrated-st.bin'];error=observed-ref;scale=np.maximum(1,np.max(j,axis=-1))[...,None]
    limit=[8e-4,1.2e-3,2.5e-3][m['densityMode']]
    scaled=error[...,:3]/scale;identity=(observed[...,:3]-(1-observed[...,3,None])*j)/scale
    bounds=bool(np.all((observed[...,3]>=0)&(observed[...,3]<=1)) and np.all(observed[...,:3]>=0) and np.all(observed[...,:3]<=j+limit*scale))
    vacuum=bool(np.array_equal(observed,np.broadcast_to([0,0,0,1],observed.shape))) if m['extinctionPerMetre']==0 else None
    zero=bool(np.all(observed[...,:3]==0)) if m['albedo']==0 or np.all(np.array(m['incidentWeightRGB'])==0) else None
    quad=0.
    if m['densityMode']==2:
        ref128,_,_=reference(m,d,low,128);delta=ref128-ref
        quad=float(max(np.max(abs(delta[...,3])),np.max(abs(delta[...,:3]/scale))))
    optical=bool(np.max(abs(error[...,3]))<=limit and np.max(abs(scaled))<=limit and np.max(abs(identity))<=limit and bounds and depth['conversionBad']==0 and depth['decodedLowBad']==0 and quad<=1e-10 and vacuum is not False and zero is not False)
    geometry=all(depth[k]==0 for k in ('skyMismatch','geometryBad','lowGeometryBad'))
    return {'run':str(root),'sourceRevision':run['sourceRevision'],'modelKey':MODEL,
            'parameters':{k:m[k] for k in LIGHT_FIELDS+('steps','densityMode','downsample')},
            'viewMin':m['viewMin'],'viewSize':m['viewSize'],'fogSize':m['fogSize'],'preExposure':m['preExposure'],
            'passed':optical and (geometry or run['fixture']=='Diagnostic'),'opticalPassed':optical,'strictGeometryPassed':geometry,
            'depthScope':'Diagnostic optical acceptance conditional on independently decoded device Z; ideal geometry failures reported' if run['fixture']=='Diagnostic' else 'analytic geometry and decoded device Z both required',
            'tolerance':limit,'S':stats(error[...,:3]),'scaledS':stats(scaled),'T':stats(error[...,3]),'geometryReferencedST':stats(observed-geometry_ref),
            'mediumS':stats(error[...,:3],length>0),'mediumScaledS':stats(scaled,length>0),'mediumT':stats(error[...,3],length>0),
            'relativeErrorFloor1e-4':stats(error/np.maximum(abs(ref),1e-4)),
            'identityScaledMax':float(np.max(abs(identity))),'bounds':bounds,'vacuumExact':vacuum,'zeroScatteringExact':zero,'cpu64vs128ScaledMax':quad,'depth':depth}


def case_request(case, defaults):
    c=dict(defaults,**case.get('overrides',{}))
    return c,make_lighting(c['Albedo'],c['HGg'],[float(x) for x in c['IncidentWeight'].split(',')],[float(x) for x in c['PhotonDirection'].split(',')])


def check_case(frame,case,defaults):
    c,light=case_request(case,defaults);_,run,m,_=frame
    validate_lighting(run,light)
    for key,name in (('Width','width'),('Height','height'),('Steps','steps'),('DensityMode','densityMode'),('Fixture','fixture'),('Camera','camera')):
        assert run[name]==c[key],f'case label/config mismatch: {case["id"]} {name}'
    assert abs(run['extinctionPerMetre']-c['Extinction'])<1e-6,'case extinction mismatch'
    assert run['preset']==c['Preset'],'case preset mismatch'
    assert run['downsample']==(2 if c['Preset'] in ('Medium','Low') else 1),'case divisor mismatch'
    assert run['originOffset']==c.get('OriginOffset',0),'case origin mismatch'
    assert run['preExposureRequested']==c.get('PreExposure',1),'case exposure mismatch'
    assert run['constrainedAspect']==0 and run['reconstruction']==1,'case observation mismatch'


def validate_suite(suite):
    s=read_json(Path(suite)/'suite.json');matrix=read_json(Path(__file__).with_name('m2_cases.json'))
    assert s['schemaVersion']==2 and s['modelKey']==MODEL and s['stage']=='Validation','wrong M2 validation suite'
    paths={x['name']:x['path'] for x in s['runs']}
    assert len(paths)==len(s['runs']) and set(paths)=={c['id'] for c in matrix['validation']},'missing/duplicate/unknown case labels'
    frames={name:load_capture(path) for name,path in paths.items()}
    assert all(rendering_sources(f[1])==rendering_sources(frames['forward'][1]) for f in frames.values()),'rendering source drift in validation matrix'
    for case in matrix['validation']:check_case(frames[case['id']],case,matrix['defaults'])
    results=[validate(paths[c['id']]) for c in matrix['validation']]
    pairs=[];base=frames['forward'];b=base[3]['integrated-st.bin']
    for name in ('isotropic','backward','reverse-direction','double-light','half-albedo','zero-light','zero-albedo','unit-albedo','exposure','high-forward'):
        other=frames[name];o=other[3]['integrated-st.bin']
        assert np.array_equal(base[3]['scene-data.bin'][...,:2],other[3]['scene-data.bin'][...,:2]),'paired depth changed'
        assert np.array_equal(base[3]['fog-ray-depth.bin'],other[3]['fog-ray-depth.bin']),'paired mapping changed'
        item={'case':name,'TByteIdentical':bool(np.array_equal(b[...,3],o[...,3]))}
        if name in ('double-light','half-albedo'):
            factor=2 if name=='double-light' else .5
            j=source_function(rays(other[2],other[2]['fogSize']),other[2]);scale=np.maximum(1,np.max(j,axis=-1))[...,None]
            item['scaledSPrediction']=stats((o[...,:3]-b[...,:3]*factor)/scale)
        item['passed']=item['TByteIdentical'] and item.get('scaledSPrediction',{'max':0})['max']<=1.2e-3
        pairs.append(item)
    wrong=[]
    for name in ('forward','backward'):
        f=frames[name];d,low,_,_=decoded(f);_,_,j=reference(f[2],d,low);observed=f[3]['integrated-st.bin'];scale=np.maximum(1,np.max(j,axis=-1))[...,None]
        for kind in ('HG-sign-flipped','missing-4pi'):
            m=dict(f[2])
            if kind=='HG-sign-flipped':m['g']=-m['g']
            ref,_,_=reference(m,d,low)
            if kind=='missing-4pi':ref[...,:3]*=4*np.pi
            error=stats((observed[...,:3]-ref[...,:3])/scale)
            wrong.append({'case':name,'wrongExpectedModel':kind,'scaledS':error,'rejected':error['max']>1.2e-3})
    return {'schemaVersion':2,'modelKey':MODEL,'passed':all(x['passed'] for x in results+pairs) and all(x['rejected'] for x in wrong),'runs':results,'pairedPredictions':pairs,'wrongExpectedModelNegatives':wrong}


def write_result(path,result):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n',encoding='utf8')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('path',type=Path);p.add_argument('--suite',action='store_true');p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    try:result=validate_suite(a.path) if a.suite else {'schemaVersion':2,'modelKey':MODEL,**validate(a.path)}
    except (AssertionError,ValueError,TypeError,KeyError,OSError,IndexError) as e:result={'schemaVersion':2,'modelKey':MODEL,'passed':False,'errors':[{'message':str(e)}]}
    write_result(a.output,result);print('M2 numerical passed',result['passed'])
    for r in result.get('runs',[]):print(Path(r['run']).name,r['passed'],r['S']['max'],r['scaledS']['max'],r['T']['max'])
    for e in result.get('errors',[]):print(e,file=sys.stderr)
    return 0 if result['passed'] else 2


if __name__=='__main__':sys.exit(main())
