"""Explicit positive-finite shader half rounding vs independent IEEE binary16."""
import argparse
import json
from pathlib import Path
import numpy as np


def shader_round(value):
    value=np.asarray(value,dtype=np.float32)
    bits=value.view(np.uint32)
    normal=((bits+0xfff+((bits>>13)&1))&0xffffe000).view(np.float32)
    sub=np.rint(value*np.float32(2**24))*np.float32(2**-24)
    return np.where(value<2**-14,sub,normal)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    known={'zero':0.,'half-min-subnormal':2**-25,'min-subnormal':2**-24,
           'subnormal-normal-midpoint':2**-14-2**-25,'min-normal':2**-14,
           'normal-halfway-even-lower':1+2**-11,'normal-halfway-even-upper':1+3*2**-11,'maximum-domain':58.}
    checks=[]
    for name,value in known.items():
        actual=float(shader_round(value));expected=float(np.float16(np.float32(value)))
        checks.append({'name':name,'inputFloat32':float(np.float32(value)),'result':actual,'independentNumpyHalf':expected,'passed':actual==expected})
    # Include every adjacent half pair through64 and the float32 on either side
    # of its exact midpoint. This exercises both tie parities and all binades.
    half=np.arange(0,0x5401,dtype=np.uint16).view(np.float16).astype(np.float32)
    mid=(half[:-1]+half[1:])*np.float32(.5)
    vectors=np.concatenate((half,mid,np.nextafter(mid,np.float32(0)),np.nextafter(mid,np.float32(np.inf))))
    actual=shader_round(vectors);expected=vectors.astype(np.float16).astype(np.float32)
    result={'passed':bool(np.array_equal(actual,expected)) and all(c['passed'] for c in checks),
            'known':checks,'boundaryVectorCount':len(vectors),'boundaryMismatches':int(np.sum(actual!=expected)),
            'evidenceType':'CPU emulation vs independent numpy IEEE binary16; not GPU',
            'limits':'finite nonnegative M2 S/T <58; sweep extends through64'}
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,indent=2)+'\n');print('half rounding',result['passed'],len(vectors),'vectors');return 0 if result['passed'] else 2


if __name__=='__main__':raise SystemExit(main())
