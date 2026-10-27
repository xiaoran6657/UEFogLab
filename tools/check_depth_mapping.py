"""Independent rational mapping tests and a CPU-only DeviceZ semantic probe."""
import argparse
from fractions import Fraction
import json
from pathlib import Path
import sys
import numpy as np
from fog_sampling import axis_indices,MAX_VIEW_DIMENSION
from fog_analysis import decoded


def exact_axis(view,fog):
    # Fraction establishes the mathematical pixel centre independently of the
    # vectorized production expression and of floating roundoff.
    return np.array([int(Fraction(2*p+1,2*fog)*view) for p in range(fog)],dtype=np.int64)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    checks=[];floating=[]
    sizes=((640,360),(1280,720),(321,181),(640,361),(641,360),(1280,721),(1279,720),(1,1),(2,3),(32768,32768))
    for width,height in sizes:
        for divisor in (1,2):
            for view in (width,height):
                fog=(view+divisor-1)//divisor;expected=exact_axis(view,fog);actual=axis_indices(view,fog)
                assert np.array_equal(actual,expected) and actual.min()>=0 and actual.max()<view
                maximum_numerator=(2*fog-1)*view
                assert maximum_numerator<=np.iinfo(np.int32).max
                checks.append({'view':view,'fog':fog,'pixels':fog,'first':int(actual[0]),'last':int(actual[-1]),'maximumSignedNumerator':maximum_numerator,'passed':True})
                old64=(((np.arange(fog)+.5)/fog)*view).astype(np.int64)
                old32=((np.arange(fog,dtype=np.float32)+np.float32(.5))*np.float32(1/fog)*np.float32(view)).astype(np.int64)
                if np.any(old64!=expected) or np.any(old32!=expected):
                    bad=np.flatnonzero((old64!=expected)|(old32!=expected));i=int(bad[0])
                    floating.append({'view':view,'fog':fog,'float64Mismatches':int(np.sum(old64!=expected)),'float32ReciprocalMismatches':int(np.sum(old32!=expected)),'example':{'pixel':i,'rational':int(expected[i]),'float64':int(old64[i]),'float32':int(old32[i])}})
    assert any(x['view']==360 and x['fog']==180 and x['float64Mismatches'] for x in floating)
    invalid=0
    for view,fog in ((0,0),(10,0),(-1,1),(10,11),(MAX_VIEW_DIMENSION+1,1)):
        try:axis_indices(view,fog)
        except AssertionError:invalid+=1
    assert invalid==5
    w,h=640,360;fw,fh=320,180
    # Independent simple perspective: forward Z is encoded as 10/Z. Every
    # neighboring source pixel has distinct Z, so a wrong pixel is observable.
    u,v=np.meshgrid((np.arange(w)+.5)/w,(np.arange(h)+.5)/h)
    forward_z=100+3*np.arange(w)[None,:]+5*np.arange(h)[:,None]
    scene=np.zeros((h,w,4));scene[...,0]=(10/forward_z).astype(np.float32)
    scene[...,1]=forward_z*np.sqrt(1+(2*u-1)**2+(1-2*v)**2);scene[...,2]=1
    ix,iy=np.meshgrid(exact_axis(w,fw),exact_axis(h,fh))
    lu,lv=np.meshgrid((np.arange(fw)+.5)/fw,(np.arange(fh)+.5)/fh)
    low_factor=np.sqrt(1+(2*lu-1)**2+(1-2*lv)**2)
    canonical=(forward_z[iy,ix]*low_factor).astype(np.float32)
    meta={'viewSize':[w,h],'fogSize':[fw,fh],'viewMin':[0,0],'cameraWorldCm':[0,0,0],
          'cameraRight':[1,0,0],'cameraUp':[0,1,0],'cameraForward':[0,0,1],
          'viewToClip':[1,0,0,0,0,1,0,0,0,0,0,1,0,0,10,0]}
    run={'fixture':'Empty','originOffset':0};arrays={'scene-data.bin':scene,'fog-ray-depth.bin':canonical[...,None]}
    frame=(Path('synthetic-cpu-only'),run,meta,arrays)
    correct=decoded(frame)[3];assert correct['conversionBad']==correct['decodedLowBad']==0
    meta['viewMin']=[13,9]
    translated=decoded(frame)[3];assert translated==correct,'CPU re-applied ViewMin to local scene data'
    ox,oy=np.meshgrid((((np.arange(fw)+.5)/fw)*w).astype(int),(((np.arange(fh)+.5)/fh)*h).astype(int))
    arrays['fog-ray-depth.bin']=(forward_z[oy,ox]*low_factor).astype(np.float32)[...,None]
    wrong=decoded(frame)[3];assert wrong['conversionBad']==0 and wrong['decodedLowBad']>0
    result={'passed':True,'evidenceType':'CPU only; synthetic DeviceZ, not a GPU capture','rationalAxisChecks':checks,'invalidInputsRejected':invalid,
            'floatingCounterexamples':floating,'localViewMinInvariant':True,'syntheticCanonical':correct,'syntheticFloat64WrongPixel':wrong,
            'signed32768Bound':(2*32768-1)*32768,'recordedD3D12Dimension':16384}
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_bytes((json.dumps(result,indent=2)+'\n').encode('utf8'))
    print('Rational axis checks',len(checks),'passed; synthetic old-float depth failures',wrong['decodedLowBad']);return 0


if __name__=='__main__':sys.exit(main())
