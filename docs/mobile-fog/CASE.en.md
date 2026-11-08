# Repairing thin occluders in mobile fog and measuring the cost crossover

UEFogLab · UE 5.8.4 · C++ / HLSL / Vulkan · [中文](CASE.zh.md)

On a fixed ThinStripe test on MEIZU21, reintegrating only fallback pixels reduced transmittance MAE on the occluder from **0.3912** for the original half-resolution path to **1.63×10⁻⁵**. The repaired path met the preset quality budgets and reduced fog-pass GPU time from **3.366 ms** to **1.396 ms (−58.5%)** versus full resolution in the same APK.

![GPU diagnostic readback: the half-grid depth samples miss the stripe; the repaired transmittance recovers it and agrees with the high-sample reference](media/thinstripe-gpu-crop.png)

All panels use the same 11×12 crop. Dots are the observed half-grid depth sample locations, and the orange column marks the thin occluder. Transmittance uses the same [0,1] color scale across the three right panels. The reported means and errors use all 120 pixels that hit the stripe. [Figure provenance](results.json)

## Find where the occluder disappears

The scene is 1552×720 and the half-resolution fog grid is 776×360. In the test fixture, the full-resolution depth buffer hits the stripe at **120 pixels** in column 1000 (zero-based). The actual half-grid depth samples hit it **zero times**. Guided reconstruction cannot find a suitable sample and falls back to the neighbor with the largest spatial weight. That sample integrated over a longer fog segment, yielding mean transmittance **0.536** on the stripe versus **0.927** in the reference. The original half path's whole-image T MAE was only about **0.000104**, so an image-wide mean alone would hide the failure.

## Reintegrate only when guided reconstruction falls back

The low-resolution integration stays in place. At full resolution the pixel shader computes four spatial weights multiplied by depth weights. When their FP32 sum is ≤ `1e-6`, the existing reconstruction falls back. The repair uses the output pixel's own center ray and native depth to clip the visible segment of spherical fog, then recomputes optical depth with the same **64 midpoint steps** as FullN. The source function J is fixed along the ray. The shader computes transmittance T and scattering S, rounds them to the same half-precision storage convention and composites `Cout = PreExposure × S + T × Cin` in the same pixel shader.

The GPU diagnostic recorded **120 selected pixels and 7,680 executed density iterations**. Their stored S/T matched FullN bit for bit; the remaining pixels retained the original Half S/T. A shifted stripe at a different depth checked the selection and work again. The vacuum and before-entry controls still selected pixels but executed **zero** density iterations. The validation also checked the HDR output consumed by the engine. [Guided reconstruction](../../Plugins/UEFog/Shaders/Private/UEFogMobileOptimization.usf#L35) · [Repair and composition](../../Plugins/UEFog/Shaders/Private/UEFogMobileRepair.usf#L5) · [Quality validation](README.md#quality)

## Measure quality and cost separately in the same APK

Quality used a matched Full1024 reference after checking **256 → 512 → 1024** convergence with actual GPU readbacks. Cost used separate runs with diagnostics disabled and the shared Vulkan timestamp span for FogTotal. The main estimate is the median of four trial p50s per profile. A separate independent block ran each profile once.

| Path | Author median FogTotal | Independent trial p50 | Thin-hit T MAE |
|---|---:|---:|---:|
| Full resolution (FullN) | 3.366 ms | 3.358 ms | 1.63×10⁻⁵ |
| Original half resolution (FusedHalfGN) | 1.339 ms | 1.360 ms | 0.3912 |
| Half resolution + local reintegration | **1.396 ms** | **1.389 ms** | **1.63×10⁻⁵** |

The preset thin-hit T MAE limit was **0.05**. The repair met that limit and the other frozen quality budgets. Against the original Half path, the median paired cost was **+0.057 ms**. Against FullN, every author block and the independent block observed lower FogTotal. The two comparisons show the cost of restoring the occluder and the savings against integrating the whole image. [Quality and cost figure](media/repair-cost-quality.png) · [Timing protocol summary](README.md#timing)

The sparse fixture reintegrated about **0.011%** of the image. Full/High is the default.

## When does local reintegration become more expensive?

The WIDE follow-up used a separate Development Android ASTC APK on the same MEIZU21. Each fixture was compared within that APK. The two wide fixtures shared 632 stripe columns, an occluder depth of 600 cm and a 1552×720 scene. Only stripe height differed between them. Coverage counts pixels that executed the density loop. Of the 400,032 fallback pixels in the highest tier, 20,118 had an empty visible fog segment and executed zero iterations.

| Measured reintegration coverage | Reintegrated pixels | FullN → repair (author FogTotal) | Repair / FullN (author / independent) |
|---:|---:|---:|---:|
| 1.37457% | 15,360 | 3.367 → 1.483 ms | 44.04% / 44.23% |
| 19.69734% | 220,106 | 3.434 → 2.694 ms | 78.44% / 79.13% |
| 33.99860% | 379,914 | 3.493 → 3.646 ms | 104.39% / 104.49% |

FullN and repair met the per-fixture quality budgets against converged references. Original Half exceeded the 0.05 thin-hit transmittance MAE limit in all three. The author values are medians of three trial p50s per profile and fixture. The independent block ran each profile once per fixture. **The 19.70% fixture retained the 10% timing margin. At 34.00%, repair took longer than FullN in all four matched blocks.** Intermediate heights remain unmeasured. Stripe height also changes occlusion and the fog ray lengths, so the conclusion applies to this device, fixture family and fog interval.

![WIDE results for three fixtures, showing fog GPU time, repair-to-FullN ratios, paired differences and thin-hit transmittance error](media/wide-cost-quality.png)

[Compact WIDE data](wide-results.json) · [Source and figure provenance](wide-source-provenance.json) · [Run the wide fixtures](README.md#wide-coverage-reproduction)
