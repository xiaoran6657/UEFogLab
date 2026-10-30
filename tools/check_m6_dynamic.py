"""Mutation tests against an actual captured M6 sequence, plus real CLI refusals."""
import argparse
import copy
import json
from pathlib import Path
import subprocess
import sys
import m6_dynamic as m


def main():
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('sequence'); p.add_argument('--output',required=True)
    a = p.parse_args(); original = m.read(a.sequence); request = original['request']
    m.validate_sequence(original,request)
    cases = []
    def negative(name, mutate):
        s = copy.deepcopy(original); mutate(s)
        try: m.validate_sequence(s,request)
        except (ValueError,KeyError) as e: cases.append({'name':name,'rejected':True,'error':str(e)}); return
        raise AssertionError('Accepted invalid '+name)
    def change(path,value):
        def apply(s):
            obj = s
            for key in path[:-1]: obj = obj[key]
            obj[path[-1]] = value
        return apply
    negative('missing_frame',lambda s:s['frames'].pop(1))
    negative('duplicated_output',change(['frames',1,'outputIndex'],0))
    negative('engine_frame_gap',change(['frames',1,'view','engineFrame'],9999))
    negative('render_view_gap',change(['frames',1,'view','renderedViewIndex'],9999))
    negative('game_frame_gap',change(['frames',1,'view','dynamic','gameFrame'],9999))
    negative('wrong_animation_time',change(['frames',1,'view','dynamic','timeSeconds'],7))
    negative('wrong_simulation_step',change(['frames',1,'view','dynamic','actors','deltaSeconds'],.1))
    negative('wrong_actual_camera',change(['frames',1,'view','cameraWorldCm'],[0,0,0]))
    negative('wrong_actor_transform',change(['frames',1,'view','dynamic','actors','geometry',14,'positionCm'],[0,0,0]))
    negative('wrong_photon_input',change(['frames',1,'view','dynamic','photonDirection'],[1,0,0]))
    negative('wrong_native_sun_proxy',change(['frames',1,'view','dynamic','sunRenderProxies',0,'photonDirection'],[1,0,0]))
    negative('hidden_history_disabled',change(['frames',1,'view','cvars','r.VolumetricFog.TemporalReprojection'],0))
    negative('custom_mode_contamination',change(['frames',1,'view','integrateExecutions'],9))
    if request['mode'] == 'Ours':
        negative('wrong_shader_sphere',change(['frames',1,'view','dynamic','shaderSphereTranslatedCm'],[0,0,0]))
        negative('wrong_shader_photon',change(['frames',1,'view','dynamic','shaderPhotonDirection'],[1,0,0]))
        negative('wrong_shader_size',change(['frames',1,'view','dynamic','shaderFogSize'],[2,2]))
    if request['mode'].startswith('Native'):
        negative('wrong_native_fog_proxy',change(['frames',1,'view','dynamic','lfvRenderProxies',0,'positionCm'],[0,0,0]))
    cli = []
    base = [sys.executable,str(Path(m.__file__)),'run','--shot','Camera','--mode','Ours','--output',str(m.ROOT/'Saved/M6-DYNAMIC-MVP/refused-never-created')]
    for name,args in [('unknown_shot',['--shot','Noise']),('too_few_frames',['--frames','2']),('short_warmup',['--warmup','1']),
                      ('bad_profile',['--profile','VFTR']),('bad_dimensions',['--width','32']),('recon_native',['--shot','Reconstruction','--mode','NativeLFV'])]:
        r = subprocess.run(base+args,capture_output=True,text=True,timeout=20)
        assert r.returncode == 2 and 'REJECTED:' in r.stderr, (name,r.returncode,r.stdout,r.stderr)
        cli.append({'name':name,'exitCode':r.returncode,'stderr':r.stderr.strip()})
    assert not (m.ROOT/'Saved/M6-DYNAMIC-MVP/refused-never-created').exists()
    result = {'kind':'UEFogLab.M6DynamicNegativeChecks','actualSequence':m.bind(a.sequence),'sequenceMutations':cases,'cliRefusals':cli,'passed':True}
    m.write(a.output,result); print(json.dumps({'passed':True,'mutations':len(cases),'realCliRefusals':len(cli)}))


if __name__ == '__main__': main()
