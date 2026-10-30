# M6 continuous capture protocol

This is an opt-in Win64 / D3D12 SM6 demonstration, using the installed UE 5.8.4.
It does not replace the static numerical fixtures or change High=Full64 defaults.
The movie's 30 fps is an offline simulation/playback time base, not measured GPU FPS.

## Entry and time

`tools/m6_dynamic.py run --shot Camera --mode Ours --profile High --frames 60 --output Saved/M6-DYNAMIC-MVP/pilot-camera`

Use `--engine` or UEFOGLAB_ENGINE_ROOT to select the installed editor. Output must be a new directory below
the repository's ignored Saved/, work/ or out/. The implementation must be committed.
The driver creates the existing exclusive Compare request with three additional
validated fields: dynamicShot, dynamicFrames (3–900), dynamicFps (30). The original
Compare entry remains a single static capture when those fields are absent.

Each shot is one engine process, one view and one uninterrupted scene history.
The scene holds its initial phase for 120 game ticks (configurable 120–600), then
advances one output per actual game/render frame. Time is index/30 seconds; phase
is index/(count-1), and travel uses u=(1-cos(pi*phase))/2. The final frame reaches
the endpoint. A shorter pilot therefore traverses the same path more quickly.
No frame is repeated to fill a gap. No per-output warmup or history reset occurs.

| Shot | The one changing factor |
|---|---|
| Camera | Orbit the original eye about its target from -20 to +20 degrees |
| Object | Opaque plate centred at (0,-250,160)cm travels ±180cm along (-.6435,.7655,0), across the visible fog |
| Fog | Sphere centre (300,y,180)cm translates y=-280 to +280cm |
| Light | Rotate the base photon direction about Z from -75 to +75 degrees |
| Reconstruction | Same moving plate, with a fixed closer camera |

Geometry, manual HG parameters, scene resolution and exposure are common across
modes. The added plate uses the engine basic cube (35×80×320cm). Full64,
HalfB64 and HalfG64 alter only the existing quality settings. Native settings
retain M3's radial-v2 height gate, with LFV standalone and LFV injection into VF
separate. Native densities, source functions and units differ from Ours.

## State binding and actual observations

The game thread applies transforms, reads actual actor/component state and publishes
an immutable value snapshot. Actor JSON is serialized into an owned FString on
GT and decoded into RT-owned JSON; default non-thread-safe JSON reference counters
never cross threads. The activation flag is atomic. At BeginRenderViewFamily, after UE has assigned the
scene frame, the extension binds it to that actual FrameNumber. The render thread
uses the matching snapshot for the sphere and manual HG shader parameters. No
render-thread actor dereference occurs. A bounded locked map retains recent views.

The before-DOF observer records actual view matrices, camera, exposure, CVars,
local-fog and directional-light render proxies. Custom dispatch/composite execution
counts and the actual shader sphere/photon/size/steps are recorded for that view.
The scene request arms a normal viewport screenshot each tick. UE 5.8.4's viewport
readback completes before its screenshot delegate. The delegate rejects a mismatched
output index, image dimensions or nonconsecutive scene frame/view index. Offline
validation also checks consecutive GT frames/world time, independently recomputes
trajectories and camera matrices, and checks every PNG binding.

The first, middle and last outputs get distinct fixed GPU markers (Insights retains
format strings rather than their arguments). Sparse ProfileGPU windows cover those
three markers; the matching expanded tables and Insights scopes permit checking the actual
Scene scope and custom/standalone-LFV/VF GPU paths at the first, middle and last
outputs. The marker itself is not proof that a fog dispatch ran. These are execution
and state witnesses, not GPU ST numerical accuracy or performance measurements.

VF uses temporal reprojection on, jitter on, history weight .9, grid pixel size 8
and 128 Z slices. VFNoTR is an explicitly labelled optional diagnosis. Scene AA,
motion blur, bloom and DOF are off, resolution 100%, fixed manual exposure with
pre-exposure override 1, local exposure off and one-frame thread lag 0. The effective
render-view settings are retained per output. Wall-clock capture time has no role
in animation progression. The host timeout is 480 seconds and the in-scene shot
deadline 420 seconds; only the owned child process is killed on timeout.

## Boundaries and verification

Rigid fog translation is not fluid simulation. The plate changes camera-ray depth
clipping, not volume shadows. The demo explicitly synchronizes the manual HG photon
direction with its sun; the renderer still does not extract arbitrary UE lights.
No incoming attenuation, multiple scattering, custom temporal reconstruction or
Android motion validation is added. Native history remains visible in the footage.

Before formal capture: build, existing CPU group, short actual sequences for all
four motions and the quality modes, semantic negatives, and affected legacy
Prescribed/Manual numerical captures. Actual results and fixed source/binary/media
identities belong in the execution evidence, separate from this protocol.
