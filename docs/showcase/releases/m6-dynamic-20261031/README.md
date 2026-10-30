# M6 dynamic showcase · author candidate

36 seconds, 1080 frames, H.264/yuv420p, 1920×1280, no audio. The 30fps time base
controls offline simulation/playback; it is not measured rendering FPS.
This candidate awaits independent review, coordinator acceptance and publication.
The historical `mit-20261027` still-image edition and old tag remain intact.

[Movie](media/dynamic-showcase.mp4) · [actual decoded movie poster](media/dynamic-poster.png)
· [encoding and file hashes](manifest.json) · [all source-frame/time bindings](sources.json).

| Movie time | Actual changing scene state | Synchronized columns |
|---|---|---|
| 0–7s | Camera orbit with real parallax | Full64 / standalone LFV / LFV→VF |
| 7–14s | Opaque plate crosses the visible fog | Same native comparison |
| 14–21s | Rigid translation of the local sphere | Same native comparison |
| 21–28s | Directional sun rotation; explicitly synced manual HG direction | Same native comparison |
| 28–36s | Closer moving occlusion edge | Full64 / HalfB64 / HalfG64 |

Each column shows the complete 1280×720 viewport at half size and the same
320×320 source region at 2× with nearest sampling. The marked box identifies that
region. All three columns use the same output index/time; no interpolation,
duplicate fill, generated motion, per-image grading or different-time crops.
Every effective segment contains motion. Static plots, cost tables and CLI details
remain in the article/repository instead of being repeated in the movie.

Final tested renderer: **4dcf1c8**. Actor records cross GT/RT as owned string values,
with an atomic activation flag; no shared JSON reference counter crosses those threads.
All 15 shots were freshly rerun, re-encoded and fully decoded after this correction.
All3240 new source PNGs, and the encoded MP4/poster, match the previous author
candidate byte-for-byte; the source map now binds only the final fresh runs.

Inputs: 15 continuous runs / 3240 actual engine PNGs, installed UE5.8.4 /
CL58902052, Win64 RX9070 D3D12 SM6. Each shot has one initial 120-tick warmup;
VF keeps TR=1, jitter=1 and history weight CVar .9 throughout the continuous shot.
Scene AA/motion blur are off; manual exposure and pre-exposure1 are fixed.
Actual view/frame, transforms, native proxies, custom shader inputs and first/
middle/last GPU paths are retained in the local capture evidence. The compact
public source map binds every PNG SHA, animation time and actual frame identity.

Native density profiles and lighting units differ from Ours; these are appearance/
feature comparisons, without pixel-accuracy ranking. Moving opaque geometry tests
camera-ray depth clipping, not volume shadows. Fog translation is not fluid motion.
Manual HG synchronization is a demo input, not UE automatic light extraction.
There is no new Android motion, formal timing or general temporal-stability claim.

Author checks: cross-mode state synchronization for every frame; full 1080-frame
movie decode; exact frame count/duration/format; first/middle/last image inspection
per shot and layout previews at 960px/390px. Poster is decoded output frame105.
Browser playback is a separate unit03 activity. These are author observations,
not independent acceptance or proof that the new source/media are already online.

Reproduction: [continuous capture protocol](../../../design/M6-DYNAMIC-CAPTURE.md),
`tools/m6_dynamic.py` and `tools/m6_media.py`. Keep raw generated captures in a new
ignored Saved/M6-DYNAMIC-MVP directory. Pass the installed engine, existing FFmpeg
and an installed TTF font explicitly; no tool/font/engine assets are distributed.

Original contributions: MIT / Copyright (c)2026 xiaoran6657. UE and third-party
rights remain reserved. Coding agents assisted implementation and experiment
execution; owner learning is tracked separately.
