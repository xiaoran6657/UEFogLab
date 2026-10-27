"""Recompute M2 convergence and actual GPU reconstruction with bound references."""
from __future__ import annotations
import argparse
import hashlib
from pathlib import Path
import sys
import numpy as np
from PIL import Image,ImageDraw
from fog_analysis import read_json,rays,stats
from fog_lighting import MODEL,LIGHT_FIELDS,source_function
from fog_m2_analysis import load_capture,validate,check_case,rendering_sources,write_result,decoded
from fog_reconstruction import reference_reconstruction_sanity,edge_mask,mask_boundary

SIZES=('640x360','321x181')


class StudyError(ValueError):
    def __init__(self,code,message,details=None):
        super().__init__(message);self.code=code;self.details=details


def binding(frame):
    p=frame[0]
    return {'path':str(p),'sourceRevision':frame[1]['sourceRevision'],
            'runManifestSha256':hashlib.sha256((p/'run.json').read_bytes()).hexdigest(),
            'metadataSha256':hashlib.sha256((p/'raw/metadata.json').read_bytes()).hexdigest()}


def scene_matched(ref,other,grid=False):
    _,r0,m0,a0=ref;_,r1,m1,a1=other
    for key in ('fixture','camera','width','height','originOffset','constrainedAspect'):
        assert r0[key]==r1[key],f'unmatched scene {key}'
    for key in LIGHT_FIELDS+('schemaVersion','modelKey','viewMin','viewSize','densityMode','extinctionPerMetre','earlyOut','preExposure','cameraWorldCm','sphereWorldCm','sphereRadiusCm','viewToClip','clipToTranslatedWorld'):
        assert m0[key]==m1[key],f'unmatched scene/light {key}'
    assert np.array_equal(a0['scene-data.bin'],a1['scene-data.bin']),'scene depth/exposure changed'
    assert rendering_sources(r0)==rendering_sources(r1),'rendering source drift'
    if grid:
        assert m0['fogSize']==m1['fogSize'] and m0['downsample']==m1['downsample']==1,'sequence grid mismatch'
        assert np.array_equal(a0['fog-ray-depth.bin'],a1['fog-ray-depth.bin']),'ray mapping changed'


def scale_for(frame):
    m=frame[2];j=source_function(rays(m,m['fogSize']),m)
    return np.maximum(1,np.max(j,axis=-1))


def delta_metrics(first,second,scale):
    e=first-second;s=stats(e[...,:3]);scaled=stats(e[...,:3]/scale[...,None]);t=stats(e[...,3])
    return {'S':s,'scaledS':scaled,'T':t,'passed':scaled['max']<=5e-4 and scaled['mean']<=5e-5 and t['max']<=5e-4 and t['mean']<=5e-5}


def suite_paths(path,stage):
    s=read_json(Path(path)/'suite.json')
    assert s['schemaVersion']==2 and s['modelKey']==MODEL and s['stage']==stage,'wrong study suite/model'
    paths={x['name']:x['path'] for x in s['runs']}
    assert len(paths)==len(s['runs']),'duplicate study labels'
    return paths


def convergence(suite):
    matrix=read_json(Path(__file__).with_name('m2_cases.json'));paths=suite_paths(suite,'Convergence')
    expected={f'{c["id"]}-s{n}' for c in matrix['convergence'] for n in (256,512,1024)}
    assert set(paths)==expected,'missing/unknown convergence labels'
    cases=[];source=None
    for case in matrix['convergence']:
        frames=[]
        for n in (256,512,1024):
            f=load_capture(paths[f'{case["id"]}-s{n}'])
            check_case(f,{'id':case['id'],'overrides':dict(case['overrides'],Steps=n)},matrix['defaults'])
            assert f[2]['viewSize']==[321,181] and f[2]['viewMin']==[0,0] and f[2]['downsample']==1,'sequence size mismatch'
            if source is None:source=rendering_sources(f[1])
            assert rendering_sources(f[1])==source,'convergence rendering source drift'
            frames.append(f)
        for f in frames[1:]:scene_matched(frames[0],f,grid=True)
        st=[f[3]['integrated-st.bin'] for f in frames];a=scale_for(frames[-1])
        d0=delta_metrics(st[0],st[1],a);d1=delta_metrics(st[1],st[2],a);anchor=validate(frames[-1][0])
        cases.append({'id':case['id'],'passed':d1['passed'] and anchor['passed'],
                      'lighting':{k:frames[-1][2][k] for k in LIGHT_FIELDS},'densityMode':frames[-1][2]['densityMode'],
                      'delta256to512':d0,'delta512to1024':d1,'cpuAnchor1024':anchor,
                      'reference':str(frames[-1][0]),'frames':[binding(f) for f in frames]})
    return {'schemaVersion':2,'modelKey':MODEL,'sourceSuite':str(Path(suite).resolve()),
            'protocol':'docs/evidence/M2-validation-protocol.md','maxDeltaLimit':5e-4,'meanDeltaLimit':5e-5,
            'passed':all(c['passed'] for c in cases),'cases':cases}


def metrics(error,scale,mask):
    scaled=error[...,:3]/scale[...,None]
    return {'S':stats(error[...,:3],mask),'scaledS':stats(scaled,mask),'T':stats(error[...,3],mask),
            'ST':stats(error,mask),'scaledST':stats(np.concatenate((scaled,error[...,3:]),axis=-1),mask)}


def figure(records,ref,scale,path,marker=None):
    h,w=ref.shape[:2];label=24
    canvas=Image.new('RGB',(w*4,(h+label)*len(records)),'#10151b');draw=ImageDraw.Draw(canvas)
    for row,(name,st) in enumerate(records):
        y=row*(h+label)
        et=abs(st[...,3]-ref[...,3]);es=np.max(abs(st[...,:3]-ref[...,:3]),axis=-1)/scale
        values=[np.repeat(st[...,3,None],3,axis=-1),st[...,:3]/2,
                np.stack((et/.05,np.zeros_like(et),np.zeros_like(et)),axis=-1),
                np.stack((es/.05,np.zeros_like(es),np.zeros_like(es)),axis=-1)]
        for col,(title,value) in enumerate(zip(('T [0,1]','linear S [0,2]','|T-ref| [0,.05]','max |S-ref|/A [0,.05]'),values)):
            draw.text((col*w+4,y+5),name+' '+title,fill='white')
            canvas.paste(Image.fromarray((np.clip(value,0,1)*255).astype('uint8')),(col*w,y+label))
            if marker is not None:
                x0,y0=marker;draw.rectangle((col*w+x0-5,y+label+y0-5,col*w+x0+5,y+label+y0+5),outline='#00ffff')
    path.parent.mkdir(parents=True,exist_ok=True);canvas.save(path)


def reconstruction(suite,prior_path,figures):
    paths=suite_paths(suite,'Reconstruction');suffixes=('high','medium-bilinear','medium-guided','low-bilinear','low-guided')
    expected={size+'-'+suffix for size in SIZES for suffix in ('reference','check512')+suffixes}
    assert set(paths)==expected,'missing/unknown reconstruction labels'
    # Both actual references AND High pass before any quality or figures.
    references={size:load_capture(paths[size+'-reference']) for size in SIZES}
    highs={size:load_capture(paths[size+'-high']) for size in SIZES}
    sanity={size:reference_reconstruction_sanity(references[size]) for size in SIZES}
    high_sanity={size:reference_reconstruction_sanity(highs[size]) for size in SIZES}
    if not all(s['passed'] for s in sanity.values()):
        raise StudyError('reference_reconstruction_mismatch','actual reference reconstructed S/T does not match full integration',sanity)
    if not all(s['passed'] for s in high_sanity.values()):
        raise StudyError('full_resolution_mismatch','High reconstructed S/T does not match full integration',high_sanity)
    prior=read_json(prior_path)
    if not prior.get('passed'):raise StudyError('reference_dependency_failed','recorded convergence dependency failed')
    # Re-evaluate raw sequences, actual lighting, labels, sources and CPU anchor;
    # a copied passed=true field is not a convergence certificate.
    replay=convergence(prior['sourceSuite'])
    if not replay['passed']:raise StudyError('reference_dependency_failed','actual convergence dependency fails on replay',replay)
    if replay!=prior:raise StudyError('reference_dependency_mismatch','convergence summary differs from actual raw replay')
    radial=next(c for c in replay['cases'] if c['id']=='radial-forward');prior_ref=load_capture(radial['reference'])
    matrix=read_json(Path(__file__).with_name('m2_cases.json'));base=matrix['reconstruction'];groups=[];plots=[]
    if figures.exists():raise FileExistsError('Use a fresh figure directory; existing figures retain their earlier identity')
    for size in SIZES:
        ref=references[size];r=ref[3]['reconstructed-st.bin'];st=ref[3]['integrated-st.bin'];w,h=map(int,size.split('x'))
        for field in LIGHT_FIELDS:
            assert ref[2][field]==prior_ref[2][field],f'reference dependency lighting mismatch: {field}'
        assert rendering_sources(ref[1])==rendering_sources(prior_ref[1]),'reference dependency source drift'
        for suffix,n in (('reference',1024),('check512',512)):
            f=ref if suffix=='reference' else load_capture(paths[size+'-'+suffix])
            check_case(f,{'id':size+'-'+suffix,'overrides':dict(base['overrides'],Width=w,Height=h,Steps=n)},matrix['defaults'])
            assert f[2]['viewSize']==[w,h] and f[2]['viewMin']==[0,0] and f[2]['downsample']==1,'reference actual size mismatch'
            scene_matched(ref,f,grid=True)
            if suffix=='check512':check=f
        a=scale_for(ref);delta=delta_metrics(st,check[3]['integrated-st.bin'],a);anchor=validate(ref[0])
        assert delta['passed'] and anchor['passed'],'new reference failed convergence/CPU anchor'
        continuity=None
        if size=='321x181':
            scene_matched(ref,prior_ref,grid=True)
            continuity=(ref[0]/'raw/integrated-st.bin').read_bytes()==(prior_ref[0]/'raw/integrated-st.bin').read_bytes()
            assert continuity,'321 reference differs from bound convergence integration'
        edge=edge_mask(ref[3]['scene-data.bin'][...,1]);boundary=mask_boundary(r[...,3])
        masks={'overall':None,'edges':edge,'nonEdges':~edge,'fogBoundary':boundary,'medium':r[...,3]<.999}
        group={'size':size,'reference':binding(ref),'referenceReconstructionSanity':sanity[size],
               'referenceDelta512to1024':delta,'cpuAnchor1024':anchor,'sameRawIntegrationAsConvergence':continuity,
               'edgePixels':int(edge.sum()),'fogBoundaryPixels':int(boundary.sum()),'variants':[]}
        records=[('reference',r)];loaded={}
        for suffix in suffixes:
            f=highs[size] if suffix=='high' else load_capture(paths[size+'-'+suffix]);scene_matched(ref,f)
            expected_config=(32 if suffix.startswith('low') else 64,1 if suffix=='high' else 2,0 if suffix.endswith('bilinear') else 1)
            assert (f[2]['steps'],f[2]['downsample'],f[2]['reconstruction'])==expected_config,'variant label/config mismatch'
            _,_,_,depth_check=decoded(f)
            assert depth_check['conversionBad']==depth_check['decodedLowBad']==0,'variant DeviceZ/low-ray mapping mismatch'
            v=f[3]['reconstructed-st.bin'];e=v-r;loaded[suffix]=v
            group['variants'].append({'name':suffix,'capture':binding(f),'steps':f[2]['steps'],'fogSize':f[2]['fogSize'],
                                      'fogTexels':int(np.prod(f[2]['fogSize'])),'maximumMidpointSamples':int(np.prod(f[2]['fogSize'])*f[2]['steps']),
                                      'metrics':{name:metrics(e,a,mask) for name,mask in masks.items()},
                                      'depthMapping':depth_check,
                                      'fullResolutionSanity':high_sanity[size] if suffix=='high' else None})
            records.append((suffix,v))
        b,g=loaded['medium-bilinear'],loaded['medium-guided']
        same_integrations={}
        for tier in ('medium','low'):
            f0=Path(paths[size+'-'+tier+'-bilinear']);f1=Path(paths[size+'-'+tier+'-guided'])
            same_integrations[tier]=all((f0/'raw'/file).read_bytes()==(f1/'raw'/file).read_bytes()
                                        for file in ('integrated-st.bin','fog-ray-depth.bin'))
            assert same_integrations[tier],'reconstruction-only pair changed integration/depth'
        group['reconstructionOnlyPairByteIdentical']=same_integrations
        eb=np.maximum(np.max(abs(b[...,:3]-r[...,:3]),axis=-1)/a,abs(b[...,3]-r[...,3]))
        eg=np.maximum(np.max(abs(g[...,:3]-r[...,:3]),axis=-1)/a,abs(g[...,3]-r[...,3]))
        y,x=np.unravel_index(np.argmax(eg-eb),eg.shape)
        group.update(guidingWorsePixels=int(np.sum(eg>eb+1e-5)),guidingBetterPixels=int(np.sum(eb>eg+1e-5)),
                     comparisonNorm='max(abs(T error), maxRGB(abs(S error))/A); difference threshold1e-5',
                     worstGuidingRegression={'xy':[int(x),int(y)],'guidedError':float(eg[y,x]),'bilinearError':float(eb[y,x]),
                                             'referenceST':r[y,x].tolist(),'guidedST':g[y,x].tolist(),'bilinearST':b[y,x].tolist(),'edge':bool(edge[y,x])})
        groups.append(group);plots.append((size,records,r,a,int(x),int(y)))
    # All references/dependencies/variants are preflighted before creating any figure.
    for size,records,r,a,x,y in plots:
        figure(records,r,a,figures/(size+'-ST.png'))
        x0=max(0,x-16);x1=min(r.shape[1],x+17);y0=max(0,y-16);y1=min(r.shape[0],y+17)
        def crop(v):return np.repeat(np.repeat(v[y0:y1,x0:x1],8,axis=0),8,axis=1)
        figure([(name,crop(v)) for name,v in records],crop(r),crop(a),figures/(size+'-counterexample.png'),((x-x0)*8+4,(y-y0)*8+4))
    return {'schemaVersion':2,'modelKey':MODEL,'passed':True,'sourceSuite':str(Path(suite).resolve()),
            'convergenceDependency':str(Path(prior_path).resolve()),'convergenceRawReplayed':True,
            'protocol':'docs/evidence/M2-validation-protocol.md','groups':groups,
            'figureScales':{'T':[0,1],'linearS':[0,2],'absTError':[0,.05],'scaledSError':[0,.05]},
            'figures':[{'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(figures.glob('*.png'))]}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('stage',choices=('convergence','reconstruction'));p.add_argument('suite',type=Path)
    p.add_argument('--output',required=True,type=Path);p.add_argument('--convergence',type=Path);p.add_argument('--figures',type=Path);a=p.parse_args()
    try:
        if a.stage=='convergence':result=convergence(a.suite)
        else:
            assert a.convergence and a.figures,'reconstruction requires --convergence and --figures'
            result=reconstruction(a.suite,a.convergence,a.figures)
    except (AssertionError,ValueError,KeyError,TypeError,OSError,IndexError) as e:
        result={'schemaVersion':2,'modelKey':MODEL,'passed':False,'groups':[],
                'errors':[{'code':getattr(e,'code','study_contract_failed'),'message':str(e),'details':getattr(e,'details',None)}]}
    write_result(a.output,result);print('M2',a.stage,'passed',result['passed'])
    for c in result.get('cases',[]):print(c['id'],c['passed'],'512->1024',c['delta512to1024']['scaledS']['max'],c['delta512to1024']['T']['max'])
    for g in result.get('groups',[]):print(g['size'],'guiding worse',g['guidingWorsePixels'])
    for e in result.get('errors',[]):print(e['code'],e['message'],file=sys.stderr)
    return 0 if result['passed'] else 2


if __name__=='__main__':sys.exit(main())
