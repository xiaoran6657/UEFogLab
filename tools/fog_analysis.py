"""Independent float64 optical-depth reference and capture integrity checks.

Requires numpy. Never reads screenshots; never calls the engine or copies its types.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
import numpy as np
from fog_sampling import scene_indices

J = np.array([.35, .60, .90], dtype=np.float64)
SKY = 1e8


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def load_capture_checked(path, check_model):
    """Shared raw integrity/geometry loader; callers must supply a model gate."""
    root = Path(path).resolve()
    run = read_json(root/'run.json')
    meta = read_json(root/'raw/metadata.json')
    assert run['status'] == meta['status'] == 'complete', 'incomplete capture'
    check_model(run,meta)
    assert run['exitCode'] == 0 and run['sourceRevision'], 'missing successful source identity'
    assert meta['captureViewIndex'] == run['frame'], 'frame mismatch'
    for name in ('steps','densityMode','downsample','reconstruction'):
        assert run[name] == meta[name], f'configuration mismatch: {name}'
    assert abs(run['extinctionPerMetre']-meta['extinctionPerMetre']) < 1e-6
    assert run['fixture'] == meta['fixtureKey'] and run['camera'] == meta['cameraMode'], 'fixture/camera mismatch'
    assert run['originOffset'] == meta['originOffsetCm'], 'world offset mismatch'
    eye=np.array([-850.,-950.,310.]);target=np.array([280.,0.,145.])
    if run['camera']=='Inside': eye=np.array([300.,0.,180.]);target=np.array([800.,100.,190.])
    elif run['camera']=='Oblique': eye=np.array([-350.,-950.,600.]);target=np.array([300.,0.,180.])
    else: assert run['camera']=='Outside'
    if run['fixture']=='Occluded': eye=np.array([-850.,0.,180.]);target=np.array([300.,0.,180.])
    offset=np.array([run['originOffset'],0,0])
    assert np.max(abs(np.array(meta['cameraWorldCm'])-eye-offset)) < 1e-4, 'actual camera position mismatch'
    assert np.max(abs(np.array(meta['sphereWorldCm'])-np.array([300.,0.,180.])-offset)) < 1e-4, 'actual sphere mismatch'
    forward=(target-eye)/np.linalg.norm(target-eye)
    assert np.max(abs(np.array(meta['cameraForward'])-forward)) < 1e-6, 'actual camera direction mismatch'
    assert meta['sphereRadiusCm']==360, 'sphere mismatch'
    assert meta['earlyOut'] == 0 and not meta['scatteringPreExposed']
    manifest = {a['path']: a for a in run['artifacts']}
    for name, a in manifest.items():
        file = (root/name).resolve()
        assert file.is_relative_to(root), 'artifact traversal'
        data = file.read_bytes()
        assert len(data) == a['bytes'] and hashlib.sha256(data).hexdigest() == a['sha256'], f'hash/size mismatch: {name}'
    assert 'raw/metadata.json' in manifest
    sizes = {'integrated-st.bin':(meta['fogSize'],4,'<f2'), 'fog-ray-depth.bin':(meta['fogSize'],1,'<f4'), 'scene-data.bin':(meta['viewSize'],4,'<f4')}
    arrays = {}
    for file in meta['files']:
        name = file['file']
        expected = sizes.get(name, (meta['viewSize'],4,'<f4') if name == 'reconstructed-st.bin' else None)
        assert expected is not None, f'unknown texture {name}'
        size, channels, dtype = expected
        assert [file['width'],file['height']] == size and file['channels'] == channels and file['dtype'] == dtype, 'data layout mismatch'
        assert file['sourceRowPitchPixels'] >= size[0] and file['sourceBufferHeight'] >= size[1]
        assert 'raw/'+name in manifest, 'unhashed texture'
        array = np.fromfile(root/'raw'/name,dtype=dtype).reshape(size[1],size[0],channels).astype(np.float64)
        assert array.nbytes//8*np.dtype(dtype).itemsize == file['bytes'] and np.isfinite(array).all()
        assert name not in arrays, 'duplicate texture'
        arrays[name] = array
    assert set(sizes) <= arrays.keys(), 'missing texture'
    view = np.array(meta['viewSize'])
    assert np.all(np.array(meta['fogSize']) == (view+meta['downsample']-1)//meta['downsample'])
    assert np.all(np.array(meta['viewMin']) >= 0) and np.all(np.array(meta['viewMin'])+view <= meta['sceneExtent'])
    assert np.all(view <= [run['width'], run['height']]), 'view larger than requested scene'
    assert np.max(np.abs(arrays['scene-data.bin'][...,2]-meta['preExposure'])) < 1e-7
    assert abs(meta['preExposure']-run['preExposureRequested']) < 1e-5, 'pre-exposure request not effective'
    return root,run,meta,arrays


def prescribed_model(run,meta):
    assert run['schemaVersion']==meta['schemaVersion']==1, 'M1 requires Prescribed/schema1'
    assert meta['sourceFunction']==[.35,.6,.9], 'prescribed source mismatch'
    for record in (run,meta):
        assert record.get('modelKey','Prescribed')=='Prescribed', 'M1 model mismatch'
        assert not {'lightingInput','directionConvention','albedo','g','incidentWeightRGB','photonDirectionWorld'} & record.keys(), 'M1 rejects manual lighting fields'


def load_capture(path):
    return load_capture_checked(path,prescribed_model)


def rays(meta, size):
    w,h = size
    u,v = np.meshgrid((np.arange(w)+.5)/w,(np.arange(h)+.5)/h)
    p = np.array(meta['viewToClip']).reshape(4,4)
    assert abs(p[2,3]-1) < 1e-10 and abs(p[3,3]) < 1e-10, 'perspective only'
    right,up,forward = [np.array(meta[k]) for k in ('cameraRight','cameraUp','cameraForward')]
    d = forward + ((2*u-1-p[2,0])/p[0,0])[...,None]*right + ((1-2*v-p[2,1])/p[1,1])[...,None]*up
    d /= np.linalg.norm(d,axis=-1,keepdims=True)
    return d


def boxes(run):
    if run['fixture'] == 'Empty': return []
    if run['fixture'] == 'Occluded': return [(np.array([-250,0,180]),np.array([.2,30,30])*50)]
    assert run['fixture'] == 'Diagnostic'
    return [(np.array([200,0,-35]),np.array([24,20,.5])*50), (np.array([900,0,280]),np.array([.3,18,6])*50)] + [(np.array([250,i*190,150]),np.array([.6,.6,3])*50) for i in range(-3,4)] + [(np.array([-150,i*85,125]),np.array([.06,.06,2.5])*50) for i in range(-2,3)]


def geometry_depth(run, meta, d):
    # Work relative to the large world offset, independent of shader translated coordinates.
    eye = np.array(meta['cameraWorldCm'])-np.array([run['originOffset'],0,0])
    nearest = np.full(d.shape[:-1],SKY)
    for centre,half in boxes(run):
        with np.errstate(divide='ignore',invalid='ignore'):
            a,b = (centre-half-eye)/d,(centre+half-eye)/d
        enter = np.max(np.minimum(a,b),axis=-1)
        leave = np.min(np.maximum(a,b),axis=-1)
        hit = (leave >= np.maximum(enter,0)) & (enter > 0)
        nearest = np.minimum(nearest,np.where(hit,enter,SKY))
    return nearest


def sphere_segment(meta, d, depth):
    o = np.array(meta['cameraWorldCm'])-np.array(meta['sphereWorldCm'])
    b = np.sum(o*d,axis=-1)
    disc = b*b - (o@o-meta['sphereRadiusCm']**2)
    root = np.sqrt(np.maximum(disc,0))
    begin,end = np.maximum(0,-b-root),np.minimum(depth,-b+root)
    length = np.where((disc > 0)&(end > begin),end-begin,0)
    return o,begin,length


def reference(meta, d, depth, nodes=64):
    o,begin,length = sphere_segment(meta,d,depth)
    tau = meta['extinctionPerMetre'] * length * .01
    mode = meta['densityMode']
    if mode:
        x,w = np.polynomial.legendre.leggauss(8 if mode == 1 else nodes)
        integral = np.zeros_like(length)
        for node,weight in zip(x,w):
            t = begin + (node+1)*length/2
            p = (o+d*t[...,None])/meta['sphereRadiusCm']
            density = np.maximum(0,1-np.sum(p*p,axis=-1))**2
            if mode == 2: density *= .65+.35*np.sin(13*p[...,0])*np.sin(17*p[...,1])*np.sin(11*p[...,2])
            integral += weight*density
        tau *= integral/2
    t = np.exp(-tau)
    return np.concatenate((-np.expm1(-tau)[...,None]*J,t[...,None]),axis=-1), length


def stats(error,mask=None):
    a = np.asarray(error)
    if a.ndim == 2: a=a[...,None]
    selected = a if mask is None else a[mask]
    if not selected.size: return {'pixels':0}
    absolute = np.abs(selected)
    pixel = np.max(np.abs(a),axis=-1)
    where = np.unravel_index(np.argmax(np.where(mask,pixel,-1) if mask is not None else pixel),pixel.shape)
    return {'pixels':int(selected.size/a.shape[-1]),'max':float(absolute.max()),'mean':float(absolute.mean()),'rms':float(np.sqrt(np.mean(selected**2))), 'p95':float(np.percentile(absolute,95)), 'p99':float(np.percentile(absolute,99)), 'worstXY':[int(where[1]),int(where[0])]}


def cpu_checks():
    t = np.exp(-1.75)
    assert abs(t-np.exp(-.5)*np.exp(-1.25)) < 1e-12
    a,b = -np.expm1(-.5)*J, -np.expm1(-1.25)*J
    assert np.max(abs(a+np.exp(-.5)*b-J*(1-t))) < 1e-12
    x,w = np.polynomial.legendre.leggauss(8)
    assert abs(np.dot(w,(1-x*x)**2)-16/15) < 1e-12
    # Exercise actual reference functions on hand-computed 2m sphere chords.
    meta={'cameraWorldCm':[-200,0,0],'sphereWorldCm':[0,0,0],'sphereRadiusCm':100,'extinctionPerMetre':.5,'densityMode':0}
    direction=np.array([[[1.,0,0]]]);depth=np.array([[SKY]])
    uniform,length=reference(meta,direction,depth)
    assert abs(length.item()-200)<1e-12 and abs(uniform[0,0,3]-np.exp(-1))<1e-12
    clipped,_=reference(meta,direction,np.array([[150.]]))
    assert abs(clipped[0,0,3]-np.exp(-.25))<1e-12
    meta['densityMode']=1
    radial,_=reference(meta,direction,depth)
    assert abs(radial[0,0,3]-np.exp(-.5*16/15))<1e-12
    meta['cameraWorldCm']=[0,0,0];meta['densityMode']=0
    inside,inside_length=reference(meta,direction,depth)
    assert abs(inside_length.item()-100)<1e-12 and abs(inside[0,0,3]-np.exp(-.5))<1e-12
    meta['cameraWorldCm']=[-200,100,0]
    tangent,tangent_length=reference(meta,direction,depth)
    assert tangent_length.item()==0 and tangent[0,0,3]==1
    return {'knownTau':1.75,'T':float(t),'segmentComposition':'pass','actualReferenceFixtures':'uniform full chord, clipped chord, radial chord, inside, tangent; <1e-12','radialChordIntegral':'16/15, agreement <1e-12','vacuum':'exp(0)=1, -expm1(0)=0'}


def decoded(frame, *, include_extrema=False):
    """Independently decode actual DeviceZ; no optical model or GPU ray-depth oracle."""
    _,run,m,a=frame
    full=rays(m,m['viewSize']);geom=geometry_depth(run,m,full);scene=a['scene-data.bin']
    sky=scene[...,0]<=1e-7;analytic_sky=geom==SKY;surface=~sky&~analytic_sky
    depth_error=abs(scene[...,1]-geom)
    p=np.array(m['viewToClip']).reshape(4,4);cf=full@np.array(m['cameraForward'])
    z=np.divide(p[3,2],scene[...,0]-p[2,2],out=np.full_like(geom,SKY),where=~sky)
    converted=np.where(sky,SKY,z/cf)
    conversion_error=abs(converted-scene[...,1])
    ix,iy=scene_indices(m)
    d=rays(m,m['fogSize']);cl=d@np.array(m['cameraForward'])
    low=np.where(sky[iy,ix],SKY,z[iy,ix]/cl)
    geom_low=np.where(geom[iy,ix]==SKY,SKY,geom[iy,ix]*cf[iy,ix]/cl)
    low_error=abs(a['fog-ray-depth.bin'][...,0]-geom_low)
    depth={'skyMismatch':int(np.sum(sky!=analytic_sky)),
           'geometryBad':int(np.sum(surface&(depth_error>np.maximum(.05,2e-5*geom)))),
           'lowGeometryBad':int(np.sum(low_error>np.maximum(.05,2e-5*geom_low))),
           'conversionBad':int(np.sum(~sky&(conversion_error>np.maximum(.05,2e-5*converted)))),
           'decodedLowBad':int(np.sum(abs(a['fog-ray-depth.bin'][...,0]-low)>np.maximum(.05,2e-5*low))),
           'geometry':stats(depth_error,surface)}
    if include_extrema:
        depth.update(lowMax=float(low_error.max()),conversionMaxSurface=float(conversion_error[~sky].max(initial=0)))
    return d,low,geom_low,depth


def validate(path):
    frame=load_capture(path);root,run,m,a=frame
    d,decoded_depth,low_depth,depth=decoded(frame,include_extrema=True)
    geometry_ref,length = reference(m,d,low_depth)
    # Also test integration conditional on independently decoded raw device depth.
    # This separates rasterization/AABB boundary disagreement from reconstruction.
    ref,_ = reference(m,d,decoded_depth)
    observed = a['integrated-st.bin']
    e = observed-ref
    limit = [8e-4,1.2e-3,2.5e-3][m['densityMode']]
    identity = observed[...,:3]-(1-observed[...,3,None])*J
    quadrature_delta = 0.
    if m['densityMode'] == 2:
        ref128,_=reference(m,d,decoded_depth,128)
        quadrature_delta=float(np.max(abs(ref-ref128)))
    bounds = bool(np.all((observed[...,3]>=0)&(observed[...,3]<=1)) and np.all(observed[...,:3]>=0) and np.all(observed[...,:3]<=J+8e-4))
    vacuum = bool(np.array_equal(observed, np.broadcast_to([0,0,0,1],observed.shape))) if m['extinctionPerMetre']==0 else None
    geometry_passed = all(depth[k]==0 for k in ('skyMismatch','geometryBad','lowGeometryBad'))
    optical_passed = bool(np.max(abs(e))<=limit and np.max(abs(identity))<=8e-4 and bounds and depth['conversionBad']==0 and depth['decodedLowBad']==0 and quadrature_delta<=1e-10 and vacuum is not False)
    passed = optical_passed and (geometry_passed or run['fixture']=='Diagnostic')
    return {'run':str(root),'sourceRevision':run['sourceRevision'],'parameters':{k:run[k] for k in ('fixture','camera','width','height','steps','densityMode','extinctionPerMetre','originOffset')},'viewMin':m['viewMin'],'viewSize':m['viewSize'],'fogSize':m['fogSize'],'preExposure':m['preExposure'],'passed':passed,'opticalPassed':optical_passed,'strictGeometryPassed':geometry_passed,'depthScope':'Diagnostic integration conditional on raw device depth; ideal AABB failures remain reported' if run['fixture']=='Diagnostic' else 'Independent analytic geometry and raw device-depth conversion both required','tolerance':limit,'ST':stats(e),'geometryReferencedST':stats(observed-geometry_ref),'S':stats(e[...,:3]),'T':stats(e[...,3]),'relativeErrorFloor1e-4':stats(e/np.maximum(abs(ref),1e-4)),'mediumPixels':stats(e,length>0),'identityMax':float(np.max(abs(identity))),'bounds':bounds,'vacuumExact':vacuum,'cpu64vs128Max':quadrature_delta,'depth':depth,'worst':{'observed':observed.reshape(-1,4)[np.argmax(abs(e).max(axis=-1))].tolist(),'reference':ref.reshape(-1,4)[np.argmax(abs(e).max(axis=-1))].tolist()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runs', nargs='+',type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    result={'schemaVersion':1,'cpuChecks':cpu_checks(),'runs':[],'errors':[]}
    for run in args.runs:
        try: result['runs'].append(validate(run))
        except (AssertionError,ValueError,KeyError,OSError) as error: result['errors'].append({'run':str(run),'error':str(error)})
    result['passed']=not result['errors'] and all(r['passed'] for r in result['runs'])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n',encoding='utf8')
    for r in result['runs']: print(Path(r['run']).name, 'PASS' if r['passed'] else 'FAIL', 'maxST=',r['ST']['max'],'depth=',r['depth']['geometryBad'],r['depth']['skyMismatch'])
    for e in result['errors']: print(e,file=sys.stderr)
    return 0 if result['passed'] else 2


if __name__=='__main__': sys.exit(main())
