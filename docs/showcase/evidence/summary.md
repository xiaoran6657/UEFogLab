# Derived public evidence summary / 新派生公开证据摘要

Prepared for M5-PUB-01 from accepted local-development records. This document is a new summary, not a rewritten frozen report, raw capture, current-commit device receipt or independent numerical proof. Historical source IDs and exact report hashes are in [compact data](../data/results.json) and [provenance](../data/provenance.json); the original objects/history are not included in this standalone Git.

## Models, versions and correctness

Historical desktop figures use UE5.8.2 / CL56702186, RX9070 / Win64 / D3D12 SM6. Prescribed uses J=(.35,.60,.90). ManualDirectionalHG uses explicit direction/albedo/HG, with incoming transmittance and visibility fixed to one, a ray-constant source, compensated optical depth and closed S/T. Custom fog does not extract UE lights or implement incoming attenuation, volume shadows, multiple scattering, temporal reconstruction or transparent integration.

The repaired integer centre-depth selector is shared by both models. Old defective even-half data remain historical rejection cases; current repaired figures retain their own identities. Scene and fog resolutions are separate. Unexposed S/T are reconstructed before linear-HDR composition Cout=PreExposure*S+T*Cin. Geometry is cm, extinction inverse metres. The Reference preset has recorded convergence only within fixed cases and is not universal ground truth. The static native LFV/VF comparison records density, units, feature and history differences and does not rank common-model numerical accuracy.

M5-01 used two fresh source directories and UE5.8.4 / CL58902052: author Prescribed High Full64 scene=fog321x181, independent Manual Medium64 guided scene640x360/fog320x180. Each clean Editor build performed17 actions and each CPU automation returned1 success/0 failure/0 warning. Eight core arrays matched their respective earlier captures. Optical checks conditional on independently decoded actual depth passed; strict ideal geometry remained false. Engine/DDC reuse was permitted, so no cold-cache claim follows. This is historical M5 evidence, not a claim that those old runs used the new public Git commit.

## Mobile quality and instrumented cost

Only the recorded MEIZU21 / Adreno750 / Vulkan ES3_1 Mobile Forward HDR / Development path is covered. Scene1552x720, Half776x360, ViewMin0, MSAA1, Uniform Prescribed64, extinction.35m^-1, preExposure1. Quality source36724b7 and timing native fea3f13 / host fd7e335 are separate APKs. Eleven RECON matrix cases and six actual-Full quality pairs were accepted only within their frozen conditional ST/reconstruction/HDR scope. Actual Full is a same-model GPU reference, not zero physical error.

Four balanced blocks contain16 trials/7708 frames including Off; an independent complete block contains4 trials/1925 frames. BOP FogTotal medians of four trial p50s are Full64 .650651ms, HalfB64 .917552ms and HalfG64 1.144466ms. Their trial-median ranges are .647083–.655391, .916146–.920208 and1.141562–1.167135ms. Independent single-trial p50s are .646771, .915677 and1.144531ms. Ranges are not confidence intervals. Markers/barriers and Full's R32 guide contribute and perturb scheduling; component medians are not additive. Half integration is cheaper here while total fog cost is higher. Full/High stays the default.

ThinStripe has120 actual Full hits and0low-grid canonical hits. All120 guided queries use zero-compatible fallback; thin-hit T MAE is .428046672 for HalfB and .427970378 for HalfG in ROI[1000,300,1001,420). Planes listed T/HDR errors and fixed ROIs remain in compact data. Global budgets do not establish thin-object recovery or recommend Half over Full. Odd775x359 is a diagnostic fog grid, not an odd scene.

Whole GPU frame, FPS and energy are unavailable. Frequency is unreadable and battery temperature is only a proxy. Historical startup SIGSEGV root cause remains unresolved. No broader Android stability/compatibility or new public-snapshot phone validation is claimed.

## New packaging and public interpretation

The independent public source keeps the accepted Config/Plugins/Source/uproject Git bytes. Environment selection, portable entry documents, historical locator metadata and included material differ as explicitly listed in provenance. New MIT captions/media are separate derivatives; unchanged numerical fields and original image bytes remain fixed. The media generator needs only included compact data/PNGs and external tools/fonts. Its48-second silent edit has six still slides;24fps is an encoding property, not measured real-time performance. Native controls/no-autoplay/preload-none are browser gates, distinct from decode/build checks.

The local candidate delivery records new clean-source build/CPU/two-model tests and new media/Astro browser verification against their actual public commit and SHA. Those execution receipts are separate from this historical summary. No private review logs, original device serials, binaries or overwritten historical DLL/Build.version references are dependencies of this summary. No results are inferred from the13 old binary/version references that the latest local acceptance could not reverify.

Coding agents assisted development, experiments and independent review. Owner study remains unconfirmed. Integration, repository creation, tag/push and website deployment are separate actions; this candidate does not claim they occurred.
