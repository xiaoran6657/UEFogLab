"""Quantify real GPU reconstructed S/T; generate fixed-scale diagnostic figures."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import numpy as np
from PIL import Image,ImageDraw
from fog_analysis import load_capture,read_json,stats,validate,decoded

FULL_RESOLUTION_SANITY_LIMIT = 1e-5
SIZES = ('640x360','321x181')


def reference_reconstruction_sanity(frame):
    _,_,meta,arrays=frame
    integrated=arrays['integrated-st.bin']
    reconstructed=arrays['reconstructed-st.bin']
    if meta['downsample']!=1 or reconstructed.shape!=integrated.shape:
        return {'passed':False,'tolerance':FULL_RESOLUTION_SANITY_LIMIT,
                'reason':'reference must use matching full-resolution buffers','metrics':None}
    metrics=stats(reconstructed-integrated)
    return {'passed':metrics['max']<=FULL_RESOLUTION_SANITY_LIMIT,
            'tolerance':FULL_RESOLUTION_SANITY_LIMIT,'metrics':metrics}


def dilate(mask,count=2):
    result=mask.copy()
    for _ in range(count):
        padded=np.pad(result,1)
        result=np.logical_or.reduce([padded[y:y+result.shape[0],x:x+result.shape[1]] for y,x in ((0,1),(1,0),(1,1),(1,2),(2,1))])
    return result


def edge_mask(depth):
    mask=np.zeros(depth.shape,dtype=bool)
    for axis in (0,1):
        a,b=np.take(depth,range(depth.shape[axis]-1),axis=axis),np.take(depth,range(1,depth.shape[axis]),axis=axis)
        different=abs(a-b)>np.maximum(5,.02*np.minimum(a,b))
        if axis==0:mask[:-1]|=different;mask[1:]|=different
        else:mask[:,:-1]|=different;mask[:,1:]|=different
    return dilate(mask)


def mask_boundary(t):
    inside=t<.999;mask=np.zeros(inside.shape,dtype=bool)
    for axis in (0,1):
        different=np.diff(inside.astype(np.int8),axis=axis)!=0
        if axis==0:mask[:-1]|=different;mask[1:]|=different
        else:mask[:,:-1]|=different;mask[:,1:]|=different
    return dilate(mask)


def scene_matched(ref,other):
    _,r0,m0,a0=ref;_,r1,m1,a1=other
    for k in ('fixture','camera','width','height','originOffset','constrainedAspect'):assert r0[k]==r1[k],f'unmatched {k}'
    for k in ('viewMin','viewSize','densityMode','extinctionPerMetre','earlyOut','preExposure','sourceFunction','cameraWorldCm','sphereWorldCm','sphereRadiusCm','viewToClip','clipToTranslatedWorld'):assert m0[k]==m1[k],f'unmatched {k}'
    assert np.array_equal(a0['scene-data.bin'],a1['scene-data.bin']),'unmatched scene depth/exposure'
    def sources(r):return {s['path']:s['sha256'] for s in r['sourceInputs'] if s['path'].startswith(('Config/','Plugins/','Source/')) or s['path']=='UEFogLab.uproject'}
    assert sources(r0)==sources(r1),'rendering input drift'


def figure(records,reference,path,marker=None):
    h,w=reference.shape[:2];label=24
    canvas=Image.new('RGB',(w*3,(h+label)*len(records)),'#11151c');draw=ImageDraw.Draw(canvas)
    for row,(name,st) in enumerate(records):
        y=row*(h+label)
        values=[np.repeat(st[...,3,None],3,axis=-1),st[...,:3]/np.array([.35,.6,.9]),np.stack([np.clip(abs(st[...,3]-reference[...,3])/.05,0,1),np.zeros((h,w)),np.zeros((h,w))],axis=-1)]
        for col,(title,a) in enumerate(zip(('T [0,1]','S / J [0,1]','|T-ref| red [0,.05]'),values)):
            draw.text((col*w+5,y+5),f'{name}  {title}',fill='white')
            canvas.paste(Image.fromarray((np.clip(a,0,1)*255).astype('uint8')),(col*w,y+label))
            if marker is not None:
                mx,my=marker
                draw.rectangle((col*w+mx-5,y+label+my-5,col*w+mx+5,y+label+my+5),outline='#00ffff',width=1)
    path.parent.mkdir(parents=True,exist_ok=True);canvas.save(path)


class ReconstructionError(ValueError):
    def __init__(self,code,message,**details):
        super().__init__(message);self.code=code;self.details=details


def reconstruction(suite_path, convergence_path, figures):
    assert not figures.exists(),'figures directory must be fresh'
    suite=read_json(suite_path/'suite.json')
    assert suite['schemaVersion']==1 and suite['stage']=='Reconstruction','wrong reconstruction suite'
    suffixes=('high','medium-bilinear','medium-guided','low-bilinear','low-guided')
    expected={size+'-'+name for size in SIZES for name in ('reference','check512')+suffixes}
    paths={x['name']:Path(x['path']) for x in suite['runs']}
    assert len(paths)==len(suite['runs']) and set(paths)==expected,'missing/duplicate/unknown reconstruction labels'
    frames={name:load_capture(path) for name,path in paths.items()}
    references={size:frames[size+'-reference'] for size in SIZES}
    # Preflight BOTH sizes and every variant before quality or any figure. Raw
    # integrity alone cannot establish a correct depth mapping or reference.
    sanity={size:reference_reconstruction_sanity(references[size]) for size in SIZES}
    high_sanity={size:reference_reconstruction_sanity(frames[size+'-high']) for size in SIZES}
    for size in SIZES:
        for kind,check in (('reference',sanity[size]),('high',high_sanity[size])):
            if not check['passed']:
                raise ReconstructionError('reference_reconstruction_mismatch' if kind=='reference' else 'full_resolution_mismatch',
                    f'{kind} reconstruction gate failed for {size}',size=size,
                    run=str(frames[size+'-'+kind][0]),sanity=check)
    depths={}
    for name,frame in frames.items():
        depth=decoded(frame,include_extrema=True)[3];depths[name]=depth
        if depth['conversionBad'] or depth['decodedLowBad']:
            raise ReconstructionError('canonical_depth_mismatch',f'canonical DeviceZ/low-ray mapping failed: {name}',
                                      run=str(frame[0]),capture=name,depth=depth)
    convergence=read_json(convergence_path);assert convergence['passed'],'reference dependency failed'
    prior=[x for x in convergence['cases'] if x['densityMode']==1][0]
    assert prior['passed'],'radial reference dependency failed'
    prior_frame=load_capture(prior['reference'])
    assert prior_frame[2]['steps']==1024 and prior_frame[2]['densityMode']==1,'dependency reference config mismatch'
    assert validate(prior_frame[0])['passed'],'dependency CPU anchor failed'
    prior_raw=prior_frame[3]['integrated-st.bin']
    result={'schemaVersion':1,'protocol':'docs/evidence/M1-validation-protocol.md',
            'convergenceDependency':{'path':str(convergence_path),'sha256':hashlib.sha256(convergence_path.read_bytes()).hexdigest()},'groups':[]}
    plots=[]
    for size in SIZES:
        ref=references[size];r=ref[3]['reconstructed-st.bin']
        st=ref[3]['integrated-st.bin'];check=frames[size+'-check512'];scene_matched(ref,check)
        assert ref[2]['steps']==1024 and check[2]['steps']==512 and ref[2]['downsample']==check[2]['downsample']==1,'reference label mismatch'
        assert ref[2]['densityMode']==1 and ref[1]['fixture']=='Diagnostic' and ref[1]['camera']=='Outside'
        assert ref[2]['viewSize']==[int(x) for x in size.split('x')] and ref[2]['preExposure']==1
        delta=stats(st-check[3]['integrated-st.bin']);anchor=validate(ref[0])
        continuity=bool(np.array_equal(st,prior_raw)) if size=='321x181' else None
        assert delta['max']<=5e-4 and delta['mean']<=5e-5 and anchor['passed'],'new reference did not converge/anchor'
        if continuity is not None:assert continuity,'integration changed from M1-03 reference'
        edge=edge_mask(ref[3]['scene-data.bin'][...,1]);boundary=mask_boundary(r[...,3])
        group={'size':size,'reference':str(ref[0]),'referenceReconstructionSanity':sanity[size],
               'referenceDepthMapping':depths[size+'-reference'],'check512DepthMapping':depths[size+'-check512'],
               'referenceDelta512to1024':delta,'cpuAnchor':anchor['ST'],'sameRawIntegrationAsM103':continuity,'edgePixels':int(edge.sum()),'fogBoundaryPixels':int(boundary.sum()),'variants':[]}
        records=[('reference',r)];loaded={}
        for suffix in suffixes:
            item=frames[size+'-'+suffix];scene_matched(ref,item);v=item[3]['reconstructed-st.bin'];error=v-r;loaded[suffix]=v
            expected_steps=32 if suffix.startswith('low') else 64
            expected_divisor=1 if suffix=='high' else 2
            expected_reconstruction=0 if suffix.endswith('bilinear') else 1
            assert (item[2]['steps'],item[2]['downsample'],item[2]['reconstruction'])==(expected_steps,expected_divisor,expected_reconstruction),'variant label mismatch'
            full_sanity=stats(v-item[3]['integrated-st.bin']) if suffix=='high' else None
            group['variants'].append({'name':suffix,'run':str(item[0]),'steps':item[2]['steps'],'fogSize':item[2]['fogSize'],'fogTexels':int(np.prod(item[2]['fogSize'])),'maximumMidpointSamples':int(np.prod(item[2]['fogSize'])*item[2]['steps']),'ST':stats(error),'S':stats(error[...,:3]),'T':stats(error[...,3]),'edges':stats(error,edge),'edgesS':stats(error[...,:3],edge),'edgesT':stats(error[...,3],edge),'nonEdges':stats(error,~edge),'fogBoundary':stats(error,boundary),'medium':stats(error,r[...,3]<.999),'fullResolutionSanity':full_sanity,'depthMapping':depths[size+'-'+suffix]})
            group['variants'][-1]['regions']={name:{'S':stats(error[...,:3],mask),'T':stats(error[...,3],mask),'ST':stats(error,mask)}
                                            for name,mask in (('overall',None),('edges',edge),('nonEdges',~edge),('fogBoundary',boundary),('medium',r[...,3]<.999))}
            records.append((suffix,v))
        pairs={}
        for tier in ('medium','low'):
            pair=[paths[size+'-'+tier+'-'+filter] for filter in ('bilinear','guided')]
            pairs[tier]=all((pair[0]/'raw'/file).read_bytes()==(pair[1]/'raw'/file).read_bytes()
                            for file in ('integrated-st.bin','fog-ray-depth.bin'))
            assert pairs[tier],'reconstruction-only pair changed integration/depth'
        group['reconstructionOnlyPairByteIdentical']=pairs
        bilinear,guided=loaded['medium-bilinear'],loaded['medium-guided']
        eb=np.max(abs(bilinear-r),axis=-1);eg=np.max(abs(guided-r),axis=-1)
        group['guidingWorsePixels']=int(np.sum(eg>eb+1e-5));group['guidingBetterPixels']=int(np.sum(eb>eg+1e-5))
        y,x=np.unravel_index(np.argmax(eg-eb),eg.shape)
        group['worstGuidingRegression']={'xy':[int(x),int(y)],'guidedError':float(eg[y,x]),'bilinearError':float(eb[y,x]),'refST':r[y,x].tolist(),'guidedST':guided[y,x].tolist(),'bilinearST':bilinear[y,x].tolist(),'edge':bool(edge[y,x])}
        plots.append((size,records,r,int(x),int(y)))
        result['groups'].append(group)
    # All gates, including the second size's variants, precede the first figure.
    for size,records,r,x,y in plots:
        figure(records,r,figures/(size+'-ST.png'))
        cropped=[];x0=max(0,x-16);y0=max(0,y-16);x1=min(r.shape[1],x+17);y1=min(r.shape[0],y+17)
        for name,array in records:cropped.append((name,np.repeat(np.repeat(array[y0:y1,x0:x1],8,axis=0),8,axis=1)))
        crop_ref=np.repeat(np.repeat(r[y0:y1,x0:x1],8,axis=0),8,axis=1)
        figure(cropped,crop_ref,figures/(size+'-counterexample.png'),((x-x0)*8+4,(y-y0)*8+4))
    result['passed']=True
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('suite',type=Path);p.add_argument('--convergence',required=True,type=Path);p.add_argument('--output',required=True,type=Path);p.add_argument('--figures',required=True,type=Path);args=p.parse_args()
    try:result=reconstruction(args.suite,args.convergence,args.figures)
    except ReconstructionError as error:
        result={'schemaVersion':1,'passed':False,'groups':[],
                'errors':[{'code':error.code,'message':str(error),**error.details}]}
    except (AssertionError,ValueError,KeyError,TypeError,IndexError,OSError) as error:
        result={'schemaVersion':1,'passed':False,'groups':[],
                'errors':[{'code':'invalid_reconstruction_input','message':str(error)}]}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n',encoding='utf8')
    for g in result.get('groups',[]):
        print(g['size'],'worse pixels',g['guidingWorsePixels'])
        for v in g['variants']:print(v['name'],'Tmean',v['T']['mean'],'edgeSTmean',v['edges']['mean'],'max',v['ST']['max'])
    for error in result.get('errors',[]):print(error['code'],error['message'],file=sys.stderr)
    return 0 if result['passed'] else 2


if __name__=='__main__':sys.exit(main())
