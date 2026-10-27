# UEFogLab MIT showcase

Original project work: MIT / Copyright (c) 2026 xiaoran6657. UE and third-party rights remain reserved. This is a local publication candidate; no remote repository or deployment is claimed.

- [中文案例](CASE.zh.md) / [English case](CASE.en.md)
- [48-second silent still-image edit](media/showcase.mp4) / [poster](media/cover.png)
- [Compact results](data/results.json), [image sources](data/source-map.json), [technical provenance](data/provenance.json), [derived summary](evidence/summary.md)
- [Desktop quickstart](QUICKSTART.md), [primary references](references.md), [media SHA/derivations](media-manifest.json)

The old pre-license-decision M5 media/reports retain their historical identities. New MIT caption bands and the final slide produce a new cover/MP4 and new manifests; other numerical fields and accepted raw-image pixels remain unchanged. Historical commit names identify local development, not reachable public Git objects.

To regenerate in the standalone public snapshot, use an existing Python3.12 with NumPy2.3.5/Pillow12.3.0/Matplotlib3.10.9, installed fonts and FFmpeg. From its root, run python docs/showcase/build_showcase.py --output out/new-showcase --ffmpeg <existing-ffmpeg-path>. Optional --font/--font-bold select installed fonts; none are bundled. The generator checks source PNG hashes and reads only committed compact data/images. Output must be new and below Saved/out/work. The private development tree keeps this edition in releases/mit-20261027; use that edition's actual script path there.

Source report SHA values distinguish exact Git LF bytes from original frozen working bytes. Originals are excluded local records, identified by hash and historical relative name. No old Saved, raw device logs or theory originals are needed to view or regenerate this showcase. New GPU evidence requires fresh captures; formal timing must be separate from readback/screenshots.
