"""Rebuild presentation media from committed compact data/images; no UE/GPU work."""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont, ImageOps, __version__ as PILLOW_VERSION

SOURCE = Path(__file__).resolve().parent
ROOT = next(parent for parent in SOURCE.parents if (parent/'UEFogLab.uproject').is_file())
BG = '#101820'
FG = '#eef1f3'
MUTED = '#b4c1cc'
GOLD = '#e9bb74'

def read(p): return json.loads(p.read_text(encoding='utf-8'))
def write(p, value): p.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8', newline='\n')
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def bind(p, path): return dict(path=path, bytes=p.stat().st_size, sha256=sha(p))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--matplotlib-path', type=Path)
    parser.add_argument('--ffmpeg', default='ffmpeg')
    parser.add_argument('--font', type=Path, default=Path('C:/Windows/Fonts/arial.ttf'))
    parser.add_argument('--font-bold', type=Path, default=Path('C:/Windows/Fonts/arialbd.ttf'))
    parser.add_argument('--figures-only', action='store_true')
    args=parser.parse_args()
    out=args.output.resolve()
    assert any(out.is_relative_to(ROOT/p) for p in ['Saved','out','work']), 'Use a new ignored output directory'
    assert not out.exists(), 'Refusing existing/stale output directory'
    assert args.font.is_file() and args.font_bold.is_file(), 'Select installed fonts; do not redistribute font files'
    if args.matplotlib_path: sys.path.insert(0,str(args.matplotlib_path.resolve()))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    data=read(SOURCE/'data/results.json')
    source_map=read(SOURCE/'data/source-map.json')
    out.mkdir(parents=True)
    (out/'media').mkdir()
    commands=[]
    def command(label, argv):
        record=dict(label=label,argv=[str(x) for x in argv],startUTC=dt.datetime.now(dt.timezone.utc).isoformat())
        commands.append(record)
        write(out/'commands.json',commands)
        with (out/(label+'.log')).open('wb') as stream:
            result=subprocess.run(record['argv'],stdout=stream,stderr=subprocess.STDOUT,cwd=out)
        record.update(exitCode=result.returncode,endUTC=dt.datetime.now(dt.timezone.utc).isoformat(),log=label+'.log')
        write(out/'commands.json',commands)
        assert result.returncode==0, label+' failed; inspect preserved log'
    def font(size,bold=False): return ImageFont.truetype(str(args.font_bold if bold else args.font),size)
    def text(draw,xy,value,size=24,fill=FG,bold=False): draw.text(xy,value,font=font(size,bold),fill=fill)
    def canvas(title,sub=''):
        im=Image.new('RGB',(1280,720),BG)
        draw=ImageDraw.Draw(im)
        text(draw,(48,26),title,36,bold=True)
        if sub: text(draw,(48,78),sub,21,MUTED)
        return im,draw
    def fitted(im,source,box):
        pic=ImageOps.contain(source.convert('RGB'),(box[2],box[3]),Image.Resampling.LANCZOS)
        im.paste(pic,(box[0]+(box[2]-pic.width)//2,box[1]+(box[3]-pic.height)//2))
    for entry in source_map['files']:
        p=SOURCE/entry['path']
        assert sha(p)==entry['source']['sha256'] and p.stat().st_size==entry['source']['bytes'], 'source mirror changed'
    # Cover derives from an actual screenshot. Overlay text is presentation, not a measured buffer.
    cover=Image.open(SOURCE/'sources/manual-high.png').convert('RGB')
    assert cover.size==(1280,720)
    overlay=Image.new('RGBA',cover.size,(0,0,0,0))
    od=ImageDraw.Draw(overlay)
    od.rectangle((0,0,1280,172),fill=(16,24,32,235))
    od.rectangle((0,604,1280,720),fill=(16,24,32,240))
    cover=Image.alpha_composite(cover.convert('RGBA'),overlay).convert('RGB')
    draw=ImageDraw.Draw(cover)
    text(draw,(48,22),'UEFogLab',64,bold=True)
    text(draw,(50,108),'Local fog integration & depth-guided reconstruction',28,GOLD)
    text(draw,(48,622),'ACTUAL STATIC CAPTURE  /  Manual HG  /  Full64  /  UE 5.8.2',23)
    text(draw,(48,663),'Agent-assisted learning project  ·  MIT original work  ·  Full64 default',22,MUTED)
    cover.save(out/'media/cover.png')
    # Exact T cells from the repaired accepted 640x360 visualization; no new numerical claim.
    st=Image.open(SOURCE/'media/desktop-st.png').convert('RGB')
    assert st.size==(1920,2304)
    trip=Image.new('RGB',(1280,500),BG)
    draw=ImageDraw.Draw(trip)
    text(draw,(32,22),'Reconstruct scattering and transmittance before HDR',32,bold=True)
    text(draw,(32,70),'Prescribed T in [0,1]  |  scene 640x360  |  64 steps  |  UE 5.8.2 / RX 9070',20,MUTED)
    crops=[]
    for i,(row,label) in enumerate([(1,'High / Full64'),(2,'Medium B / Half64'),(3,'Medium G / Half64')]):
        box=(0,row*384+24,640,row*384+384)
        crops.append(list(box))
        pic=st.crop(box).resize((400,225),Image.Resampling.LANCZOS)
        trip.paste(pic,(24+i*420,140))
        text(draw,(24+i*420,108),label,23,bold=True)
    text(draw,(32,397),'Same model and source; full fog 640x360, half fog 320x180. Fixed display scale.',20,MUTED)
    text(draw,(32,435),'Guidance preserves many edges; retained local and thin-geometry failures remain in the case.',20,GOLD)
    trip.save(out/'media/reconstruction-triptych.png')
    # Summary plot is reproducible from the compact public data; component medians are not stacked.
    modes=data['mobile']['timing']['modes']
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':13,'axes.titlesize':15})
    fig,axes=plt.subplots(1,2,figsize=(12.8,6.2),dpi=100,gridspec_kw={'width_ratios':[1.5,1]})
    for i,m in enumerate(modes):
        value=m['FogTotal']['medianOfTrialMediansMs']
        lo,hi=m['FogTotal']['rangeOfTrialMediansMs']
        axes[0].errorbar(value,i,xerr=[[value-lo],[hi-value]],fmt='o',color='#496f92',capsize=5,markersize=8)
        axes[0].plot(m['freshFogTotalMs'],i,'x',color='#c6783c',markersize=9)
        axes[0].text(value+.055,i+.09,f'{value:.3f}',fontsize=12)
    axes[0].set(yticks=range(4),yticklabels=[m['name'] for m in modes],xlabel='Instrumented FogTotal (GPU ms)',xlim=(-.06,1.37),title='Four trial medians: median and range\nOrange x: one independent complete block')
    axes[0].invert_yaxis()
    axes[0].grid(axis='x',alpha=.25)
    q=data['mobile']['quality']['rows']
    vals=[q[k]['thinHitTMAE'] for k in ['HalfB64','HalfG64']]
    axes[1].bar(['HalfB64','HalfG64'],vals,color=['#7898b4','#718b73'])
    axes[1].set(ylim=(0,.5),ylabel='T MAE in 120-pixel thin-hit ROI',title='ThinStripe remains missed\nFull hits 120; low canonical hits 0')
    for i,v in enumerate(vals): axes[1].text(i,v+.015,f'{v:.6f}',ha='center',fontsize=12)
    fig.suptitle('UE 5.8.4 / MEIZU21 / Uniform64 / scene 1552x720 / Full default retained',fontsize=16,y=.98)
    fig.subplots_adjust(left=.09,right=.98,bottom=.22,top=.78,wspace=.32)
    fig.text(.5,.09,'Development / Vulkan ES3_1 HDR / Half776x360 / fixed fixtures / BOP markers perturb scheduling',ha='center',fontsize=11)
    fig.text(.5,.045,'Timing fea3f13 / fd7e335; quality 36724b7 (separate APK). Whole GPU frame, FPS and energy unavailable.',ha='center',fontsize=10)
    fig.savefig(out/'media/mobile-cost-quality.png',metadata={'Software':'UEFogLab M5 compact-data figure'})
    plt.close(fig)
    derived=[dict(path='media/cover.png',derivedFrom=['sources/manual-high.png'],transformation='opaque caption bands only; original screenshot also included'),dict(path='media/reconstruction-triptych.png',derivedFrom=['media/desktop-st.png'],transformation='exact T-cell crops then uniform 400x225 LANCZOS; no numerical analysis',cropBoxesXYXY=crops),dict(path='media/mobile-cost-quality.png',derivedFrom=['data/results.json'],transformation='Matplotlib plot of published compact data; no new timing or quality capture')]
    environment=dict(Python=sys.version.split()[0],Pillow=PILLOW_VERSION,numpy=np.__version__,matplotlib=matplotlib.__version__,fonts=[dict(name=p.name,sha256=sha(p)) for p in [args.font,args.font_bold]])
    if not args.figures_only:
        slides=out/'slides'
        slides.mkdir()
        cover.save(slides/'01.png')
        im,draw=canvas('01  /  Reconstruct S/T before composition','Prescribed radial fixture  ·  repaired depth sampling  ·  UE 5.8.2 / RX 9070')
        fitted(im,trip,(24,120,1232,480))
        text(draw,(48,655),'Cout = PreExposure * S + T * Cin  |  High / Full64 stays default',24,GOLD)
        im.save(slides/'02.png')
        im,draw=canvas('02  /  Native features and models differ','Manual HG, standalone LFV, LFV density injected into VF  ·  UE 5.8.2 / RX 9070')
        native=Image.open(SOURCE/'media/native-comparison.png').convert('RGB')
        for i,(col,row,label) in enumerate([(0,0,'Off'),(1,0,'Ours / Manual HG / Full64'),(1,1,'Native LFV standalone'),(2,1,'LFV -> VF / temporal on')]):
            x,y=16+col*656,164+row*444
            tile=native.crop((x,y,x+640,y+360))
            px,py=168+(i%2)*496,116+(i//2)*272
            text(draw,(px,py),label,22,bold=True)
            fitted(im,tile,(px,py+28,432,243))
        text(draw,(48,678),'1280x720 raw scenes; different density / light units. Display comparison, no accuracy ranking.',20,MUTED)
        im.save(slides/'03.png')
        im,draw=canvas('03  /  Lower integration cost; higher total fog cost','Fixed Development / MEIZU21 / Vulkan ES3_1 HDR / Uniform64  ·  UE 5.8.4')
        fitted(im,Image.open(out/'media/mobile-cost-quality.png'),(16,114,1248,565))
        im.save(slides/'04.png')
        im,draw=canvas('04  /  Thin geometry remains a failure case','Uniform Prescribed / actual GPU readback / scene1552x720 / Half776x360 / UE 5.8.4')
        fitted(im,Image.open(SOURCE/'media/mobile-thin-roi.png'),(35,110,850,565))
        for y,line in zip([180,235,290,345,410,465,520],['Full hits: 120','Low-grid hits: 0','Guided fallback: 120','T MAE: about 0.428','Source: 36724b7','Separate quality APK','Full64 remains default']): text(draw,(903,y),line,22,GOLD if y in [345,520] else FG)
        im.save(slides/'05.png')
        im,draw=canvas('05  /  Reproduce the bounded result','Agent-assisted implementation and review; owner study remains separately pending')
        lines=['Clean project builds and two fresh desktop GPU models on UE 5.8.4.', 'Prescribed / Manual HG / Native remain distinct models.', 'Whole GPU frame, FPS and energy are unavailable.', 'Historical startup SIGSEGV root cause remains unresolved.', 'No UE light extraction, volume shadows or temporal custom fog.', 'Static-image showcase edit: encoding rate is not measured FPS.', 'Original project work: MIT / 2026 xiaoran6657. UE rights retained.']
        for i,line in enumerate(lines): text(draw,(48,170+i*63),line,27,GOLD if i==5 else FG)
        im.save(slides/'06.png')
        (slides/'sequence.txt').write_text(''.join(f"file '{i:02d}.png'\nduration 8\n" for i in range(1,7))+"file '06.png'\n",encoding='utf-8',newline='\n')
        ffmpeg=shutil.which(args.ffmpeg) or args.ffmpeg
        ffprobe=str(Path(ffmpeg).with_name('ffprobe.exe' if str(ffmpeg).lower().endswith('.exe') else 'ffprobe'))
        environment['ffmpegVersion']=subprocess.check_output([ffmpeg,'-version'],text=True).splitlines()[0]
        video=out/'media/showcase.mp4'
        command('encode',[ffmpeg,'-hide_banner','-nostdin','-f','concat','-safe','1','-i',slides/'sequence.txt','-vf','fps=24,format=yuv420p','-t','48','-c:v','libx264','-preset','medium','-crf','20','-movflags','+faststart','-an',video])
        command('decode-all',[ffmpeg,'-v','error','-nostdin','-i',video,'-f','null','-'])
        probe=json.loads(subprocess.check_output([ffprobe,'-v','error','-show_streams','-show_format','-of','json',str(video)]))
        write(out/'video-probe.json',probe)
        assert len(probe['streams'])==1 and probe['streams'][0]['codec_name']=='h264' and probe['streams'][0]['pix_fmt']=='yuv420p'
        assert abs(float(probe['format']['duration'])-48)<.1 and video.stat().st_size<=15*1024**2
        derived.append(dict(path='media/showcase.mp4',derivedFrom=['media/cover.png','media/reconstruction-triptych.png','media/native-comparison.png','media/mobile-cost-quality.png','media/mobile-thin-roi.png','data/results.json'],transformation='six still slides, 8s each; cuts only; no motion interpolation; silent H.264 yuv420p 1280x720/24 encoded fps',durationSeconds=48,frameRateIsEncodingProperty=True,videoKind='still-image showcase edit',slideTimeRangesSeconds=[[i*8,(i+1)*8] for i in range(6)],nativeSlideCropsXYWH=[[16,164,640,360],[672,164,640,360],[672,608,640,360],[1328,608,640,360]]))
    files=[]
    for entry in source_map['files']:
        item={**entry,**bind(SOURCE/entry['path'],entry['path'])}
        with Image.open(SOURCE/entry['path']) as pic: item.update(width=pic.width,height=pic.height)
        files.append(item)
    for entry in derived:
        item={**entry,**bind(out/entry['path'],entry['path'])}
        if entry['path'].endswith('.png'):
            with Image.open(out/entry['path']) as pic: item.update(width=pic.width,height=pic.height)
        else: item.update(width=1280,height=720)
        files.append(item)
    manifest=dict(schemaVersion=1,kind='UEFogLab.PublicMediaManifest',status='mit_local_publication_candidate_not_published',pathBase='docs/showcase',sourcePathBase='historical_internal_repository_identifiers_only',generator=bind(Path(__file__),'build_showcase.py'),generatorRevision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),dataFiles=[bind(SOURCE/p,p) for p in ['data/results.json','data/source-map.json']],environment=environment,files=files,totalMediaBytes=sum(f['bytes'] for f in files),licensing='MIT / 2026 xiaoran6657 for original project work; UE and third-party rights retained; font/tool/engine binaries not included',rawBuffersIncluded=False)
    write(out/'media-manifest.json',manifest)
    write(out/'environment.json',environment)
    print(json.dumps(dict(output=str(out),mediaFiles=len(files),totalMediaBytes=manifest['totalMediaBytes'],videoGenerated=not args.figures_only)))

if __name__=='__main__': main()
