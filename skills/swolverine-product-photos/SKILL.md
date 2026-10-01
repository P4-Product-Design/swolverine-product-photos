---
name: swolverine-product-photos
description: Swolverine (SWOL) product-photo cleanup pipeline. Turns raw studio shots of Swolverine supplement packaging (creatine jar, whey pouch, and new products shot on the same white podium) into website-ready product images. The output is a cut-out on a 3247px transparent canvas with the house tone curve and a faint reflection, saved as an sRGB PNG in Final/PNG/ plus a layered 16-bit TIFF in WIP/. The process is pixel-accurate with zero generative fill, so label fine print survives. Use this whenever Chance or anyone else wants Swolverine product photos edited, cleaned up, cut out, retouched, colour-matched, "made to look like the creatine/whey ones", batch-processed, or exported for Shopify. Also use it for new raws landing in "Design/Swolverine/Product images/Raw", including when they only say "do the next product", "process the new shots" or "same as before for the <product> images".
---

# Swolverine product photos

You are reproducing a look Chance built by hand in Photoshop. The same work has already been done and approved for CreatineMonohydrate (Front/Back/Side), WheyIsolate (Front/Back, light-matched to the vanilla), WheyIsolate-Vanilla (Front), Equilone, 13 bundles, and the 2026-09-30 batch: Ashwagandha, BCAA (3 flavour fronts), BComplex, BetaAlanine, Collagen, CitrullineMalate, Colostrum, GreensReds, Incivra, Intra, KreAlkalyn, KrillOil, LGlutamine, Multivitamin, PRE. New products should look like they came from the same edit.

## Rules that matter most

1. **No generative anything.** No Firefly or generative fill, no AI upscaling, no "enhance", and no inpainting on or near printed areas. The reason AI tools failed for Chance is that they redraw and drop fine print: the small blue sub-heading beside the product name, the Net Wt line, nutrition facts, and the Lot/EXP code. Every step in this skill only masks, moves, resamples or tone-maps real camera pixels. If something can't be fixed that way, flag it for Chance to retouch by hand in Photoshop. Don't invent pixels.
2. **Fine print is the acceptance test.** After rendering, look at the `qa_print*.jpg` crops at 100%. Every character must be present and crisp.
3. **Chance approves before anything lands in Final/ or WIP/.** Show the contact sheet and QA crops, then wait. Never overwrite an existing Final/ or WIP/ file without an explicit yes; the script refuses unless you pass `--overwrite`.
4. **Protect Chance's Photoshop work.** The scripts drive Photoshop. They refuse to run while Photoshop has unsaved documents, because a crash would lose that work. Photoshop has crashed during this work before (see troubleshooting). If it's already open with documents, ask Chance to save and close them first.

Chance works from precise specs and likes itemised plans. Report in short numbered steps with the numbers you measured.

## Folder layout

Each person keeps their own product-images folder. Point the pipeline at it with `SWOL_PHOTO_ROOT` (or pass `--root`, or save `{"root": "/path"}` in `~/.config/swolverine-product-photos/config.json`). If none is set, the script stops and says so; ask the user where their folder is rather than guessing. It holds:

- `Raw/`: camera TIFFs, 8149×5435, 16-bit, Adobe RGB. Named `<Product>-<View>.tif`, e.g. `WheyIsolate-Front.tif`. The creatine raws have odd names; pass `--raw View=path` for files like that.
- `WIP/`: layered masters `<Product>-<View>.tif` (16-bit Adobe RGB, transparent). This folder also holds Chance's own `Product-Images.psd`, `CreatineMonohydrate.tif` and `Originals/`, which you must never modify. When Chance approves replacing an exported file, move the old PNG/TIFF into `WIP/Previous versions/` first (Chance asked for this on 2026-09-25).
- `Final/PNG/`: web images `<Product>-<View>.png` (3247×3247, 8-bit sRGB, transparent). Chance moved them here on 2026-10-01. `Final/WebP/<Product>-<View>.webp` holds a quality-90 WebP of every PNG, about 15× smaller (Chance asked for small WebPs on 2026-09-29). Don't create any other folders, such as a "renders" folder.
- `Archive/`: old material, but it holds the Aug 31 session reference raw (`CreatineMonohydrate-1 1 (1).tif`); never delete it. Chance deletes raws from `Raw/` once they're exported.
- `Final/PNG/Bundles/`, `Final/WebP/Bundles/`: group shots. Export them with `--subdir Bundles`.

## How to run it

The pipeline is one script. Intermediate files go to `~/Library/Caches/swolverine-product-photos/<Product>/` (or `--work <dir>`), never into Chance's folders until export.

```bash
SKILL_DIR="<this skill's base directory, shown when the skill loads>"
export SWOL_PHOTO_ROOT="<the user's product-images folder>"          # holds Raw/, WIP/, Final/
bash $SKILL_DIR/scripts/setup.sh                                          # once; prints the python path
PY=~/.cache/swolverine-product-photos/venv/bin/python
SW=$SKILL_DIR/scripts/swolphoto.py
P=WheyIsolate; V=Front,Back                                               # product prefix + views
```

1. **See the raws:** `$PY $SW preview --product $P --views $V`, then look at `<View>_raw.jpg`. Check that the product sits on the white podium like the earlier shoots, and note any extra raws (e.g. `+Shaker` lifestyle shots). Only process the views Chance asked for.
2. **Cut out:** `$PY $SW mask --product $P --views $V --crop x0,y0,x1,y1`.
   - This runs Photoshop's Select Subject.
   - A crop around the product plus the podium top (from the preview; e.g. the whey used `2300,150,6200,4900`) makes it faster and cleaner. The crop may cut through the podium; it must not cut the product.
   - It takes a few minutes. If it times out, Photoshop is showing a dialog; ask Chance to click through it.
   - Look at `<View>_psmask_overlay.jpg`.
3. **Remove the podium:** `$PY $SW podium-cut --product $P --views $V --guide-view Front`.
   - Select Subject often includes the podium under a pouch or bag, though for the creatine jar it didn't.
   - The script finds where the product meets the podium and traces the thin dark shadow gap under the product's bottom edge. If there's no podium in the mask, it leaves the mask alone.
   - Put the view with the least print near its base first with `--guide-view`. The whey back's "Lot:… EXP:…" line broke a naive trace, so the back reused the front's gap curve.
   - Check `<View>_podiumcut.jpg`: the red line must sit on the product's bottom edge, not on the podium seam and not on printed text.
4. **Render:** `$PY $SW render --product $P --views $V`.
   - It matches exposure, frames the product (fill-height, see the spec), applies the look, and adds the reflection.
   - Exposure is matched per **capture session** (the EXIF capture date). Known sessions in `assets/config.json` use their approved gain. Other raws from the same session are matched to that session's reference raw by comparing the podium at the same pixels, which also absorbs ISO changes (e.g. PreBundle at ISO 400).
   - If it says **NEW capture session**, the gain is only an estimate. The podium is side-lit, so its reading depends on where it's sampled. Compare the contact sheet with the existing Finals (white panels about 237–241, brand blue), adjust with `--gain r,g,b`, get Chance's approval, then add the session to `config.json` → `sessions` (reference_raw, gain, note).
   - It writes a `contact_sheet.jpg` that includes an existing Final/ product for comparison.
5. **QA:** `$PY $SW qa --product $P --views $V`.
   - Look at `qa_print1-4.jpg` (the densest print areas at 100%) and `qa_edges.jpg` (the outline on white).
   - Check the clipped-to-white % and the brand blue against the approved values.
6. **Show Chance** the contact sheet and QA crops, with a short numbered summary: scale, gain, clipping, anything to retouch by hand. Wait for approval.
7. **Export:** `$PY $SW export --product $P --views $V`.
   - It writes the PNG to Final/PNG/ and the WebP to Final/WebP/, then builds the layered TIFF in Photoshop. With `--subdir Bundles`, they go to Final/PNG/Bundles/ and Final/WebP/Bundles/.
   - The WebP is lossy quality 90 at full size: 0.2–0.5 MB against 3–6 MB for the PNG.
   - At that quality the fine print, including the creatine's tiny blue sub-heading, is indistinguishable from the PNG at 2× zoom.
   - It's decoded back and checked: transparency must match exactly and colour must be within 2.5 levels on average (it's usually 1.1–1.6), or it isn't kept.
   - `webp --lossless` gives pixel-identical files instead, about 35% smaller than the PNG.
   - If Chance re-exports a hand-edited PNG, refresh its WebP with `$PY $SW webp --product $P --views $V --overwrite`. Use `--views all` for every PNG in Final/PNG/ (add `--subdir Bundles` for the group shots).
   - It then checks Photoshop's own render of the TIFF against the PNG, and only moves the TIFF into WIP/ if they match (mean under 1 level).

Viewing images: read only the small JPEGs the script writes, never the 265MB raws or the .npy arrays. That keeps the session cheap.

## House spec (what "the look" is)

This is decoded from Chance's creatine front edit (`WIP/Product-Images.psd`, group Creatine > Front), which is the source of truth. Full details and the reasoning are in `references/spec-and-decisions.md`.

| Item | Value |
|---|---|
| Canvas | 3247 × 3247 px, transparent, 300 dpi |
| Baseline | product's lowest pixel at y = 2913 (334px bottom margin), centred horizontally |
| Size (new products) | fill-height: top margin = bottom margin = 334px, same scale for every view of a product (from the first/ref view). Too wide? Fit the width with 334px side margins. Chance chose this on 2026-09-25 |
| Look | per-channel tone curve (`assets/tone_lut.npy`) matching Chance's Brightness (Whites/Highlights/Shadows +10) plus Curves (R 39→0/225→255, G 34/223, B 32/224) |
| Exposure match | per capture session (EXIF date). 2026-08-31 creatine: gain 1. 2026-09-04 whey/PreBundle/Shaker: gain (0.977, 0.984, 0.987). A new session needs Chance's approval, then gets recorded in config.json |
| Reflection | the product silhouette flipped below the baseline, black at 10/255 opacity, overlapping the product bottom by 97px |
| Drop shadow | **none**. The PSD has one defined, but layer effects are off, so it doesn't render |
| Web export | sRGB PNG, transparent, 8-bit, embedded sRGB profile, plus a quality-90 WebP (transparency exact, sRGB profile kept) in `Final/WebP/` |
| Master | layered TIFF: Look (Curves, clipped), [Light match (Multiply, clipped), only if match-light was used], Product (ungraded full frame plus layer mask), Reflection, White background (hidden) |
| Approved brand blue (sRGB) | creatine #00BEF4, whey (light-matched) #00C3EA, whey vanilla #00C1E6; white panels land around (237–241) |

## When the job goes beyond the standard pipeline

- **Several views of one rigid product that must share an outline** (jar or tub back/side vs front, lid seated off-centre): read `references/multiview-outline-match.md`. It's the approach Chance approved for the creatine back/side. Only do it if Chance asks for matching outlines or notices a mismatch. It isn't part of the default run.
- **Match lighting to another approved shot of the same packaging** (Chance preferred the vanilla pouch's lighting, from the creatine setup, over the chocolate's flatter light, 2026-09-25): run `$PY $SW match-light --product WheyIsolate --views Front,Back --to WheyIsolate-Vanilla:Front` after `render` and before `qa`. The donor must be rendered in its own cache folder.
  - It measures the donor/this ratio on flat white label areas only, in bag-relative coordinates, then smooths it (`--smooth 0.08` of the product size, broad light only).
  - It caps the result at 1, so it only darkens, and applies it to every view.
  - Export puts it on its own clipped **"Light match (Multiply)"** layer, so Chance can dial it down.
  - Rerunning `render` removes it.
  - Approved result: the lower pouch went from 234→218 (vanilla 216), and the residual averages 1.7%.
- **Jar or tub on the riser disc** (Equilone, 2026-09-28): Select Subject takes the whole disc. Run `podium-cut --body-cols --gap-row <row>`, using the row where the mask widens from the jar to the disc; print the mask width per row to find it (Equilone: 3134).
  - The cut then traces only under the jar and drops the disc on either side.
  - It draws the base band as a tilted ellipse, tangent to the jar's sides, fitted to the middle 70% of the traced base. Equilone's residual was 0.05–0.11 px.
  - Near the corners the jar and disc are the same brightness, so nothing can be traced there. A free-width ellipse left flat ledges, and an untilted one let the dark contact line in at one corner.
  - Jars are wider than tall and small in the frame, so fill-width gives about 2.4× (soft print). Offer the creatine's scale, `--scale 1.60172`, for jar products instead.
  - **Add `--from-below` for everything on the riser** (the 2026-09-30 batch of 15 products). Tracing down from the product stops at the label's lower edge whenever print or dark contents sit near the base (gummies in clear glass, Supplement Facts panels). `--from-below` traces up from the disc's plain top instead.
    - The trace skips soft shading steps on the disc.
    - It climbs through the thin contact shadow (≤8px), so the shadow stays outside the mask.
    - A darker band than that is treated as a dark glass base.
    - The ellipse fit is robust: it ignores specks and dents.
    - Typical residual is 0.05–0.13px.
    - Gap rows: bottles 3125–3130, 1lb tubs 3133–3141, Sep 4 tabletop riser 3859–3862.
  - **Warped or dented tubs** (Collagen, Intra): the rim lifts off the disc, with dark gaps under it, and the ellipse residual comes out above 1.5px. Add `--band-max 30 --base-shape 47,-3 --follow-trace`.
    - `--base-shape 47,-3` borrows the depth and tilt of a same-size tub.
    - `--follow-trace` cuts up to the real rim where it lifts.
    - Flag the warp for Chance.
  - **Scale for a mixed batch** (2026-09-30): use the creatine 1.60172 for bottles and short tubs. Tall 1lb tubs (BCAA, Collagen, Intra, Greens, PRE) break the 334px top margin at that scale, so they share 1.4675 (Collagen fills exactly).
  - **Shots from another setup** get the scale that makes them the same canvas size as their twin packaging (Beta-Alanine/L-Glutamine 1.4369 = Citrulline's tub width).
  - **Batch driver:** `~/Library/Caches/swolverine-product-photos/_batch/` holds `products.txt` (product|View=raw name…), `run.sh <subcommand> [args]` (with `ONLY=P1,P2`), `gaprow.py` (finds the gap rows), and `overview.py` (one sheet of every render).
- **Group / bundle shots on the tabletop sweep** (`Raw/Bundle*.tif`, 2026-08-31; several products, no podium, the camera zoomed per setup). They differ from the single-product flow in four ways:
  - **Views.** Each raw is its own composition, so render each shot separately: one `render --views <n>` call per shot. That gives each shot its own fill-height scale. Pass the odd file names with `--raw <n>=<path>`.
  - **No podium.** Run `podium-cut --no-podium`. Mixed-height groups fool the gap finder: it trimmed about 32px off product bottoms before this flag existed.
  - **Exposure.** Use `--gain white:WheyIsolate-Vanilla:Front`. Products sit at different distances from the lights in each setup: the same bottle was 11% brighter in one shot than in another. Wall and podium matching gave 22–47% clipping, and product-white anchoring brought it to 0–3%.
  - **Scale.** Small groups get upscaled (bottles-only at 1.3–1.9×), so tell Chance.
  - **Where they go.** Bundles live in `Final/PNG/Bundles/` and `Final/WebP/Bundles/`; layered TIFFs stay in WIP/. Run `export --subdir Bundles` and the files land there directly.
- **Adding a product that isn't in the shot** (Bundle 12 + Intra, approved 2026-10-01). Only composite real camera pixels, taken from a shot with the same setup, camera angle and light. The single-product riser shots don't match the tabletop angle.
  - Find every edge the donor product hides in its own shot, and every edge the target shot hides. Then pick a layout where something in front still covers each hidden edge.
  - Mock up the options before building one. Driver scripts: `~/Library/Caches/swolverine-product-photos/_batch/bundle12_intra/` (`segment.py`, `compose.py`, `build_tiff.py`).
  - Read a layered master's Product layer straight from the TIFF: tag 37724 → `Lr16` block → `psd_tools` `LayerInfo.read` with a 4-byte length prefix. Re-grade it and check it against the PNG (Bundle-105 matched within 0.21 levels).
  - Bottle edge against a tub: trace the colour edge from the tub's side. Where print sits at the edge, use the twin bottle's measured width profile instead (bottle symmetry holds within about 1–2px). Smooth each unbroken run of rows separately.
  - Match exposure on the same packaging's whites (bottle against bottle), not on a printed blue: blue and white disagreed by up to 5%.
  - Edges that stood against the backdrop carry a light halo when moved over a colour. Pull them in 2px (masking only).
  - The layered TIFF has a "Products" group (Normal blend) with one masked full-frame layer per source and Look unclipped at the top of the group. Verify it like the standard export (0.28 levels).
  - `compose.py` overwrites `12_*.npy`; the plain render is kept as `12plain_*`, and segmentation must read that.
- **Exposure when the session reference raw is gone.** Chance deletes processed raws.
  - The Aug 31 reference survives as `Archive/CreatineMonohydrate-1 1 (1).tif`, which the script finds by itself.
  - The Sep 4 whey reference is gone. The approved Sep 4 gain clipped the tabletop tubs (Beta-Alanine, L-Glutamine) 26–32%, because that setup was brighter than the whey.
  - Anchor to the label white of the same packaging shot on Aug 31 instead (`--gain white:CitrullineMalate:Front` gave 0.933–0.941). Then apply one gain to all views, keeping the session's colour balance: 0.9323,0.9390,0.9418.
  - Same-session exposure changes are caught automatically: BCAA ×1.078, Incivra at ISO 500 ×0.907.
- **Photoshop hangs, times out or crashes; a mask is wrong; colours look off:** read `references/troubleshooting.md`.
- **Product-specific notes** (fine-print hot spots, what Chance retouched by hand, glossy areas) are in `references/spec-and-decisions.md` under "Products".

## Known limits to tell Chance about, not hide

- Dust specks, dents and scuffs are real and stay. List them for Chance to fix by hand; don't paint them out.
- Upscaling (scale > 1, e.g. the creatine jar at 160%) adds no detail. Say so if a small product ends up upscaled.
- About 1% of pixels clipping to white in glossy highlights is normal and matches Chance's edit.
