"""CPU-only checks of HG, direction signs, integration and input contract."""
import argparse
import copy
import json
import math
from pathlib import Path
import sys
import numpy as np
from fog_lighting import (hg_phase,hg_pbrt_outward,integrate_tau,make_lighting,
                          normalize_photon,source_function,validate_lighting,reference)


def check():
    checks=[]
    def record(name,error,limit):
        error=float(error);checks.append({'name':name,'maxError':error,'limit':limit,'passed':error<=limit})
    angles=np.linspace(-1,1,401)
    record('isotropic',np.max(abs(hg_phase(angles,0)-1/(4*math.pi))),1e-12)
    record('forward-known',abs(hg_phase(1,.5)-3/(2*math.pi)),1e-12)
    record('backward-known',abs(hg_phase(-1,.5)-1/(18*math.pi)),1e-12)
    record('known-ratio-27',abs(hg_phase(1,.5)/hg_phase(-1,.5)-27),1e-12)
    x,w=np.polynomial.legendre.leggauss(256)
    sx=np.linspace(-1,1,65537);sw=np.ones_like(sx);sw[1:-1:2]=4;sw[2:-1:2]=2
    step=2/65536
    light=np.array([1.,0,0]);outward=np.stack((angles,np.sqrt(1-angles**2),np.zeros_like(angles)),axis=-1)
    for g in (0,.5,-.5,.8,-.8):
        phase=hg_phase(angles,g)
        if not np.isfinite(phase).all() or np.any(phase<0): raise AssertionError('invalid phase')
        record(f'symmetry-{g}',np.max(abs(phase-hg_phase(-angles,-g))),1e-12)
        record(f'pbrt-direction-{g}',np.max(abs(phase-hg_pbrt_outward(-light,outward,g))),1e-12)
        integral=2*math.pi*np.dot(w,hg_phase(x,g));moment=2*math.pi*np.dot(w,x*hg_phase(x,g))
        si=2*math.pi*step/3*np.dot(sw,hg_phase(sx,g));sm=2*math.pi*step/3*np.dot(sw,sx*hg_phase(sx,g))
        record(f'normalization-gauss256-{g}',abs(integral-1),1e-9)
        record(f'moment-gauss256-{g}',abs(moment-g),1e-9)
        record(f'normalization-simpson65536-{g}',abs(si-1),1e-9)
        record(f'moment-simpson65536-{g}',abs(sm-g),1e-9)
    rays=np.array([[-1.,0,0],[0,1.,0],[1.,0,0]])
    lighting=make_lighting(photon=(1.,0,0))
    j=source_function(rays,lighting)
    record('source-forward-backward-27',abs(j[0,0]/j[2,0]-27),1e-12)
    reversed_light=make_lighting(photon=(-1.,0,0))
    record('photon-flip',np.max(abs(j-source_function(-rays,reversed_light))),1e-12)
    optical=np.array([.2,.7,1.1]);tau=np.full(3,optical.sum())
    closed=integrate_tau(tau,rays,lighting)
    s=np.zeros((3,3));t=np.ones(3)
    for part in optical:
        s+=t[:,None]*j*(-math.expm1(-part));t*=math.exp(-part)
    record('segments',np.max(abs(closed-np.concatenate((s,t[:,None]),axis=-1))),1e-12)
    double=integrate_tau(tau,rays,make_lighting(incident=(8.,6.,4.),photon=(1.,0,0)))
    record('light-linear-S',np.max(abs(double[:,:3]-closed[:,:3]*2)),1e-12)
    record('light-independent-T',np.max(abs(double[:,3]-closed[:,3])),0)
    for albedo in (0,.4,1):
        value=integrate_tau(tau,rays,make_lighting(albedo=albedo,photon=(1.,0,0)))
        record(f'albedo-S-{albedo}',np.max(abs(value[:,:3]-closed[:,:3]*albedo/.8)),1e-12)
        record(f'albedo-T-{albedo}',np.max(abs(value[:,3]-closed[:,3])),0)
    zero_light=integrate_tau(tau,rays,make_lighting(incident=(0,0,0)))
    record('zero-light',np.max(abs(zero_light[:,:3])),0)
    vacuum=integrate_tau(np.zeros(3),rays,lighting)
    record('vacuum',np.max(abs(vacuum-np.broadcast_to([0,0,0,1],vacuum.shape))),0)
    for g in (-.8,.8):
        value=integrate_tau(tau,rays,make_lighting(g=g))
        record(f'g-independent-T-{g}',np.max(abs(value[:,3]-closed[:,3])),0)
    record('tau-not-linear',abs(math.exp(-2)-(math.exp(-1)**2)),1e-12)
    meta=dict(lighting,cameraWorldCm=[-200,0,0],sphereWorldCm=[0,0,0],sphereRadiusCm=100,
              extinctionPerMetre=.5,densityMode=0)
    sphere,_,_=reference(meta,np.array([[[1.,0,0]]]),np.array([[1e8]]))
    record('sphere-analytic-T',abs(sphere[0,0,3]-math.exp(-1)),1e-12)
    record('robust-direction-large',np.max(abs(normalize_photon((1e308,-1e308,0))-np.array([1,-1,0])/math.sqrt(2))),1e-12)
    record('robust-direction-small',np.max(abs(normalize_photon((1e-308,-1e-308,0))-np.array([1,-1,0])/math.sqrt(2))),1e-12)
    bad_inputs=[lambda:make_lighting(g=.81),lambda:make_lighting(g=float('nan')),
                lambda:make_lighting(g=float(np.float32(.8))),lambda:make_lighting(g=-float(np.float32(.8))),
                lambda:make_lighting(albedo=-.1),lambda:make_lighting(albedo=1.1),
                lambda:make_lighting(incident=(17,1,1)),lambda:make_lighting(incident=(-1,0,0)),
                lambda:make_lighting(incident=(1,float('inf'),1)),lambda:make_lighting(photon=(0,0,0)),
                lambda:make_lighting(photon=(1,float('nan'),0)),lambda:make_lighting(photon=(1,0))]
    invalid=[]
    for index,run in enumerate(bad_inputs):
        try:run();rejected=False
        except (ValueError,TypeError):rejected=True
        invalid.append({'input':index,'rejected':rejected})
    actual=copy.deepcopy(lighting);actual.pop('photonDirectionRequestedWorld')
    validate_lighting(actual,lighting)
    faults={'schema':lambda r:r.update(schemaVersion=1),'model':lambda r:r.update(modelKey='Prescribed'),
            'missing-g':lambda r:r.pop('g'),'effective-g':lambda r:r.update(g=-.5),
            'direction-length':lambda r:r.update(photonDirectionWorld=[2,0,0]),
            'fixed-source':lambda r:r.update(sourceFunction=[.35,.6,.9])}
    semantic=[]
    for sign in (-1,1):
        requested=make_lighting(g=sign*.8,photon=(1,0,0))
        endpoint=copy.deepcopy(requested);endpoint.pop('photonDirectionRequestedWorld')
        endpoint['g']=float(np.float32(sign*.8))
        validate_lighting(endpoint,requested)
        record(f'effective-float32-endpoint-{sign}',
               np.max(abs(source_function(rays,endpoint)-endpoint['albedo']*np.asarray(endpoint['incidentWeightRGB'])*
                          hg_pbrt_outward(-np.array([1,0,0]),-rays,endpoint['g'])[:,None])),1e-12)
        beyond=copy.deepcopy(endpoint)
        beyond['g']=sign*float(np.nextafter(np.float32(.8),np.float32(np.inf)))
        try:validate_lighting(beyond,requested);rejected=False
        except ValueError:rejected=True
        semantic.append({'fault':f'effective-g-beyond-float32-endpoint-{sign}','rejected':rejected})
    for name,mutate in faults.items():
        bad=copy.deepcopy(actual);mutate(bad)
        try:validate_lighting(bad,lighting);rejected=False
        except (ValueError,KeyError):rejected=True
        semantic.append({'fault':name,'rejected':rejected})
    # These are deliberately wrong expected models, not modified GPU evidence.
    mutations={'HG-sign-flipped':abs(hg_phase(-1,.5)-3/(2*math.pi)),
               'missing-4pi':abs(hg_phase(1,.5)*4*math.pi-3/(2*math.pi)),
               'wrong-normalization':abs(2*math.pi*np.dot(w,2*hg_phase(x,.5))-1)}
    negative=[{'model':name,'error':float(error),'rejected':bool(error>1e-9)} for name,error in mutations.items()]
    passed=all(c['passed'] for c in checks) and all(x['rejected'] for x in invalid+semantic+negative)
    return {'schemaVersion':1,'passed':passed,'evidenceType':'CPU mathematics and input contract; no GPU',
            'angularIntegration':{'gaussNodes':256,'simpsonIntervals':65536},'checks':checks,
            'invalidInputs':invalid,'semanticFaults':semantic,'wrongModelNegatives':negative}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    result=check();a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n',encoding='utf8')
    print('CPU checks',len(result['checks']),'passed',result['passed'])
    for c in result['checks']:
        if not c['passed']: print(c)
    sys.exit(0 if result['passed'] else 2)
