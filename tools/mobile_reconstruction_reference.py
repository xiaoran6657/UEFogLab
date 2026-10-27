"""Independent CPU definitions for M4 mobile reconstruction (not GPU proof).

The reference uses analytic optics and actual stored low-grid inputs. It never
fills a device receipt or calls a production shader recurrence as ground truth.
"""
from __future__ import annotations
import argparse
from fractions import Fraction
import json
import math
from pathlib import Path
import re
import struct
import sys
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
PROFILE = json.loads(Path(__file__).with_name('mobile_reconstruction_profile.json').read_text(encoding='utf-8-sig'))
MODEL = 'MobileUniformReconstruction64-v1'
EPS = float(np.float32(PROFILE['guide']['fallbackThreshold']))


def check(condition, message):
    if not bool(condition):
        raise ValueError(message)


def write(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8', newline='\n')


def new_ignored_output(path):
    resolved=Path(path).resolve()
    try: relative=resolved.relative_to(ROOT)
    except ValueError: raise ValueError('Output must stay in project ignored Saved/work/out')
    check(relative.parts and relative.parts[0].lower() in ('saved','work','out') and not resolved.exists(),
          'Require a new ignored Saved/work/out output; never overwrite evidence')
    return resolved


def canonical_axis(v, f):
    check(v > 0 and f > 0 and (2*f-1)*v < 2**31, 'invalid/overflow canonical dimensions')
    return np.minimum(v-1, ((2*np.arange(f, dtype=np.int64)+1)*v)//(2*f))


def taps_axis(v, f):
    check(v > 0 and f > 0 and (2*v-1)*f < 2**31, 'invalid/overflow reconstruction dimensions')
    n = (2*np.arange(v, dtype=np.int64)+1)*f-v
    base, remainder = np.divmod(n, 2*v)  # exact floor, including the negative border
    fraction = remainder.astype(np.float64)/(2*v)
    return np.clip(base, 0, f-1), np.clip(base+1, 0, f-1), fraction


def half_ulp(x):
    x = np.asarray(x, dtype=np.float64)
    check(np.all(np.isfinite(x)) and np.all(np.abs(x)<=65504), 'outside finite half range')
    unused, exponent = np.frexp(np.abs(x))
    return np.ldexp(np.ones_like(x), np.where(x==0,-24,np.maximum(-24, exponent-11)))


def storage_bound(x):
    return np.maximum(.0002, half_ulp(x))


def half_store(x):
    check(np.all(np.isfinite(x)) and np.all(np.abs(x)<=65504), 'invalid CPU half storage input')
    return np.asarray(x, dtype=np.float16).astype(np.float32)


def rays(size, inverse=None):
    # Fixed +X camera: slopes come from the recorded inverse, never requested FOV.
    m = np.array(inverse or PROFILE['planningProjection']['clipToWorld'], dtype=np.float64).reshape(4,4)
    check(abs(m[3,0])>1e-15 and np.count_nonzero(np.abs(m)>1e-12)==4, 'unfrozen camera matrix structure')
    u = (np.arange(size[0],dtype=np.float64)+.5)/size[0]
    v = (np.arange(size[1],dtype=np.float64)+.5)/size[1]
    y = np.broadcast_to((2*u-1)*m[0,1]/m[3,0], (size[1],size[0]))
    z = np.broadcast_to(((1-2*v)*m[1,2]/m[3,0])[:,None], y.shape)
    length = np.sqrt(1+y*y+z*z)
    return np.stack([1/length,y/length,z/length],axis=-1)


def fixture_axis(size, near=600, fixture='Planes'):
    d = rays(size)
    boxes = [([near,-240,-60],[near+2,0,60]),([1200,0,-120],[1202,700,120])]
    if fixture == 'ThinStripe':
        t=PROFILE['thinStripe']; boxes.append((t['boundsMinCm'],t['boundsMaxCm']))
    nearest = np.full(d.shape[:2], np.inf)
    label = np.full(d.shape[:2], -1, dtype=np.int32)
    for index,(lo,hi) in enumerate(boxes):
        enter=np.full(d.shape[:2],-np.inf); leave=np.full_like(enter,np.inf)
        for k in range(3):
            nonzero=np.abs(d[...,k])>1e-15
            denom=np.where(nonzero,d[...,k],1.)
            t0,t1=lo[k]/denom,hi[k]/denom
            t0=np.where(nonzero,np.minimum(t0,t1), -np.inf if lo[k]<=0<=hi[k] else np.inf)
            t1=np.where(nonzero,np.maximum(lo[k]/denom,hi[k]/denom), np.inf if lo[k]<=0<=hi[k] else -np.inf)
            enter=np.maximum(enter,t0); leave=np.minimum(leave,t1)
        hit=(leave>=np.maximum(enter,0)) & (enter>0) & (enter<nearest)
        nearest=np.where(hit,enter,nearest); label=np.where(hit,index,label)
    axis=np.where(np.isfinite(nearest),nearest*d[...,0],0.)
    return axis,label


def decode_axis(native, matrix):
    m=np.asarray(matrix,dtype=np.float64).reshape(4,4)
    check(abs(m[3,2]-10)<1e-6 and abs(m[0,3]-1)<1e-6, 'unfrozen DeviceZ projection')
    return np.divide(m[3,2],native,out=np.zeros_like(native,dtype=np.float64),where=native>0)


def analytic_st(size, axis_cm, sigma, inverse=None):
    d=rays(size,inverse)
    # Closest approach formulation, distinct from shader quadratic discriminant.
    along=800*d[...,0]
    perpendicular=(800-along*d[...,0])**2+(along*d[...,1])**2+(along*d[...,2])**2
    reach=np.sqrt(np.maximum(0,400**2-perpendicular))
    entry=np.maximum(0,along-reach); leave=along+reach
    stop=np.divide(axis_cm,d[...,0],out=np.full_like(axis_cm,np.inf,dtype=np.float64),where=axis_cm>0)
    length=np.where(perpendicular<400**2,np.maximum(0,np.minimum(leave,stop)-entry),0)
    tau=length*.01*sigma
    alpha=-np.expm1(-tau)
    return np.concatenate([alpha[...,None]*np.asarray(PROFILE['sourceJ']),np.exp(-tau)[...,None]],axis=-1),length


def depth_weights(low, query):
    low=np.asarray(low,dtype=np.float64); query=np.asarray(query,dtype=np.float64)
    check(np.all(np.isfinite(low)) and np.all(low>=0) and np.all(np.isfinite(query)) and np.all(query>=0), 'invalid guide')
    sky=(low==0)&(query==0)
    delta=np.abs(low-query)
    geometry=(low>0)&(query>0)&(delta<=25)
    return np.where(sky,1.,np.where(geometry,np.exp(-np.minimum(delta,25)/10),0.)), sky|geometry


def fallback_index(spatial, total):
    return list(spatial).index(max(spatial)) if total<=EPS else -1


def reconstruct(low_st, low_guide, query, guided=True):
    fh,fw=low_guide.shape; vh,vw=query.shape
    check(low_st.shape==(fh,fw,4),'wrong low ST shape')
    check(np.all(np.isfinite(low_st)),'nonfinite low ST')
    x0,x1,fx=taps_axis(vw,fw); y0,y1,fy=taps_axis(vh,fh)
    rec=np.empty((vh,vw,4),dtype=np.float64)
    counts=np.empty((vh,vw),dtype=np.int32); fallback=np.full((vh,vw),-1,dtype=np.int32)
    for start in range(0,vh,96):
        end=min(vh,start+96); q=query[start:end]
        x=[x0,x1,x0,x1]; y=[y0[start:end],y0[start:end],y1[start:end],y1[start:end]]
        tx=fx[None,:]; ty=fy[start:end,None]
        spatial=[(1-tx)*(1-ty),tx*(1-ty),(1-tx)*ty,tx*ty]
        values=[low_st[y[i][:,None],x[i][None,:]] for i in range(4)]
        weights=[]; compatible=np.zeros(q.shape,dtype=np.int32)
        for i in range(4):
            w,ok=depth_weights(low_guide[y[i][:,None],x[i][None,:]],q) if guided else (np.ones(q.shape),np.ones(q.shape,dtype=bool))
            weights.append(spatial[i]*w); compatible+=ok
        total=weights[0]+weights[1]+weights[2]+weights[3]
        use=total<=EPS if guided else np.zeros(q.shape,dtype=bool)
        choice=np.argmax(np.stack(spatial),axis=0) # first 00/10/01/11 on equal maxima
        acc=np.zeros((*q.shape,4),dtype=np.float64)
        for i in range(4):
            normalized=np.divide(weights[i],total,out=np.zeros_like(total),where=~use)
            normalized=np.where(use,(choice==i).astype(np.float64),normalized)
            acc+=values[i]*normalized[...,None]
        rec[start:end]=acc; counts[start:end]=compatible; fallback[start:end]=np.where(use,choice,-1)
    return rec,counts,fallback


def budget(profile,size,fog_size):
    return 0 if profile=='Off' else 64 if profile=='GuideProbe' else (4+2*math.prod(fog_size)+4*math.prod(size))*16


def parse(meta, path):
    check(meta['kind']=='UEFogLab.MobileReconstruction' and meta['schemaVersion']==1 and meta['modelKey']==MODEL,'wrong reconstruction schema/model')
    check(re.fullmatch('[0-9a-f]{32}',meta['runId']) is not None,'wrong runId')
    v,f=meta['viewSize'],meta['fogSize']; profile=meta['profile']
    check(profile in PROFILE['profileCodes'] and len(v)==len(f)==2 and all(type(x) is int for x in v+f) and min(v+f)>0,'wrong profile/dimensions')
    check(meta['cameraCm']==[0,0,0] and meta['projectionSignY']==1,'unfrozen camera')
    check(meta['nearCm'] in (400,600) and meta['extinctionPerMetre'] in (0,.35,.70) and meta['preExposure'] in (1,2),'unfrozen model parameters')
    check(meta['steps']==64 and meta['earlyOut']==0 and meta['sourceJ']==PROFILE['sourceJ'] and
          meta['sphereCentreCm']==[800,0,0] and meta['sphereRadiusCm']==400,'wrong declared reconstruction model')
    m=np.asarray(meta['worldToClip'],dtype=np.float64).reshape(4,4)
    inv=np.asarray(meta['clipToWorld'],dtype=np.float64).reshape(4,4)
    check(np.all(np.isfinite(m)) and np.all(np.isfinite(inv)) and np.max(np.abs(inv@m-np.eye(4)))<1e-6,'inconsistent actual matrices')
    synthetic=meta.get('cpuSynthetic',False)
    if not synthetic:
        check(v==[1552,720] and meta['viewMin']==[0,0],'wrong actual scene dimensions')
        check(max(np.max(np.abs(m-np.asarray(PROFILE['planningProjection']['worldToClip']).reshape(4,4))),
                  np.max(np.abs(inv-np.asarray(PROFILE['planningProjection']['clipToWorld']).reshape(4,4))))<=1e-6,'actual fixed projection changed')
        expected=v if profile=='Full64' else PROFILE['grids']['Odd' if meta['grid']=='Odd' else 'Half']
        check(f==expected,'scene/fog dimensions confused')
    n=budget(profile,v,f)
    check(Path(path).stat().st_size==n and n<2**27,'missing/invalid dense bytes')
    if profile=='Off': return None,None,None
    rows=np.memmap(path,mode='r',dtype='<f4').reshape(-1,4)
    check(np.all(np.isfinite(rows)),'nonfinite reconstruction buffer')
    tag=int(meta['runId'][:5],16)
    check(np.array_equal(rows[0],[tag&1023,tag>>10,meta['frame'],2]),'wrong GPU runId/frame marker')
    check(rows[1,0]==meta['preExposure'] and rows[1,1]==meta['nearCm'] and abs(float(rows[1,2])-meta['extinctionPerMetre'])<=1e-6 and rows[1,3]==64,'GPU parameters/exposure mismatch')
    check(np.array_equal(rows[2],[*v,*f]),'scene/fog dimensions confused')
    check(np.array_equal(rows[3].view('<u4'),np.asarray([0,400.125,1200.75,PROFILE['profileCodes'][profile]],dtype='<f4').view('<u4')),'float32 guide actual write/read probe mismatch')
    check(meta['gpuCompletionConfirmed'] and meta['completionFrame']>meta['frame'],'missing GPU completion')
    if profile=='GuideProbe': return rows,None,None
    nf=math.prod(f)
    low=rows[4:4+2*nf].reshape(f[1],f[0],2,4)
    scene=rows[4+2*nf:].reshape(v[1],v[0],4,4)
    return rows,low,scene


def analyze(meta,path):
    rows,low,scene=parse(meta,path)
    profile=meta['profile']
    out=dict(passed=True,profile=profile,cpuSynthetic=meta.get('cpuSynthetic',False),gpuExecutionProof=False,
             note='numeric decoding only; real device/package/source/return witness belongs to separate runtime audit')
    if profile in ('Off','GuideProbe'): return out
    v,f=meta['viewSize'],meta['fogSize']; st=low[:,:,0]; ld=low[:,:,1]; sd=scene[:,:,0]
    check(np.all(st>=0) and np.all(st[...,3]<=1) and np.array_equal(half_store(st),st),'invalid low half ST range/storage')
    px,py=canonical_axis(v[0],f[0]),canonical_axis(v[1],f[1])
    check(np.array_equal(ld[...,2],np.broadcast_to(px,ld.shape[:2])) and np.array_equal(ld[...,3],np.broadcast_to(py[:,None],ld.shape[:2])),'canonical depth pixel mismatch')
    check(np.array_equal(ld[...,0],sd[py[:,None],px[None,:],0]),'canonical selected native depth mismatch')
    axis=decode_axis(sd[...,0],meta['worldToClip']); low_axis=decode_axis(ld[...,0],meta['worldToClip'])
    for z,g,wanted in [(sd[...,0],sd[...,1],axis),(ld[...,0],ld[...,1],low_axis)]:
        check(np.all(z>=0) and np.all(g>=0) and np.all((g==0)==(z==0)),'guide sky classification mismatch')
        check(np.all(np.abs(g-wanted)<=np.maximum(.01,1e-5*np.abs(wanted))),'guide view-axis centimetre mismatch')
    expected,length=analytic_st(f,low_axis,meta['extinctionPerMetre'],meta['clipToWorld'])
    optical=np.abs(st-expected)
    check(np.all(optical<=.001),'low-grid optical S/T mismatch')
    active=int(np.count_nonzero(expected[...,3]<.999))
    check(meta['extinctionPerMetre']==0 or active>=3,'nonzero reconstruction lacks active low medium')
    check(np.count_nonzero(length==0)>=1,'missing zero-length low-grid control')
    if profile=='Full64':
        wanted=st; compatible=np.full(sd.shape[:2],4); fallback=np.full(sd.shape[:2],-1)
    else:
        wanted,compatible,fallback=reconstruct(st,ld[...,1],sd[...,1],profile=='HalfG64')
    rec=scene[:,:,1]
    check(np.all(rec>=0) and np.all(rec[...,3]<=1) and np.array_equal(half_store(rec),rec),'invalid reconstructed half ST')
    check(np.array_equal(sd[...,2],compatible) and np.array_equal(sd[...,3],fallback),'reconstruction compatibility/fallback mismatch')
    rec_error=np.abs(rec-wanted); rec_bound=storage_bound(wanted)
    check(np.all(rec_error<=rec_bound),'actual low-input reconstruction mismatch')
    if profile=='Full64': check(np.array_equal(rec.view('<u4'),st.view('<u4')),'Full dense identity reconstruction mismatch')
    cin,cout=scene[:,:,2],scene[:,:,3]
    check(np.all(cin>=0) and np.all(cout>=0) and np.all(cout[...,3]==1),'invalid actual HDR range/alpha')
    quantized=rec.astype(np.float64); inputs=cin[...,:3].astype(np.float64)
    hdr=quantized[...,:3]*meta['preExposure']+quantized[...,3,None]*inputs
    hdr_error=np.abs(cout[...,:3]-hdr); hdr_bound=storage_bound(hdr)
    check(np.array_equal(half_store(cout),cout) and np.all(hdr_error<=hdr_bound),'actual returned HDR composition mismatch')
    full_analytic,unused=analytic_st(v,axis,meta['extinctionPerMetre'],meta['clipToWorld'])
    analytic_hdr=full_analytic[...,:3]*meta['preExposure']+full_analytic[...,3,None]*cin[...,:3]
    end_to_end=cout[...,:3].astype(np.float64)-analytic_hdr
    triangle=meta['preExposure']*.001+np.abs(inputs)*.001+hdr_bound
    if profile=='Full64': check(np.all(np.abs(end_to_end)<=triangle),'Full analytic end-to-end mismatch')
    out.update(lowCells=math.prod(f),scenePixels=math.prod(v),activeLowSamples=active,zeroLengthLowSamples=int(np.count_nonzero(length==0)),
               maxLowSTAbsolute=float(optical.max()),maxReconstructionAbsolute=float(rec_error.max()),
               maxReconstructionRatioToOwnBound=float((rec_error/rec_bound).max()),maxHdrCompositionAbsolute=float(hdr_error.max()),
               maxHdrRatioToOwnBound=float((hdr_error/hdr_bound).max()),fallbackPixels=int(np.count_nonzero(fallback>=0)),
               compatibilityCounts={str(i):int(np.count_nonzero(compatible==i)) for i in range(5)},
               denseFullSanity=(profile=='Full64'),analyticEndToEndHDR=statistics(end_to_end),
               fullEndToEndRatioToDerivedBound=float((np.abs(end_to_end)/triangle).max()) if profile=='Full64' else None,
               gpuHalfRneModeValidated=False)
    return out


def statistics(difference):
    error=np.abs(np.asarray(difference,dtype=np.float64)).reshape(-1,difference.shape[-1])
    check(len(error)>0 and np.all(np.isfinite(error)),'empty/nonfinite quality ROI')
    q=np.quantile(error,[.95,.99],axis=0,method='linear')
    return dict(pixels=len(error),MAE=error.mean(axis=0).tolist(),RMSE=np.sqrt((error**2).mean(axis=0)).tolist(),
                p95=q[0].tolist(),p99=q[1].tolist(),max=error.max(axis=0).tolist())


def quality(meta,path,reference,rpath):
    check(meta.get('profile') in PROFILE['quality']['supportedProfiles'],
          'unsupported quality profile: '+str(meta.get('profile'))+' (expected Full64/HalfB64/HalfG64)')
    check(reference['profile']=='Full64','quality reference is not actual Full64')
    for key in ('viewSize','viewMin','fixture','nearCm','extinctionPerMetre','preExposure','worldToClip','clipToWorld','sourceRevision','buildId'):
        check(meta[key]==reference[key],'quality pair mismatch: '+key)
    current_audit,reference_audit=analyze(meta,path),analyze(reference,rpath)
    unused,low,current=parse(meta,path); unused,full,rscene=parse(reference,rpath)
    check(np.array_equal(current[:,:,0,:2],rscene[:,:,0,:2]) and np.array_equal(current[:,:,2],rscene[:,:,2]),'quality input/depth changed')
    groups={}
    for name,(x0,y0,x1,y1) in PROFILE['quality']['rois'].items():
        if name.startswith('thin') and meta['fixture']!='ThinStripe': continue
        groups[name]=dict(ST=statistics(current[y0:y1,x0:x1,1].astype(np.float64)-rscene[y0:y1,x0:x1,1].astype(np.float64)),
                          HDR=statistics(current[y0:y1,x0:x1,3,:3].astype(np.float64)-rscene[y0:y1,x0:x1,3,:3].astype(np.float64)))
    b=PROFILE['quality']['experimentalGlobalBudget']; g=groups['whole']
    within=all(max(g[k][metric])<=b[k+'_'+metric] for k in ('ST','HDR') for metric in ('MAE','RMSE'))
    return dict(passed=within,numericImplementationPassed=True,scope='experimental global budget; ROI descriptive, default Full retained',
                groups=groups,currentNumeric=current_audit,referenceNumeric=reference_audit,gpuExecutionProof=False)


def planning(out):
    v=PROFILE['sceneSize']; result={}
    for fixture in ('Planes','ThinStripe'):
        ideal,labels=fixture_axis(v,fixture=fixture)
        z=np.divide(10,ideal,out=np.zeros_like(ideal),where=ideal>0).astype(np.float32)
        query=decode_axis(z,PROFILE['planningProjection']['worldToClip']).astype(np.float32)
        full,length=analytic_st(v,query,.35); full=half_store(full)
        item=dict(cpuSyntheticOnly=True,geometryPixels={str(i):int(np.count_nonzero(labels==i)) for i in (-1,0,1,2)},grids={})
        for grid in ('Half','Odd') if fixture=='Planes' else ('Half',):
            f=PROFILE['grids'][grid]; px,py=canonical_axis(v[0],f[0]),canonical_axis(v[1],f[1])
            guide=query[py[:,None],px[None,:]]
            low,unused=analytic_st(f,guide,.35); low=half_store(low)
            item['grids'][grid]=dict(canonicalStripeHits=int(np.count_nonzero(labels[py[:,None],px[None,:]]==2)))
            for guided in (False,True):
                rec,count,fallback=reconstruct(low,guide,query,guided)
                rec=half_store(rec); groups={}
                for name,(x0,y0,x1,y1) in PROFILE['quality']['rois'].items():
                    if name.startswith('thin') and fixture!='ThinStripe': continue
                    groups[name]=statistics(rec[y0:y1,x0:x1]-full[y0:y1,x0:x1])
                key='HalfG64' if guided else 'HalfB64'
                item['grids'][grid][key]=dict(fallbackPixels=int(np.count_nonzero(fallback>=0)),ST=groups,
                    witness1000_350=dict(compatible=int(count[350,1000]),fallback=int(fallback[350,1000]),ST=rec[350,1000].tolist(),Full=full[350,1000].tolist()))
        if fixture=='ThinStripe':
            mask=labels==2
            check(int(mask.sum())==120 and np.all(mask[300:420,1000]),'planned thin Full footprint not exactly 120 target pixels')
            check(item['grids']['Half']['canonicalStripeHits']==0,'planned thin stripe not missed by canonical Half')
            w=item['grids']['Half']['HalfG64']['witness1000_350']
            check(w['compatible']==0 and w['fallback']==3,'planned thin fallback witness failed')
        result[fixture]=item
    write(out/'planning.json',result)
    return result


def synthetic(out):
    v,f=[9,7],[5,4]; profile='HalfG64'
    axis,unused=fixture_axis(v)
    z=np.divide(10,axis,out=np.zeros_like(axis),where=axis>0).astype(np.float32)
    query=decode_axis(z,PROFILE['planningProjection']['worldToClip']).astype(np.float32)
    px,py=canonical_axis(v[0],f[0]),canonical_axis(v[1],f[1]); guide=query[py[:,None],px[None,:]]
    st,unused=analytic_st(f,guide,.35); st=half_store(st)
    rec,count,fallback=reconstruct(st,guide,query,True); rec=half_store(rec)
    cin=np.empty((*z.shape,4),dtype=np.float32); cin[...,:3]=np.where((z>0)[...,None],[.5,1,4],[0,0,0]);cin[...,3]=1
    cout=np.concatenate([half_store(rec[...,:3]*2+rec[...,3,None]*cin[...,:3]),np.ones((*z.shape,1),dtype=np.float32)],axis=-1)
    meta=dict(kind='UEFogLab.MobileReconstruction',schemaVersion=1,modelKey=MODEL,cpuSynthetic=True,
              runId='123456789abcdef0123456789abcdef0',profile=profile,fixture='Planes',viewSize=v,viewMin=[0,0],fogSize=f,grid='Half',
              nearCm=600,extinctionPerMetre=.35,preExposure=2,frame=180,completionFrame=183,gpuCompletionConfirmed=True,
              cameraCm=[0,0,0],projectionSignY=1,steps=64,earlyOut=0,sourceJ=PROFILE['sourceJ'],sphereCentreCm=[800,0,0],sphereRadiusCm=400,
              worldToClip=PROFILE['planningProjection']['worldToClip'],clipToWorld=PROFILE['planningProjection']['clipToWorld'])
    rows=np.empty((budget(profile,v,f)//16,4),dtype='<f4'); tag=int(meta['runId'][:5],16)
    rows[:4]=[[tag&1023,tag>>10,180,2],[2,600,.35,64],[*v,*f],[0,400.125,1200.75,3]]
    low=rows[4:4+2*math.prod(f)].reshape(f[1],f[0],2,4)
    low[:,:,0]=st; low[:,:,1]=np.stack([z[py[:,None],px[None,:]],guide,np.broadcast_to(px,guide.shape),np.broadcast_to(py[:,None],guide.shape)],axis=-1)
    scene=rows[4+2*math.prod(f):].reshape(v[1],v[0],4,4)
    scene[:,:,0]=np.stack([z,query,count,fallback],axis=-1);scene[:,:,1]=rec;scene[:,:,2]=cin;scene[:,:,3]=cout
    raw=out/'synthetic-dense.bin'; rows.tofile(raw); write(out/'synthetic-meta.json',meta)
    result=analyze(meta,raw); cases=[]
    for name in ('canonical-neighbor','guide-metres','guide-sky-class','wrong-fallback','vacuum-st','uncomposited-output'):
        bad=rows.copy(); l=bad[4:4+2*math.prod(f)].reshape(f[1],f[0],2,4);s=bad[4+2*math.prod(f):].reshape(v[1],v[0],4,4)
        if name=='canonical-neighbor':l[0,0,1,2]+=1
        elif name=='guide-metres':l[:,:,1,1]/=100
        elif name=='guide-sky-class':
            y,x=np.argwhere(l[:,:,1,0]==0)[0];l[y,x,1,1]=600
        elif name=='wrong-fallback':s[0,0,0,3]=3 if s[0,0,0,3]!=3 else 0
        elif name=='vacuum-st':l[:,:,0]=[0,0,0,1]
        else:s[:,:,3]=half_store(s[:,:,2])
        bpath=out/(name+'-synthetic.bin');bad.tofile(bpath)
        try:analyze(meta,bpath)
        except ValueError as e: cases.append(dict(case=name,rejected=True,error=str(e)))
        else:raise ValueError('synthetic negative accepted: '+name)
    return dict(cpuSyntheticOnly=True,gpuExecutionProof=False,positive=result,negativeCases=cases)


def cpu(out):
    check(not out.exists(),'Require a new output directory')
    out.mkdir(parents=True)
    checks=[]; centres=0
    for v,f in [(v,f) for v in range(1,34) for f in range(1,34)]+[(321,161),(181,91),(1551,776),(719,360),(1552,776),(720,360),(1552,775),(720,359),(1552,1552)]:
        actual=canonical_axis(v,f)
        expected=[min(v-1,int(Fraction((2*i+1)*v,2*f))) for i in range(f)]
        check(actual.tolist()==expected,'rational canonical mapping mismatch')
        lo,hi,fr=taps_axis(v,f)
        for p in range(v):
            x=Fraction((2*p+1)*f-v,2*v); b=x.numerator//x.denominator
            check((int(lo[p]),int(hi[p]))==(max(0,min(f-1,b)),max(0,min(f-1,b+1))),'rational tap floor/clamp mismatch')
            check(abs(fr[p]-float(x-b))<1e-15,'rational fractional weight mismatch')
        centres+=f
    checks.append('exact Fraction canonical and signed tap mapping across small/real odd-even dimensions')
    guide=np.array([[600.,1200.],[0.,600.]])
    st=np.array([[[0,.1,.2,1],[.2,.3,.4,.8]],[[.4,.5,.6,.6],[.9,.8,.7,.1]]])
    rec,count,fall=reconstruct(st,guide,np.full((3,3),600.))
    check(np.max(np.abs(rec[1,1]-[.45,.45,.45,.55]))<1e-15 and count[1,1]==2 and fall[1,1]==-1,'normalization/two compatible corners failed')
    checks.append('normalized four logical neighbors with mixed sky/geometry')
    rec,count,fall=reconstruct(st,np.full((2,2),1200.),np.full((3,3),600.))
    check(np.array_equal(rec[1,1],st[0,0]) and count[1,1]==0 and fall[1,1]==0,'zero compatibility tie fallback failed')
    checks.append('all-incompatible fallback tie selects 00')
    rec,unused,unused=reconstruct(st,np.zeros((2,2)),np.zeros((3,3)))
    check(np.max(np.abs(rec[1,1]-st.mean(axis=(0,1))))<1e-15,'same sky weights failed')
    check(np.array_equal(rec[0,0],st[0,0]),'border duplicate weights failed')
    checks.append('same sky and repeated clamped border neighbors')
    w,ok=depth_weights(np.array([625.,np.nextafter(625.,np.inf),0.,600.]),np.array([600.,600.,600.,0.]))
    check(ok.tolist()==[True,False,False,False] and abs(w[0]-math.exp(-2.5))<1e-15 and np.all(w[1:]==0),'depth/sky hard-gate boundary failed')
    check(fallback_index([.25]*4,EPS)==0 and fallback_index([.25]*4,np.nextafter(EPS,np.inf))==-1,'fallback threshold <= boundary failed')
    checks.append('25cm inclusion, nextafter exclusion, cross-class zero, exact threshold and tie')
    for x,ulp in [(0,2**-24),(2**-14,2**-24),(1,2**-10),(2,2**-9),(65504,32)]:
        check(float(half_ulp(x))==ulp,'outward half ULP boundary failed')
    check(float(half_store(1+2**-11))==1 and float(half_store(1+3*2**-11))==1+2**-9,'CPU RNE tie boundary failed')
    checks.append('half outward binade ULP and CPU-only RNE ties')
    planned=planning(out); syn=synthetic(out)
    result=dict(kind='UEFogLab.MobileReconstructionCpuEvidence',passed=True,cpuOnly=True,gpuExecutionProof=False,
                mappingPairs=1098,canonicalCentres=centres,checks=checks,planning=planned,synthetic=syn,
                note='synthetic arrays and planning figures never count as real Full or device evidence')
    write(out/'cpu.json',result)
    print(json.dumps(dict(passed=True,mappingPairs=1098,canonicalCentres=centres,cpuChecks=len(checks),syntheticNegatives=len(syn['negativeCases']),thinPlannedFullHits=120,thinPlannedHalfHits=0)))


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    c=sub.add_parser('cpu');c.add_argument('--output',required=True)
    for name in ('analyze','quality'):
        c=sub.add_parser(name);c.add_argument('--metadata',required=True);c.add_argument('--buffer',required=True);c.add_argument('--output',required=True)
        if name=='quality':c.add_argument('--reference-metadata',required=True);c.add_argument('--reference-buffer',required=True)
    a=p.parse_args()
    # Acquire only a new ignored destination. Refusal must not overwrite it with
    # an error report, nor ever replace input metadata/readback bytes.
    try: a.output=str(new_ignored_output(a.output))
    except ValueError as e:
        print(str(e),file=sys.stderr);return 2
    try:
        if a.action=='cpu':cpu(Path(a.output))
        else:
            m=json.loads(Path(a.metadata).read_text(encoding='utf-8-sig'))
            result=analyze(m,a.buffer) if a.action=='analyze' else quality(m,a.buffer,json.loads(Path(a.reference_metadata).read_text(encoding='utf-8-sig')),a.reference_buffer)
            write(a.output,result)
            if not result['passed']:
                print('experimental quality budget failed; complete metrics preserved',file=sys.stderr);return 2
            print('CPU reconstruction reference analysis passed; no device execution proof')
    except (ValueError,KeyError,OSError) as e:
        if a.action!='cpu':write(a.output,dict(passed=False,errors=[str(e)],gpuExecutionProof=False))
        print(str(e),file=sys.stderr);return 2
    return 0


if __name__=='__main__':sys.exit(main())
