# UEFogLab: local fog integration and depth-guided reconstruction

This UE5.8 / C++ / HLSL learning project checks scattering and transmittance before evaluating reduced-resolution rendering. In the recorded MEIZU21 fixture, half-resolution integration costs less, while reconstruction makes total fog cost higher. **Full64 / High remains the default.** Original code, self-written documents and figures use MIT (2026 xiaoran6657); UE and third-party rights remain reserved. This standalone public-source candidate has not been published.

![Cover derived from an actual Manual HG still](media/cover.png)

The cover adds captions to an actual UE5.8.2 / RX9070 Manual HG Full64 screenshot. Coding agents assisted implementation, experiment execution and independent review. Owner study, individual derivation and technical mastery are tracked separately. The [48-second silent video](media/showcase.mp4) is a **still-image showcase edit**. Its 24fps encoding rate is not measured FPS or evidence of motion stability.

## Method and implementation

Halving both fog dimensions reduces the number of integration pixels to about one quarter. Opaque depth boundaries can mix during reconstruction, and thin occluders can vanish from low-grid sampling. Scene and fog resolutions therefore remain independent.

The C++ diagnostic scene fixes geometry, camera and medium parameters. The `Plugins/UEFog` RDG path clips a sphere against scene depth, integrates unexposed `S.rgb` and `T`, reconstructs these quantities, then composites pre-exposed linear HDR as `Cout = PreExposure * S + T * Cin`. Geometry is in centimetres; extinction is in inverse metres. Numerical readback, independent CPU reference, screenshots and timing use separate evidence paths.

Both desktop models use exact integer-centre depth selection, `((2*p+1)*ViewSize)/(2*FogSize)`, followed by one ViewMin offset. The repaired even-half pixel-selection defect retains its original bad captures as rejection cases. This does not establish general geometry or an actual nonzero ViewRect origin.

| Model/path | Implemented meaning | Boundary |
|---|---|---|
| Prescribed | Constant source J=(.35,.60,.90), controlled `S=J*(1-T)` fixture | No UE scene-light extraction |
| ManualDirectionalHG | Explicit direction/albedo/normalized HG; `Jray=albedo*E*HG(k·(-ray),g)`, compensated optical depth and closed S/T | Incoming transmittance/visibility fixed1; source constant along each camera ray |
| NativeLFV | UE standalone LFV | Different density/coverage, light units and transport |
| LFV→VF | Local LFV density injected into UE VF | Different grid, lighting and history behavior; distinct from standalone |

The custom models have no incoming-path attenuation, volume shadows, multiple scattering, transparent integration or temporal reconstruction. Mobile execution is limited to Uniform Prescribed; desktop Manual is not mobile validation.

## Same-model quality and retained regressions

![Repaired Prescribed transmittance comparison](media/reconstruction-triptych.png)

Prescribed/radial, UE5.8.2, RX9070/D3D12 SM6, scene640×360, Full640×360 / Half320×180, 64 steps. T uses a common [0,1] display scale. This uniformly resized triptych crops accepted High and Medium B/G cells from the [original S/T sheet](media/desktop-st.png); it adds no numerical result. Reference is a high-sample preset, with convergence only in frozen cases, not universal ground truth.

The [same-model local counterexample](media/desktop-counterexample.png) and [Manual display-edge regression](media/native-edge.png) remain available. The latter uses UE5.8.2/RX9070, Manual HG, scene1280×720/half640×360/64steps. At the post-capture illustrative point (799,411), maximum RGB-code error against High is B=0 and G=2. High is a display baseline; these codes are not S/T accuracy. Guidance does not improve every pixel. See the [repair](evidence/summary.md), [Manual study](evidence/summary.md) and [selection rule](evidence/summary.md).

## Native features and model differences

![Fixed native/custom static display matrix](media/native-comparison.png)

Historical UE5.8.2/RX9070/D3D12 SM6 capture `7730eba`, fixed1280×720, manual exposure, common unshadowed scene. Ours uses Manual HG: albedo.8, g.5, relative E=(4,3,2), normalized photon direction(-1,-1,.15), Full1280×720/Half640×360 at64steps. Native density shapes, height8 coverage gate and light units differ; VF uses160×90×128 voxels. These are feature/configuration/display-path comparisons, **not a common-model numerical oracle or performance ranking**. The [derived public summary](evidence/summary.md) preserves the comparison scope; original records are identified by historical hash.

## Mobile quality and measured cost

All rows below use UE5.8.4/CL58902052, MEIZU21/Adreno750, Vulkan ES3_1 Mobile Forward HDR, MSAA1/retained native depth, ViewMin0, scene1552×720, Full1552×720/Half776×360, Uniform Prescribed64, sigma=.35m⁻¹, P1. **Quality and timing use separate APKs:** quality source `36724b7`; timing native`fea3f13`/host`fd7e335`, Development/Planes. Exact identities and values are in the [compact data](data/results.json).

| Mode | FogTotal ms | Range of four trial medians ms | Independent complete-block single-trial p50 ms |
|---|---:|---:|---:|
| Full64 | 0.650651 | 0.647083–0.655391 | 0.646771 |
| HalfB64 | 0.917552 | 0.916146–0.920208 | 0.915677 |
| HalfG64 | 1.144466 | 1.141562–1.167135 | 1.144531 |

Author values are medians of four per-trial p50s:16 balanced trials/7708frames including Off. The independent block contains4trials/1925frames. Ranges are not confidence intervals. BOP FogTotal includes marker/barrier and current Full R32-guide cost; instrumentation can perturb scheduling. Separately computed component medians need not sum to the total median. Whole GPU frame, FPS and energy are unavailable. Frequency is unreadable; battery temperature is a proxy, not proof of constant frequency.

![Fixed instrumented cost and thin-occluder quality](media/mobile-cost-quality.png)

| Separate quality APK, relative to actual Full64 | HalfB64 | HalfG64 |
|---|---:|---:|
| Planes whole-image T RMSE | 0.010698195 | 0.001453393 |
| plane-step ROI T RMSE | 0.038466699 | 0.000029972 |
| near-sky ROI T RMSE | 0.063660286 | 0.000276456 |
| Planes whole-image HDR Blue RMSE | 0.010602295 | 0.001373467 |
| ThinStripe thin-hit T MAE | 0.428046672 | 0.427970378 |

Actual Full64 is a same-model reference, not zero physical error. Guidance reduces the listed Planes errors, yet ThinStripe has120Full hits and0low-grid canonical hits; all120guided queries fall back with zero compatible neighbours. The thin-hit ROI is[1000,300,1001,420); full Planes ROI coordinates are in compact data. Passing global budgets does not recover missing thin geometry or recommend Half over Full.

![Fixed ThinStripe miss and fallback](media/mobile-thin-roi.png)

The [Planes ROI figure](media/mobile-planes-roi.png) shows controlled depth edges. Sources: [RECON quality/failure](evidence/summary.md), [PERF cost/limits](evidence/summary.md), [acceptance](evidence/summary.md). Redrawing accepted data adds no new performance sampling or full quality matrix on the timing APK.

## Reproduction and remaining work

The [public quickstart](QUICKSTART.md) builds Editor/CPU and collects new raw data for both models. M5-01 author and reviewer each used a separate project directory without generated outputs for5.8.4 builds/CPU and fresh Prescribed/Manual GPU reproduction. Eight historical core arrays match. Ideal geometry differences remain; optical checks are conditional on independently decoded actual depth. Installed engine/DDC caches may be reused, so these are not cold-cache builds.

Android support is limited to the recorded device/path/fixtures. Historical startup SIGSEGV root cause is unresolved; successful retries do not establish stability. General scenes/sizes, actual nonzero ViewRect, desktop formal timing, mobile Manual, motion/temporal behavior, cross-device support, whole-frame/FPS/energy and owner study remain open. Optimization follows measurements; startup investigation is separate.

Reading this case and [rebuilding its media](build_showcase.py) needs no historical Saved. Fresh GPU numerical validation requires new captures. The [primary-reference index](references.md) covers Beer–Lambert/source functions, HG, joint bilateral reconstruction, RDG and native UE fog. Third-party originals are excluded from the public candidate.

This is a newly derived M5-PUB-01 public summary. Original report names/commits are historical identifiers, not reachable objects in this independent Git; see [technical provenance](data/provenance.json). Original reports and full local history remain preserved.
