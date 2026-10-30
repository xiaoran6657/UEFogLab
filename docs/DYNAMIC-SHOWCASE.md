# Controlled dynamic showcase

The 36-second movie contains four 7-second native/model comparisons and an 8-second
Full64/HalfB64/HalfG64 edge comparison. Win64 / RX9070 / UE5.8.4 / D3D12 SM6 only.
These are offline engine frame sequences, not a real-time FPS recording.

The native rows use Full64, standalone LFV and LFV density injected into VF. Native
density/source/lighting units differ from Ours. VF TR/jitter remain on with .9
history-weight CVar, one initial warmup per shot and continuous frames thereafter.
There is no per-frame warmup, repeated fill, optical flow, generated motion or
different-time ROI. Context and 2x nearest zoom share the same source frame/crop.

The camera orbits a fixed scene. An opaque plate crosses the fog. The fog sphere
translates rigidly. The directional sun rotates, and the demo explicitly supplies
its photon direction to Manual HG. This adds no automatic UE light extraction,
fluid solver, incoming shadow/attenuation, custom temporal or Android motion.

## Reproduce

Follow [QUICKSTART](QUICKSTART.md) to choose the installed engine, build and run the
existing CPU group. Use an ordinary clean source checkout; leave generated files
under ignored Saved/out/work and choose new output directories for every run.

```powershell
$env:UEFOGLAB_ENGINE_ROOT = 'D:\UE\UE_5.8'
python tools/m6_dynamic.py run --shot Camera --mode Ours --profile High --frames 30 --output Saved/M6-DYNAMIC-MVP/pilot
python tools/m6_dynamic.py validate Saved/M6-DYNAMIC-MVP/pilot
python tools/check_m6_dynamic.py Saved/M6-DYNAMIC-MVP/pilot/sequence.json --output Saved/M6-DYNAMIC-MVP/negative.json
```

The example engine path is a placeholder to replace. Thirty frames traverse the
whole phase quickly; formal native shots use210 frames, Reconstruction uses240.
Use shots Camera/Object/Fog/Light and modes Ours/NativeLFV/NativeVF with profiles
High/Default/VFTR respectively. Formal output names are formal-camera-ours,
formal-camera-nativelfv, formal-camera-nativevf (repeat per shot). Reconstruction
uses Ours and High/MediumB/MediumG, named formal-reconstruction-high/mediumb/mediumg.
All use1280x720, fixed30Hz simulation and120 warmup ticks. The driver captures,
hashes, verifies actual inputs and exports first/middle/last GPU path evidence.

After all15 formal sequences pass, compose using existing FFmpeg/Pillow and an
installed font (neither tool nor font is distributed):

```powershell
python tools/m6_media.py --capture-root Saved/M6-DYNAMIC-MVP --output Saved/M6-DYNAMIC-MVP/media-new --ffmpeg D:/tools/ffmpeg/bin/ffmpeg.exe --font C:/Windows/Fonts/arial.ttf
```

Pass your actual tool/font paths. The generator checks cross-mode actual state,
encodes H.264/yuv420p/faststart, fully decodes1080 frames, checks36s and extracts
the actual movie poster. New output hashes may depend on font/FFmpeg versions.
[Protocol](design/M6-DYNAMIC-CAPTURE.md) documents thread/view binding, deadlines,
history conditions and limitations. [Frame hashes and timeline](showcase/releases/m6-dynamic-20261031/README.md)
describe the curated candidate. Original raw PNGs/traces are local evidence, not
distributed engine assets. The initial tag remains the historical static baseline.

Author checks include all-frame state synchronization, actual GPU key paths,
existing CPU1/0/0, two legacy GPU numerical regressions/eight identical arrays,
full decode and visual inspection. Independent acceptance and publication are
separate. No general-scene, numerical motion, phone or performance guarantee.
