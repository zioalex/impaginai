# Spike: photos flying in to form a face

Not part of the album pipeline. A one-file experiment to see whether 1,000–2,000
photos of one person can be animated into a photomosaic of that person's face.

```bash
python face_mosaic.py face.jpg photos_dir/ out.mp4 --grid 48 --tint 0.55
python face_mosaic.py --demo out.mp4    # no photos needed; needs scikit-image
```

Needs `numpy`, `Pillow`, `scipy` (all already in `requirements.txt`) and
`ffmpeg` on the PATH. `--demo` also needs `scikit-image` for its sample images,
which is not a project dependency.

## How it works

1. The face is cut into a `grid × grid` set of cells; each cell, and each photo
   (centre-cropped square), is described by a 2×2 grid of mean Lab colours.
2. Photos are assigned to cells with the Hungarian algorithm in rounds, so every
   photo is used once before any photo is used twice.
3. Each photo is tinted part-way toward its cell's colour (`--tint`, 0–1). Photos
   of one person are too alike in colour to draw a face without it; this is the
   trade-off between "photos visible" and "face recognisable".
4. Frames: photos start scattered and enlarged, fly in from the centre of the
   face outward, land, hold, then the camera zooms into the full-resolution
   mosaic until single photos are visible. Frames are piped to ffmpeg (H.264).

With 1,500 demo photos on a 48×48 grid (2,304 cells) a 10-second 960×960 video
takes about 10 seconds.

## Already out there

- [Mazaika-Animation](https://mazaika.com/mazaika-animation.html): commercial,
  does this kind of assembly animation.
- After Effects: build the mosaic elsewhere and animate it with Card Dance
  ([Adobe forum thread](https://community.adobe.com/questions-529/how-would-i-create-a-mosaic-starting-on-one-image-and-then-zooming-out-to-be-someones-face-72512)).
- Static photomosaic generators are plentiful (e.g.
  [codebox/mosaic](https://github.com/codebox/mosaic),
  [photomosaic on PyPI](https://pypi.org/project/photomosaic/)); no open-source
  fly-in animator was found.

## Ideas if this goes further

- Start on one photo and pull out to reveal the face (the classic shot), or have
  one photo land last on an eye.
- Detect and crop faces in the source photos so the tiles read as portraits.
- Larger grid (64×64) with ~2,000 photos for sharper eyes and mouth.
