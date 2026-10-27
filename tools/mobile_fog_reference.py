"""M4 mobile Uniform Prescribed reference. No GPU recurrence or engine types.

CPU analytic/schema checks do not establish GPU execution. The full-view GPU
implementation is a later task; numerical coverage is 25 frozen sample points.
"""
from __future__ import annotations
import argparse
import copy
from fractions import Fraction
import json
import math
from pathlib import Path
import re
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = Path(__file__).with_name('mobile_fog_profile.json')
PROFILE = json.loads(PROFILE_PATH.read_text(encoding='utf-8-sig'))
MODEL = 'MobileUniformPrescribedFull64-v1'
J = (.35, .60, .90)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def dot(a, b):
    return sum(x*y for x, y in zip(a, b))


def half(value):
    require(math.isfinite(value) and abs(value) <= 65504, 'outside finite binary16 range')
    return struct.unpack('<e', struct.pack('<e', value))[0]


def ulp16(value):
    require(math.isfinite(value) and abs(value) <= 65504, 'outside finite binary16 range')
    # At binade boundaries use the outward spacing. Subnormals/zero use 2^-24.
    exponent = math.frexp(abs(value))[1]-1 if value else -24
    return math.ldexp(1., max(-24, exponent-10))


def hdr_bound(value):
    return max(2e-4, ulp16(value))


def optics(length_cm, sigma):
    require(math.isfinite(length_cm) and length_cm >= 0, 'invalid medium length')
    require(math.isfinite(sigma) and sigma >= 0, 'invalid extinction')
    tau = length_cm*.01*sigma
    return tuple(j*(-math.expm1(-tau)) for j in J)+(math.exp(-tau),)


def compose(st, cin, exposure):
    require(len(st) == 4 and len(cin) >= 3 and exposure in (1, 2), 'invalid composition inputs')
    return tuple(st[i]*exposure+st[3]*cin[i] for i in range(3))


def row_transform(v, matrix):
    return [sum(v[k]*matrix[k*4+c] for k in range(4)) for c in range(4)]


def ray(uv, size, inverse, sign_y=1):
    # Frozen decimal UVs are integer percentages, not platform float products.
    # The future HLSL uses (uint2(Percent) * ViewSize) / 100 identically.
    percent = [Fraction(str(x))*100 for x in uv]
    require(all(x.denominator == 1 and 0 <= x < 100 for x in percent), 'UV must be frozen integer percent')
    pixel = [(int(percent[i])*size[i])//100 for i in range(2)]
    u, v = [(pixel[i]+.5)/size[i] for i in range(2)]
    h = row_transform((2*u-1, (1-2*v)/sign_y, 1, 1), inverse)
    require(abs(h[3]) > 1e-15, 'invalid inverse projection')
    point = [x/h[3] for x in h[:3]]
    n = math.sqrt(dot(point, point))
    require(n > 0 and math.isfinite(n), 'invalid ray')
    return pixel, tuple(x/n for x in point)


def sphere_interval(direction, origin=(0., 0., 0.), centre=(800., 0., 0.), radius=400.):
    # Closest approach geometry, independent of the planned GPU quadratic roots.
    require(abs(dot(direction, direction)-1) < 1e-10, 'ray must be normalized')
    delta = tuple(centre[i]-origin[i] for i in range(3))
    along = dot(delta, direction)
    perpendicular = sum((delta[i]-along*direction[i])**2 for i in range(3))
    remainder = radius*radius-perpendicular
    if remainder < 0:
        return None
    reach = math.sqrt(remainder)
    lo, hi = max(0., along-reach), along+reach
    return (lo, hi) if hi > lo else None


def medium_length(direction, opaque_ray_cm=None):
    interval = sphere_interval(direction)
    if interval is None:
        return 0.
    stop = interval[1] if opaque_ray_cm is None else min(interval[1], opaque_ray_cm)
    return max(0., stop-interval[0])


def fixture_hit(direction, near):
    # Enumerate six planes per thin box; no production AABB/slab function.
    boxes = [((near, -240., -60.), (near+2., 0., 60.)),
             ((1200., 0., -120.), (1202., 700., 120.))]
    hits = []
    for box, (lo, hi) in enumerate(boxes):
        for axis in range(3):
            if abs(direction[axis]) < 1e-15:
                continue
            for face in (lo[axis], hi[axis]):
                t = face/direction[axis]
                if t <= 0:
                    continue
                p = tuple(t*x for x in direction)
                if all(lo[k]-1e-8 <= p[k] <= hi[k]+1e-8 for k in range(3)):
                    margin = min(min(p[k]-lo[k], hi[k]-p[k]) for k in range(3) if k != axis)
                    hits.append((t, box, p, margin))
    return min(hits) if hits else None


def decode_depth(z, direction, matrix):
    if z == 0:
        return None
    numerator = sum(direction[i]*matrix[i*4+2] for i in range(3))
    denominator = sum(direction[i]*matrix[i*4+3] for i in range(3))
    t = (matrix[14]-z*matrix[15])/(z*denominator-numerator)
    require(math.isfinite(t) and t > 0, 'invalid native DeviceZ')
    return t


def check_profile():
    require(PROFILE['kind'] == 'UEFogLab.MobileUniformFogProfile' and PROFILE['schemaVersion'] == 1,
            'wrong mobile profile')
    require(PROFILE['modelKey'] == MODEL and PROFILE['steps'] == 64 and PROFILE['earlyOut'] == 0,
            'wrong mobile model')
    require(len(PROFILE['sampleUV']) == 25 and len({tuple(x) for x in PROFILE['sampleUV']}) == 25,
            'missing/duplicate frozen samples')
    require(PROFILE['pixelSelection'] == 'integer-percent: (PercentInt * ViewSize) // 100', 'wrong pixel selection rule')
    require(PROFILE['samplePercentInt'] == [[int(Fraction(str(x))*100) for x in uv] for uv in PROFILE['sampleUV']],
            'percent/UV profile mismatch')
    require(PROFILE['buffer']['bytes'] == (2+4*25)*16 == 1632, 'wrong sample layout')


def analyze(meta, raw):
    """Analyze fixed-point raw values; runtime/package proof belongs to run audit.

    This function never accepts an empty/unknown layout and never treats CPU
    synthetic values as GPU execution. Device audit must additionally bind the
    actual producer->engine delegate->observer/returned resource and completion.
    """
    check_profile()
    require(meta['kind'] == 'UEFogLab.MobileUniformFog' and meta['schemaVersion'] == 1 and
            meta['modelKey'] == MODEL and meta['mode'] == 'Full', 'wrong mobile fog schema/model')
    require(re.fullmatch('[0-9a-f]{32}', meta['runId']) is not None, 'invalid mobile runId')
    require(meta['viewSize'] == [1552, 720] and meta['viewMin'] == [0, 0], 'unfrozen view')
    require(meta['cameraCm'] == [0, 0, 0] and meta['projectionSignY'] == 1, 'unfrozen camera')
    require(meta['nearCm'] in (400, 600) and meta['preExposure'] in (1, 2) and
            meta['extinctionPerMetre'] in (0, .35, .70), 'unfrozen parameters')
    require(len(raw) == 1632, 'missing/invalid mobile fog bytes')
    values = struct.unpack('<408f', raw)
    require(all(math.isfinite(x) for x in values), 'nonfinite mobile fog buffer')
    rows = [values[i:i+4] for i in range(0, 408, 4)]
    tag = int(meta['runId'][:5], 16)
    require(rows[0] == (tag & 1023, (tag >> 10) & 1023, meta['frame'], 1), 'wrong GPU runId/frame marker')
    require(abs(rows[1][0]-meta['preExposure']) <= 1e-5, 'GPU exposure mismatch')
    require(rows[1][1] == meta['nearCm'] and abs(rows[1][2]-meta['extinctionPerMetre']) <= 1e-6 and
            rows[1][3] == 64, 'GPU parameter/steps mismatch')
    m, inv = meta['worldToClip'], meta['clipToWorld']
    require(len(m) == len(inv) == 16 and all(math.isfinite(x) for x in m+inv), 'invalid view matrices')
    residual = max(abs(sum(inv[r*4+k]*m[k*4+c] for k in range(4))-(r == c))
                   for r in range(4) for c in range(4))
    require(residual < 1e-6, 'actual matrices inconsistent')
    samples, active, nohit, max_st, max_hdr, max_e2e = [], 0, 0, 0., 0., 0.
    for i, uv in enumerate(PROFILE['sampleUV']):
        pixel, d = ray(uv, meta['viewSize'], inv, meta['projectionSignY'])
        depth, st, cin, cout = rows[2+4*i:6+4*i]
        require(list(depth[2:]) == pixel, 'wrong GPU pixel centre mapping')
        hit = fixture_hit(d, meta['nearCm'])
        if hit is None:
            require(abs(depth[0]) <= 1e-7, 'sky classification mismatch')
            actual_distance = None
            sample_class = 'sky'
        else:
            expected_distance, box, position, margin = hit
            require(margin > 1, 'frozen geometry sample is on an edge')
            require(depth[0] > 0, 'plane classified as sky')
            clip = row_transform((*position, 1), m)
            expected_z = clip[2]/clip[3]
            require(abs(depth[0]-expected_z) <= max(1e-6, .01*abs(expected_z)), 'native DeviceZ geometry mismatch')
            actual_distance = decode_depth(depth[0], d, m)
            axis = actual_distance*d[0]
            require(abs(axis-position[0]) <= max(1, .01*position[0]) and
                    abs(actual_distance-expected_distance) <= max(1, .01*expected_distance), 'known plane geometry mismatch')
            require(abs(depth[1]-axis) <= max(1, .01*axis), 'native depth decode mismatch')
            sample_class = 'near' if box == 0 else 'far'
            color_expected = [.25*meta['preExposure'], .5*meta['preExposure'], 2*meta['preExposure']]
            require(all(abs(cin[k]-color_expected[k]) <= max(.001, .01*color_expected[k]) for k in range(3)) and
                    cin[2] > 1, 'actual linear HDR input mismatch')
        if i < 9:
            require(sample_class == ('near' if i < 3 else 'far' if i < 6 else 'sky'), 'PRE fixed sample classification mismatch')
        length = medium_length(d, actual_distance)
        expected = optics(length, meta['extinctionPerMetre'])
        require(all(x >= 0 and math.isfinite(x) for x in st) and 0 <= st[3] <= 1, 'invalid optical range')
        require(all(half(x) == x for x in st), 'S/T not binary16 storage values')
        st_error = max(abs(st[k]-expected[k]) for k in range(4))
        require(st_error <= 1e-3, 'optical S/T mismatch')
        wanted = compose(st, cin, meta['preExposure'])
        bounds = [hdr_bound(x) for x in wanted]
        require(all(half(cout[k]) == cout[k] and abs(cout[k]-wanted[k]) <= bounds[k] for k in range(3)) and
                cout[3] == 1, 'returned HDR composition mismatch')
        end = compose(expected, cin, meta['preExposure'])
        e2e_bounds = [meta['preExposure']*.001+abs(cin[k])*.001+bounds[k] for k in range(3)]
        require(all(abs(cout[k]-end[k]) <= e2e_bounds[k] for k in range(3)), 'end-to-end HDR mismatch')
        if expected[3] < .999:
            active += 1
        if length == 0:
            nohit += 1
        max_st = max(max_st, st_error)
        max_hdr = max(max_hdr, max(abs(cout[k]-wanted[k]) for k in range(3)))
        max_e2e = max(max_e2e, max(abs(cout[k]-end[k]) for k in range(3)))
        samples.append({'sample': i, 'pixel': pixel, 'class': sample_class, 'lengthCm': length,
                        'expectedST': expected, 'gpuST': st, 'inputRGB': cin[:3], 'returnedRGB': cout[:3],
                        'hdrBoundRGB': bounds, 'stError': st_error})
    require(meta['extinctionPerMetre'] == 0 or active >= 3, 'nonzero Full lacks active medium samples')
    require(nohit >= 1, 'no vacuum/no-intersection sample')
    return {'passed': True, 'coverage': '25 fixed points, not all-pixel numerical acceptance',
            'activeSamples': active, 'zeroLengthSamples': nohit, 'maxSTAbsolute': max_st,
            'maxHdrCompositionAbsolute': max_hdr, 'maxEndToEndAbsolute': max_e2e, 'samples': samples}


def planning(near):
    inv, m = PROFILE['planningProjection']['clipToWorld'], PROFILE['planningProjection']['worldToClip']
    result = []
    for i, uv in enumerate(PROFILE['sampleUV']):
        pixel, d = ray(uv, [1552, 720], inv)
        h = fixture_hit(d, near)
        result.append({'sample': i, 'pixel': pixel, 'class': 'sky' if h is None else 'near' if h[1] == 0 else 'far',
                       'edgeMarginCm': None if h is None else h[3],
                       'lengthCm': medium_length(d, None if h is None else h[0])})
    return result


def synthetic_sample_fixture():
    """CPU-filled layout fixture ONLY, not an execution/independent math witness."""
    meta = {'kind': 'UEFogLab.MobileUniformFog', 'schemaVersion': 1, 'modelKey': MODEL,
            'mode': 'Full', 'cpuSynthetic': True, 'runId': '0123456789abcdef0123456789abcdef',
            'frame': 180, 'nearCm': 600, 'preExposure': 2, 'extinctionPerMetre': .35,
            'viewSize': [1552, 720], 'viewMin': [0, 0], 'cameraCm': [0, 0, 0], 'projectionSignY': 1,
            'worldToClip': PROFILE['planningProjection']['worldToClip'],
            'clipToWorld': PROFILE['planningProjection']['clipToWorld']}
    tag = int(meta['runId'][:5], 16)
    rows = [(tag & 1023, tag >> 10, 180, 1), (2, 600, .35, 64)]
    for uv in PROFILE['sampleUV']:
        pixel, d = ray(uv, meta['viewSize'], meta['clipToWorld'])
        h = fixture_hit(d, 600)
        if h:
            clip = row_transform((*h[2], 1), meta['worldToClip'])
            z, axis = clip[2]/clip[3], h[2][0]
            cin = (.5, 1, 4, 1)
        else:
            z, axis, cin = 0, 1e8, (0, 0, 0, 1)
        st = tuple(half(x) for x in optics(medium_length(d, None if not h else h[0]), .35))
        cout = tuple(half(x) for x in compose(st, cin, 2))+(1,)
        rows += [(z, axis, *pixel), st, cin, cout]
    return meta, struct.pack('<408f', *(x for row in rows for x in row))


def schema_cpu_checks(output):
    meta, raw = synthetic_sample_fixture()
    positive = analyze(meta, raw)
    require(positive['activeSamples'] >= 3, 'synthetic schema case has no active medium')
    write_json(output/'synthetic-meta.json', meta)
    (output/'synthetic-buffer.bin').write_bytes(raw)
    cases = []
    for name in ('missing-bytes', 'wrong-model', 'wrong-runid', 'wrong-frame', 'wrong-exposure',
                 'vacuum-st', 'uncomposited-output', 'missing-cm-to-m'):
        m, vals = copy.deepcopy(meta), list(struct.unpack('<408f', raw))
        if name == 'missing-bytes':
            value = b''
        else:
            if name == 'wrong-model': m['modelKey'] = 'Prescribed'
            elif name == 'wrong-runid': m['runId'] = 'f'*32
            elif name == 'wrong-frame': vals[2] += 1
            elif name == 'wrong-exposure': m['preExposure'] = 1
            elif name == 'vacuum-st':
                for i in range(25): vals[12+16*i:16+16*i] = (0, 0, 0, 1)
            elif name == 'uncomposited-output':
                for i in range(25): vals[20+16*i:24+16*i] = [half(x) for x in vals[16+16*i:20+16*i]]
            else:
                for i, uv in enumerate(PROFILE['sampleUV']):
                    unused, d = ray(uv, m['viewSize'], m['clipToWorld']); h = fixture_hit(d, 600)
                    length = medium_length(d, None if h is None else h[0])
                    vals[12+16*i:16+16*i] = [half(x) for x in optics(length*100, .35)]
            value = struct.pack('<408f', *vals)
        try:
            analyze(m, value)
        except ValueError as e:
            expected = {'missing-bytes': 'missing/invalid', 'wrong-model': 'schema/model',
                        'wrong-runid': 'marker', 'wrong-frame': 'marker', 'wrong-exposure': 'exposure',
                        'vacuum-st': 'optical S/T', 'uncomposited-output': 'returned HDR',
                        'missing-cm-to-m': 'optical S/T'}
            require(expected[name] in str(e), 'wrong CPU schema rejection: '+name+': '+str(e))
            cases.append({'case': name, 'rejected': True, 'error': str(e)})
        else:
            raise ValueError('CPU schema negative accepted: '+name)
    return {'cpuSyntheticOnly': True, 'runtimeOrGpuProof': False, 'positive': positive,
            'semanticRejections': cases}


def cpu_suite(output):
    check_profile()
    checks = []
    def ok(name, condition):
        require(condition, 'CPU check failed: '+name)
        checks.append(name)
    ok('vacuum exact', optics(800, 0) == (0, 0, 0, 1))
    ok('zero interval exact', optics(0, .7) == (0, 0, 0, 1))
    st = optics(100, 1)
    ok('cm to metre Beer independent value', abs(st[3]-0.36787944117144233) < 1e-15)
    ok('source independent value', abs(st[0]-.22124219558999518) < 1e-15)
    ok('axial full chord 800cm', medium_length((1., 0., 0.)) == 800)
    ok('Near400 foreground clips whole sphere', medium_length((1., 0., 0.), 400) == 0)
    ok('Near600 clips to 200cm', medium_length((1., 0., 0.), 600) == 200)
    ok('Far1200 entire chord', medium_length((1., 0., 0.), 1200) == 800)
    ok('miss', sphere_interval((0., 1., 0.)) is None)
    ok('tangent zero length', sphere_interval((1., 0., 0.), origin=(0., 400., 0.)) is None)
    ok('inside mathematical helper only', sphere_interval((1., 0., 0.), origin=(800., 0., 0.)) == (0, 400))
    ok('FOG01-PIXEL-CENTRE-01 sample21', ray((.8, .7), [1552, 720], PROFILE['planningProjection']['clipToWorld'])[0] == [1241, 504])
    for tau in (0., 1e-8, .7, 2.8, 5.6):
        closed = optics(100, tau)
        attenuation, a = math.exp(-tau/64), -math.expm1(-tau/64)
        t, s = 1., [0., 0., 0.]
        for unused in range(64):
            s = [s[k]+t*J[k]*a for k in range(3)]; t *= attenuation
        ok('double segment/analytic tau='+str(tau), max(abs(x-y) for x, y in zip((*s, t), closed)) < 2e-14)
    for x, expected in [(0, 0), (2**-25, 0), (3*2**-25, 2**-23),
                        (1+2**-11, 1), (1+3*2**-11, 1+2**-9), (65504, 65504)]:
        ok('half RNE '+str(x), half(x) == expected)
    for x, expected in [(0, 2**-24), (2**-24, 2**-24), (2**-14, 2**-24),
                        (1-2**-11, 2**-11), (1, 2**-10), (2, 2**-9), (65504, 32)]:
        ok('half ULP '+str(x), ulp16(x) == expected)
    q = tuple(half(x) for x in optics(200, .35))
    c1 = compose(q, (.25, .5, 2), 1); c2 = compose(q, (.5, 1, 4), 2)
    ok('exposure changes HDR not ST', all(c2[i] == 2*c1[i] for i in range(3)))
    ok('HDR survives >1', half(c2[2]) > 1)
    negatives = []
    for label, fn in [('negative sigma', lambda: optics(1, -1)), ('negative length', lambda: optics(-1, 1)),
                      ('NaN length', lambda: optics(math.nan, 1)), ('infinite half', lambda: half(math.inf)),
                      ('half overflow', lambda: half(65505)), ('unfrozen exposure', lambda: compose(q, (1, 1, 1), 3))]:
        try:
            fn()
        except ValueError as e:
            negatives.append({'case': label, 'rejected': True, 'error': str(e)})
        else:
            raise ValueError('CPU negative accepted: '+label)
    plans = {str(n): planning(n) for n in (400, 600)}
    for n, samples in plans.items():
        ok('nonedge planned points near'+n, all(x['edgeMarginCm'] is None or x['edgeMarginCm'] > 1 for x in samples))
        ok('nonzero/vacuum coverage near'+n, sum(x['lengthCm'] > 1 for x in samples) >= 3 and any(x['lengthCm'] == 0 for x in samples))
    # Missing .01 is a numeric counterexample, not a changed units label.
    ok('missing cm/m conversion changes optics', max(abs(x-y) for x, y in zip(optics(200, .35), optics(20000, .35))) > .1)
    schema = schema_cpu_checks(output)
    result = {'kind': 'UEFogLab.MobileFogCPUReference', 'schemaVersion': 1, 'passed': True,
              'modelKey': MODEL, 'gpuExecuted': False, 'checks': checks, 'negatives': negatives,
              'planningCoverage': plans, 'profile': str(PROFILE_PATH),
              'syntheticSchemaChecks': schema,
              'limits': ['CPU only', 'planned 25-point coverage, not GPU or whole-image numerical acceptance',
                         'inside/tangent helper checks do not add GPU fixtures']}
    write_json(output/'cpu.json', result)
    print('Mobile fog CPU reference passed: '+str(len(checks))+' checks, '+str(len(negatives))+' rejections; no GPU')


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='action', required=True)
    c = sub.add_parser('cpu'); c.add_argument('--output', required=True)
    a = sub.add_parser('analyze'); a.add_argument('--metadata', required=True); a.add_argument('--buffer', required=True); a.add_argument('--output', required=True)
    args = p.parse_args(); out = Path(args.output).resolve()
    try:
        require(any(out.is_relative_to(ROOT/x) for x in ('Saved', 'work', 'out')) and not out.exists(), 'require new ignored output')
        if args.action == 'cpu':
            out.mkdir(parents=True); cpu_suite(out)
        else:
            meta = json.loads(Path(args.metadata).read_text(encoding='utf-8-sig'))
            result = analyze(meta, Path(args.buffer).read_bytes()); write_json(out, result)
            print('Mobile fog fixed-point numerical analysis passed; runtime identity not established by this command')
        return 0
    except (ValueError, KeyError, OverflowError, ZeroDivisionError) as e:
        print('REJECTED: '+str(e), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
