"""Compose synchronized M6 engine sequences; retain exact input/time mappings.

Requires existing Pillow, FFmpeg and a user-supplied installed TTF font. No engine
assets, fonts or tool binaries are copied into the release.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
if Path(__file__).parent.name != 'tools': sys.path.insert(0,str(Path.cwd()/'tools'))
import m6_dynamic as m
from PIL import Image, ImageDraw, ImageFont, __version__ as PILLOW_VERSION

WIDTH, HEIGHT, FPS = 1920, 1280, 30
BG = '#101820'
COLORS = ('#edc17c','#a6ceef','#a6dcc0')
ROI = (500,200,820,520)
LABELS = {'native':('Full64','LFV','LFV > VF'),'quality':('Full64','HalfB64','HalfG64')}
TITLES = {'Camera':'01 / CAMERA ORBIT','Object':'02 / MOVING OCCLUDER','Fog':'03 / RIGID FOG TRANSLATION',
          'Light':'04 / ROTATING SUN','Reconstruction':'05 / RECONSTRUCTION EDGE'}

def write_json(path,value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8',newline='\n')


def get_runs(root,shot):
    if shot == 'Reconstruction':
        return [root/f'formal-reconstruction-{p}' for p in ('high','mediumb','mediumg')]
    return [root/f'formal-{shot.lower()}-{p}' for p in ('ours','nativelfv','nativevf')]


def draw_frame(shot,images,font):
    canvas=Image.new('RGB',(WIDTH,HEIGHT),BG); d=ImageDraw.Draw(canvas)
    d.text((30,22),TITLES[shot],font=font[70],fill='#f2f4f6')
    labels=LABELS['quality' if shot == 'Reconstruction' else 'native']
    for j,im in enumerate(images):
        x=j*640
        d.text((x+26,120),labels[j],font=font[64],fill=COLORS[j])
        canvas.paste(im.resize((640,360),Image.Resampling.LANCZOS),(x,200))
        d.rectangle((x+ROI[0]//2,200+ROI[1]//2,x+ROI[2]//2,200+ROI[3]//2),outline=COLORS[j],width=3)
        d.text((x+22,567),'SAME REGION / 2x',font=font[30],fill='#b8c6ce')
        canvas.paste(im.crop(ROI).resize((640,640),Image.Resampling.NEAREST),(x,608))
        d.line((x,608,x,1248),fill=BG,width=4)
    return canvas


def synchronize(sequences):
    reference=sequences[0]['frames']
    for other in sequences[1:]:
        m.check(len(other['frames'])==len(reference),'Mode frame count mismatch')
        for a,b in zip(reference,other['frames']):
            av,bv=a['view'],b['view']; ad,bd=av['dynamic'],bv['dynamic']
            for key in ('timeSeconds','phase','sphereWorldCm','photonDirection'):
                m.check(m.close(ad[key],bd[key],.002),'Cross-mode state mismatch: '+key)
            for key in ('cameraWorldCm','cameraForward','worldToView','viewToClip','preExposure'):
                m.check(m.close(av[key],bv[key],.003),'Cross-mode actual view mismatch: '+key)
            for ag,bg in zip(ad['actors']['geometry'],bd['actors']['geometry']):
                m.check(m.close(ag['positionCm'],bg['positionCm'],.002) and m.close(ag['scale'],bg['scale']),'Cross-mode geometry mismatch')


def compact_run(path,sequence):
    capture=m.read(path/'capture.json')
    bindings={Path(x['path']).name:x for x in capture['artifacts']}
    frames=[]
    for f in sequence['frames']:
        v=f['view']; d=v['dynamic']; image=bindings[f['image']]
        frames.append({'outputIndex':f['outputIndex'],'timeSeconds':d['timeSeconds'],'phase':d['phase'],
                       'engineFrame':v['engineFrame'],'viewIndex':v['renderedViewIndex'],'gameFrame':d['gameFrame'],
                       'cameraWorldCm':v['cameraWorldCm'],'cameraForward':v['cameraForward'],
                       'propWorldCm':d['actors']['geometry'][-1]['positionCm'],'sphereWorldCm':d['sphereWorldCm'],
                       'photonDirection':d['photonDirection'],'image':f['image'],'bytes':image['bytes'],'sha256':image['sha256']})
    return {'id':path.name,'sourceRevision':capture['sourceRevision'],'engineBuild':capture['engineBuild'],
            'runtimeBinaries':[{'name':Path(x['path']).name,'sha256':x['sha256'],'bytes':x['bytes']} for x in capture['runtimeBinaries']],
            'request':{k:v for k,v in capture['request'].items() if k!='output'},
            'sequenceSha256':m.bind(path/'sequence.json')['sha256'],'gpuWitnessSha256':m.bind(path/'gpu-witness.json')['sha256'],'frames':frames}


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--capture-root',required=True); p.add_argument('--output',required=True)
    p.add_argument('--ffmpeg',required=True); p.add_argument('--font',required=True); p.add_argument('--pilot-preview',action='store_true')
    a=p.parse_args(); root=Path(a.capture_root).resolve(); out=Path(a.output).resolve()
    m.check(m.m3.allowed_output(out) and not out.exists(),'Use a new ignored output directory')
    out.mkdir(parents=True)
    font={n:ImageFont.truetype(a.font,n) for n in (30,64,70)}
    if a.pilot_preview:
        for shot,runs in [('Camera',('pilot12-camera-full','pilot13-camera-lfv','pilot14-camera-vf')),
                          ('Reconstruction',('pilot22-recon-visible-full','pilot23-recon-visible-b','pilot24-recon-visible-g'))]:
            images=[Image.open(root/r/'frame-00015.png').convert('RGB') for r in runs]
            frame=draw_frame(shot,images,font)
            frame.resize((960,640),Image.Resampling.LANCZOS).save(out/(shot.lower()+'-web-size.png'))
            frame.resize((390,260),Image.Resampling.LANCZOS).save(out/(shot.lower()+'-phone-size.png'))
        return
    shots=[]; inputs=[]; start=0
    for shot in TITLES:
        paths=get_runs(root,shot); sequences=[m.read(path/'sequence.json') for path in paths]
        for path in paths: m.validate_capture(path)
        synchronize(sequences)
        count=len(sequences[0]['frames']); expected=240 if shot=='Reconstruction' else 210
        m.check(count==expected,'Unexpected frozen shot duration')
        shots.append({'shot':shot,'firstOutput':start,'count':count,'seconds':count/FPS,'inputRuns':[x.name for x in paths]})
        inputs.extend(compact_run(path,s) for path,s in zip(paths,sequences)); start+=count
    movie=out/'dynamic-showcase.mp4'
    command=[a.ffmpeg,'-hide_banner','-loglevel','warning','-n','-f','rawvideo','-pixel_format','rgb24','-video_size',f'{WIDTH}x{HEIGHT}',
             '-framerate',str(FPS),'-i','pipe:0','-an','-c:v','libx264','-preset','medium','-crf','18','-pix_fmt','yuv420p','-movflags','+faststart',str(movie)]
    with (out/'encode.log').open('w',encoding='utf8') as log:
        process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=log,stderr=subprocess.STDOUT)
        try:
            for shot in TITLES:
                paths=get_runs(root,shot); count=240 if shot=='Reconstruction' else 210
                for i in range(count):
                    images=[Image.open(path/f'frame-{i:05d}.png').convert('RGB') for path in paths]
                    frame=draw_frame(shot,images,font)
                    process.stdin.write(frame.tobytes())
                print(shot,'composed',count,flush=True)
            process.stdin.close(); m.check(process.wait(timeout=180)==0,'FFmpeg encoding failed')
        finally:
            if process.poll() is None: process.kill(); process.wait(timeout=20)
    ffprobe=str(Path(a.ffmpeg).with_name('ffprobe.exe' if sys.platform=='win32' else 'ffprobe'))
    probe_command=[ffprobe,'-v','error','-count_frames','-show_entries','stream=codec_name,width,height,pix_fmt,avg_frame_rate,nb_read_frames,duration:format=duration','-of','json',str(movie)]
    probe=json.loads(subprocess.check_output(probe_command,text=True)); stream=probe['streams'][0]
    m.check(len(probe['streams'])==1 and stream['codec_name']=='h264' and stream['pix_fmt']=='yuv420p' and
            int(stream['nb_read_frames'])==start and [stream['width'],stream['height']]==[WIDTH,HEIGHT] and
            stream['avg_frame_rate']=='30/1' and abs(float(probe['format']['duration'])-start/FPS)<.001,'Encoded movie mismatch')
    decode_command=[a.ffmpeg,'-v','error','-i',str(movie),'-f','null','-']
    decoded=subprocess.run(decode_command,capture_output=True,text=True,timeout=180)
    m.check(decoded.returncode==0 and not decoded.stderr.strip(),'Full movie decode failed')
    poster_command=[a.ffmpeg,'-v','error','-n','-i',str(movie),'-vf',r'select=eq(n\,105)','-frames:v','1','-fps_mode','vfr',str(out/'dynamic-poster.png')]
    subprocess.run(poster_command,check=True,capture_output=True,timeout=30)
    sources={'kind':'UEFogLab.M6DynamicMediaSources','schemaVersion':1,'timeline':shots,'inputs':inputs}
    write_json(out/'sources.json',sources)
    def public_command(cmd): return [Path(x).name if x in (a.ffmpeg,ffprobe,str(movie),str(out/'dynamic-poster.png')) else x for x in cmd]
    manifest={'kind':'UEFogLab.M6DynamicMedia','schemaVersion':1,'status':'author_candidate_pending_independent_review',
              'edition':'m6-dynamic-20261031','scope':'UE5.8.4 Win64 RX9070 D3D12 SM6 controlled motion display; not performance footage',
              'license':'MIT / Copyright (c) 2026 xiaoran6657 for original contributions; UE and third-party rights retained',
              'frames':start,'fps':FPS,'durationSeconds':start/FPS,'width':WIDTH,'height':HEIGHT,'audio':False,
              'timeline':shots,'transform':{'fullImage':[1280,720],'contextDisplay':[640,360],'commonRoiLTRB':list(ROI),'zoomDisplay':[640,640],
                                           'zoomResampling':'nearest; 2x source pixels','contextResampling':'Lanczos; 0.5x','transitions':'hard cuts; original order; no interpolation or duplicate fill'},
              'encoding':{'argv':public_command(command),'probeArgv':public_command(probe_command),'probe':probe,'fullDecodeArgv':public_command(decode_command),
                          'fullDecodeExitCode':decoded.returncode,'fullDecodeStderr':decoded.stderr,'posterArgv':public_command(poster_command),'posterOutputIndex':105,
                          'ffmpegVersion':subprocess.check_output([a.ffmpeg,'-version'],text=True).splitlines()[0],'pillowVersion':PILLOW_VERSION,
                          'font':'installed Arial TTF; font not redistributed','fontSha256':hashlib.sha256(Path(a.font).read_bytes()).hexdigest()},
              'files':[{'path':n,'bytes':(out/n).stat().st_size,'sha256':hashlib.sha256((out/n).read_bytes()).hexdigest()} for n in ('dynamic-showcase.mp4','dynamic-poster.png','sources.json')]}
    write_json(out/'manifest.json',manifest); print(json.dumps({'frames':start,'seconds':start/FPS,'fullDecode':True}))


if __name__=='__main__': main()
