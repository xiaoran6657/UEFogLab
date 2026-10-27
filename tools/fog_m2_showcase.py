"""Validate separate normal captures and assemble a labeled visual evidence sheet."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw
from fog_analysis import read_json
from fog_lighting import validate_lighting,MODEL


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('suite',type=Path);p.add_argument('--output',required=True,type=Path);p.add_argument('--figure',required=True,type=Path);a=p.parse_args()
    s=read_json(a.suite/'suite.json');assert s['stage']=='Showcase' and s['schemaVersion']==2 and s['modelKey']==MODEL
    cases={x['id']:x for x in s['runs']};assert set(cases)=={'isotropic','forward','backward','reversed'} and len(s['runs'])==4
    fixed=['-FogLabPreExposure=1','-FogLabDensityMode=1','-FogLabExtinction=0.35','-FogLabEarlyOut=0','-FogLabFixture=Diagnostic','-FogLabCamera=Outside','-FogLabOriginOffset=0','-FogLabConstrainedAspect=0']
    assert s['fixedArguments']==fixed,'showcase fixed scene request mismatch'
    expected={'isotropic':(0,[-1,-1,.15]),'forward':(.5,[-1,-1,.15]),'backward':(-.5,[-1,-1,.15]),'reversed':(.5,[1,1,-.15])}
    records=[];images={};revisions=set()
    for name in ('isotropic','forward','backward','reversed'):
        path=Path(cases[name]['path']);cfg=read_json(path/'config.json');validate_lighting(cfg)
        g,k=expected[name]
        assert cfg['g']==g and cfg['photonDirectionRequestedWorld']==k and cfg['albedo']==.8 and cfg['incidentWeightRGB']==[4,3,2]
        assert cfg['capture'] and cfg['preset']=='High' and cfg['steps']==64 and cfg['fogDivisor']==1 and cfg['reconstruction']==1
        assert cfg['extraArguments']==fixed and [cfg['width'],cfg['height']]==[s['width'],s['height']]
        text=(path/'run.log').read_text(encoding='utf-8-sig',errors='replace')
        assert '-foglabreadback=' not in text.lower() and not (path/'raw').exists(),'normal capture contains readback request'
        image=Image.open(path/'frame.png').convert('RGB');assert image.size==(s['width'],s['height'])
        images[name]=np.asarray(image);revisions.add(cfg['sourceRevision'])
        records.append({'id':name,'path':str(path),'gRequested':g,'photonRequested':k,'sourceRevision':cfg['sourceRevision'],
                        'normalCaptureNoReadbackFlag':True,'pixelSize':list(image.size),
                        'artifacts':[{'file':file,'bytes':(path/file).stat().st_size,'sha256':hashlib.sha256((path/file).read_bytes()).hexdigest()} for file in ('frame.png','config.json','run.log')]})
    assert len(revisions)==1,'showcase source revision drift'
    different=bool(not np.array_equal(images['forward'],images['isotropic']) and not np.array_equal(images['forward'],images['backward']))
    assert different,'expected visible angular response missing'
    sheet=Image.new('RGB',(1280,876),'#10151b');draw=ImageDraw.Draw(sheet)
    draw.text((12,9),'UEFogLab - manual directional HG | fixed camera / radial density / exposure=1',fill='white')
    draw.text((12,28),'albedo=0.8, E=(4,3,2), High full/64 | normal tonemapped screenshots',fill='#c3cfdf')
    for index,name in enumerate(('isotropic','forward','backward','reversed')):
        x=(index%2)*640;y=54+(index//2)*392
        label=f'{name}: g={expected[name][0]:+.1f}; '+('reversed photon direction' if name=='reversed' else 'k=normalize(-1,-1,0.15)')
        draw.text((x+8,y+7),label,fill='white')
        sheet.paste(Image.fromarray(images[name]).resize((640,360),Image.Resampling.LANCZOS),(x,y+30))
    draw.text((12,846),'Incoming transmittance=visibility=1; no volume shadows. Surface lighting stays fixed.',fill='#c3cfdf')
    a.figure.parent.mkdir(parents=True,exist_ok=True);sheet.save(a.figure)
    result={'schemaVersion':2,'modelKey':MODEL,'passed':different,'evidenceType':'normal tonemapped visual captures; not numerical HDR or timing evidence',
            'requestsFixed':fixed,'captures':records,'backwardVsReversedPixelsEqual':bool(np.array_equal(images['backward'],images['reversed'])),
            'figure':{'path':str(a.figure),'sha256':hashlib.sha256(a.figure.read_bytes()).hexdigest()}}
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,indent=2)+'\n');print('M2 normal showcase passed',result['passed'],'HG reversal pixel equality',result['backwardVsReversedPixelsEqual']);return 0


if __name__=='__main__':raise SystemExit(main())
