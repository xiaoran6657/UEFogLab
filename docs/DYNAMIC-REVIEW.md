# Independent review of the controlled motion display

2026-10-31. An independent coordinator reviewed author candidate
`f1b0a55d68dcdc4e55fcdf14a6126af86f3c4bbb`, including C++ changes, continuous
capture, synchronization, composition, source provenance and the website candidate.
No blocking finding remained in this bounded display scope. This documentation
follow-up changes no runtime source, capture inputs, images or encoded movie.

The review checked all 15 source sequences / 3,240 frames, their actual rendered
time/view/transform/shader or native-proxy state, cross-mode synchronization and
all PNG hashes. First/middle/last GPU path witnesses were replayed separately from
state records; a marker alone was not treated as a fog dispatch. Public Git,
working files and source ZIP matched, with 50 runtime/configuration files equal
to the tested implementation. The old annotated tag and its historical data remain.

New independent execution on UE5.8.4 / RX9070 / D3D12 SM6 included an up-to-date
Editor build, CPU 1/0/0, a 30-frame moving-fog VF shot, a 30-frame rotating-light
Guided shot, and Prescribed/Manual 640x360 numerical readbacks. All eight legacy
core arrays matched their repaired historical fixtures. Optical checks remain
conditional on actual rasterized depth where the diagnostic geometry differs at
boundaries; this is not a new numerical dynamic-ST validation.

The complete 36-second / 1,080-frame H.264 movie decoded without error. Real Edge
headless checks covered Chinese/English pages at desktop and 390px, native
play/pause/progress drag/resume, no initial video transfer, theme and language
navigation, and uninterrupted playback through the final frame. These are actual
browser tests, separate from static build checks, and are not physical-phone or
headed manual tests. Website deployment is verified separately from this source
review; visit the portfolio for the currently deployed edition.

The first four sections compare custom Full64, standalone LFV and LFV-density
injected VF; the final section compares Full64, HalfB64 and HalfG64. The inset is
the same 320x320 source-pixel region displayed at 2x, while context images are
displayed at half size. Different native models/units, explicit manual HG input,
rigid translation and depth clipping retain their stated meaning. General motion
stability, dynamic numerical accuracy, Android motion and new performance results
are outside acceptance. Owner learning is separate from agent implementation and
review.
