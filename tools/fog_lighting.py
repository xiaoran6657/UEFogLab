"""Float64 manual directional/HG reference; independent of UE and GPU arrays.

PBRT's two outward vectors are l=-k and v=-d. The primary expression below
uses photon propagation k and outgoing v, hence the minus sign in HG.
"""
from __future__ import annotations
import math
import numpy as np
from fog_analysis import reference as optical_reference

MODEL = 'ManualDirectionalHG-v1'
CONVENTION = 'photon-propagation-to-camera-v1'
MAX_EFFECTIVE_G = float(np.float32(.8))
LIGHT_FIELDS = ('lightingInput','directionConvention','albedo','g','incidentWeightRGB',
                'photonDirectionWorld','lightTransmittance','lightVisibility')


def finite_vector(value, name):
    vector=np.asarray(value,dtype=np.float64)
    if vector.shape!=(3,) or not np.isfinite(vector).all():
        raise ValueError(f'{name} must contain three finite numbers')
    return vector


def normalize_photon(value):
    vector=finite_vector(value,'photon direction')
    scale=float(np.max(abs(vector)))
    if scale==0: raise ValueError('photon direction must be nonzero')
    vector=vector/scale
    return vector/np.linalg.norm(vector)


def validate_parameters(albedo, g, incident, *, effective=False):
    if not math.isfinite(albedo) or not 0<=albedo<=1: raise ValueError('albedo outside [0,1]')
    # Request bounds are exact. An effective GPU snapshot may contain the
    # nearest float32 representation of either endpoint; retain that value.
    bound=MAX_EFFECTIVE_G if effective else .8
    if not math.isfinite(g) or not -bound<=g<=bound: raise ValueError('g outside [-.8,.8]')
    weight=finite_vector(incident,'incident weight')
    if np.any((weight<0)|(weight>16)): raise ValueError('incident weight outside [0,16]')
    return weight


def make_lighting(albedo=.8, g=.5, incident=(4.,3.,2.), photon=(-1.,-1.,.15)):
    weight=validate_parameters(albedo,g,incident)
    original=finite_vector(photon,'photon direction')
    return {'schemaVersion':2,'modelKey':MODEL,'lightingInput':'manual',
            'directionConvention':CONVENTION,'albedo':float(albedo),'g':float(g),
            'incidentWeightRGB':weight.tolist(),'photonDirectionRequestedWorld':original.tolist(),
            'photonDirectionWorld':normalize_photon(original).tolist(),
            'lightTransmittance':1,'lightVisibility':1}


def validate_lighting(record, requested=None):
    if record.get('schemaVersion')!=2 or record.get('modelKey')!=MODEL:
        raise ValueError('M2 requires schema 2 / ManualDirectionalHG-v1')
    for key in LIGHT_FIELDS:
        if key not in record: raise ValueError(f'missing effective lighting field: {key}')
    if record['lightingInput']!='manual' or record['directionConvention']!=CONVENTION:
        raise ValueError('lighting input/direction convention mismatch')
    if record['lightTransmittance']!=1 or record['lightVisibility']!=1:
        raise ValueError('M2-v1 requires unit light transmittance/visibility')
    if 'sourceFunction' in record: raise ValueError('M2 has per-ray J, not a fixed sourceFunction')
    is_request='photonDirectionRequestedWorld' in record
    validate_parameters(record['albedo'],record['g'],record['incidentWeightRGB'],effective=not is_request)
    unit=finite_vector(record['photonDirectionWorld'],'effective photon direction')
    if abs(np.linalg.norm(unit)-1)>2e-6: raise ValueError('effective photon direction is not normalized')
    if is_request:
        normalized=normalize_photon(record['photonDirectionRequestedWorld'])
        if not np.allclose(normalized,unit,rtol=0,atol=2e-12):
            raise ValueError('requested/normalized photon direction mismatch')
    if requested is not None:
        validate_lighting(requested)
        if 'photonDirectionRequestedWorld' not in requested: raise ValueError('missing requested photon direction')
        for key in LIGHT_FIELDS:
            if isinstance(record[key],str):
                if record[key]!=requested[key]: raise ValueError(f'effective lighting mismatch: {key}')
            elif not np.allclose(record[key],requested[key],rtol=2e-6,atol=2e-7):
                raise ValueError(f'effective lighting mismatch: {key}')
    return record


def hg_phase(mu,g):
    if not math.isfinite(g) or not -MAX_EFFECTIVE_G<=g<=MAX_EFFECTIVE_G: raise ValueError('g outside [-.8,.8]')
    mu=np.asarray(mu,dtype=np.float64)
    if not np.isfinite(mu).all() or np.any(abs(mu)>1+1e-12): raise ValueError('invalid HG cosine')
    mu=np.clip(mu,-1,1)
    return (1-g*g)/(4*math.pi*np.power(1+g*g-2*g*mu,1.5))


def hg_pbrt_outward(light_out,view_out,g):
    cosine=np.sum(np.asarray(light_out)*np.asarray(view_out),axis=-1)
    denominator=1+g*g+2*g*cosine
    return (1/(4*math.pi))*(1-g*g)/(denominator*np.sqrt(denominator))


def source_function(directions,lighting):
    validate_lighting(lighting)
    d=np.asarray(directions,dtype=np.float64)
    if d.shape[-1]!=3 or not np.isfinite(d).all() or np.max(abs(np.linalg.norm(d,axis=-1)-1))>1e-9:
        raise ValueError('camera rays must be finite unit vectors')
    k=normalize_photon(lighting['photonDirectionWorld'])
    mu=np.sum(k*(-d),axis=-1)
    return lighting['albedo']*np.asarray(lighting['incidentWeightRGB'])*hg_phase(mu,lighting['g'])[...,None]


def integrate_tau(tau,directions,lighting):
    tau=np.asarray(tau,dtype=np.float64)
    if not np.isfinite(tau).all() or np.any(tau<0): raise ValueError('invalid optical thickness')
    j=source_function(directions,lighting)
    return np.concatenate((j*(-np.expm1(-tau))[...,None],np.exp(-tau)[...,None]),axis=-1)


def reference(meta,directions,depth,nodes=64):
    # M1's independent sphere/Gauss scalar optical oracle is reused. No GPU S/T
    # is used to infer a light direction, phase value, or source function.
    optical,length=optical_reference(meta,directions,depth,nodes)
    t=optical[...,3]
    j=source_function(directions,meta)
    return np.concatenate((j*(1-t)[...,None],t[...,None]),axis=-1),length,j
