# Raster pictograms: classified as pixels, tracing deferred

## Goal

Collect pictograms that exist only as pixel graphics (PNG, GIF, BMP, ICO) from
every source, without tracing them to vectors yet. Every classifier already
works on rendered pixels, so a raster pictogram can be classified directly.
Keep the original; derive a black-and-white version for the catalog; classify
both and report where they disagree, which shows where the conversion lost
meaning.

## Representation

- A raster pictogram is stored as a minimal SVG wrapping the image as a data
  URI (`<svg data-handdown="raster"> <image href="data:image/png;base64,…"/>`).
  resvg renders it, so metrics, grouping, embeddings, vision models, Claude
  sheets, the review app and the vault work unchanged. Images up to 64 px are
  rendered with `image-rendering: pixelated`, so pixel icons stay crisp.
- `raw_svg` holds the original (converted to PNG, first frame of an animated
  GIF, at most 1024 px on the long side); `format = 'raster'`.
- `process` turns it into the normalized pictogram: composited on white,
  grayscale, Otsu threshold to 1 bit. Icons whose shape is in the alpha
  channel (light glyphs meant for dark backgrounds) use the alpha mask.
  Dark full-frame backgrounds are not inverted: a white symbol on a black
  square is a sign frame, and frames carry meaning. `color_class` is `native` when the
  original already has two tones, else `threshold`.
- `traced` stays 0. Tracing (potrace, exact squares for pixel art) is deferred.

## Sources

- **Repositories (git-svg, npm-svg)**: PNG, GIF, BMP and ICO files are taken
  next to SVGs, except where an SVG of the same name exists (the vector is
  kept), and only the largest size per name ("16/save.png", "32/save.png").
  Images more than twice as wide as high (or the reverse), larger than 1,600 px or smaller than
  12 px are skipped (screenshots, banners, spacers). Repositories rejected
  earlier for having no SVGs are harvested again with rasters counted.
- **Wikimedia Commons**: PNG and GIF files of the pictogram categories, fetched
  as 512 px thumbnails, with their per-file license.
- **Web sources** (a generic adapter: pages, link pattern, zip archives):
  Sclera, Gerd Arntz (Isotype), the Olympic pictograms, ISO 7010 at
  freesvg.org, JIS Z 8210 (ecomo), GHS (PubChem), the disability access
  symbols (Graphic Artists Guild), Blissymbolics.
- **ARASAAC** (API, CC BY-NC-SA): the black-and-white variant of each
  pictogram, with its keywords.
- **Needs the operator**: Noun Project and OpenSymbols require API keys;
  SVG Repo and piktogramm.de (Otl Aicher, licensed commercially) are recorded
  as references, not harvested, until their terms allow it.

## Classification of both versions

- `bench-run --variant original` renders the original instead of the
  black-and-white version. For raster pictograms the vision model answers
  both; both answers go to the `classification` log with
  `context.variant`, and the object is taken from the original.
- `handdown raster-disagreements` lists pictograms whose two answers resolve
  to different objects, for review.

## Impact

- Functionality: pixel-only sources join the catalog; tracing can follow later
  from the stored originals.
- Resources: originals are stored inline (≤ 1024 px PNG); the database grows
  by roughly the size of the harvested images.
- Licenses: several raster sources are non-commercial (ARASAAC, Sclera); the
  license is recorded per source or item, as for SVG sources.
- Compatibility: nothing downstream changes; `format = 'raster'` tells the
  two kinds apart.
