# Troubleshooting

## Photoshop

- **The first script call hangs, or "AppleEvent timed out" (-1712).** Photoshop is waiting on a dialog: a first-run prompt, a script-permission prompt, a colour-profile question, or Select Subject cloud vs device. On 2026-09-24 Chance had to change a Photoshop setting before scripts would run. Ask Chance to look at Photoshop and click through, then rerun. The scripts set `displayDialogs = NO`, but first-run prompts still appear.
- **"Connection is invalid" (-609) means Photoshop crashed.** Check with `pgrep -f "Adobe Photoshop 2026.app/Contents/MacOS"`. A known cause is loading a selection from **another open document** (`setd` / `fsel` with a `Dcmn` reference). That crashed Photoshop 2026 every time. The pipeline instead embeds masks as extra alpha channels in the input TIFF (TIFF `extrasamples=0`, which Photoshop opens as ordinary alpha channels) and loads them with `doc.selection.load(doc.channels[i])`. Keep it that way.
- **Before any new or untested JSX**, have Chance save and close their documents. The scripts check and refuse to run while any document is unsaved. Relaunching is fine: `open -a "Adobe Photoshop 2026"`, then poll `osascript -e 'tell application "Adobe Photoshop 2026" to get version'` until it answers.
- **Debugging a JSX build:** split it into cumulative steps (open → load selection → add mask → curves → shadow → save) and run them one by one. That's how the crash above was found.
- A curves adjustment layer made with `Mk`/`AdjL`/`Crvs` and `Grup: true` gives a clipped layer. Photoshop holds values flat beyond the first and last points, so the points file doesn't pin (0,0) or (255,255).

## Masks

- **Select Subject included the podium** (a pouch or bag on the podium): that's what `podium-cut` handles. If the gap row is detected wrongly, pass `--gap-row <raw y>` (the row where the product's bottom meets the podium top) and rerun.
- **The trace jumps onto printed text or the podium seam:** process the cleanest view first with `--guide-view` so the others reuse its gap curve. The camera and podium don't move between views, so the curve carries over.
- **Soft or flat-cut edges at the base corners, or on pale edges against the white wall:** Select Subject is weakest where white plastic meets the pale wall. Don't try GrabCut (it failed on the creatine: chamfered lid corners, lost the lid rim). Options, in order:
  1. accept it and tell Chance;
  2. for multi-view rigid products, match to an approved view's outline (multiview-outline-match.md);
  3. Chance refines the mask by hand. The layered TIFF's Product layer has full-frame pixels under the mask for exactly this.
- **Check at 100%**, not on the contact sheet. Use contrast-boosted crops (1st–99th percentile stretch) to see where a white product really ends.

## Colour

- **The whole product looks dull or blue-grey in a preview:** the preview probably wasn't converted from Adobe RGB. Viewers that ignore profiles show Adobe RGB data desaturated. The script's JPEG previews are converted to sRGB. If you make your own, use `adobe_to_srgb()` from `swolphoto.py`, or `sips -m "/System/Library/ColorSync/Profiles/sRGB Profile.icc"`.
- **Whites clipping a lot (over 3%) or the product looks too bright:** the exposure gain is probably off. Check `<View>_podium_sample.jpg`: the green box must be on the podium's plain side band, not the seam, a shadow or the product.
- **Brand blue far off the approved values** (#00BEF4 creatine, #00C9F0 whey; the two packages print slightly differently): check the gain first, then ask Chance before changing anything. Don't hue-shift toward a target.

## Performance and tokens

- Raws are about 265MB each. Load them once per step and never print or read arrays into the conversation.
- Photoshop steps take about 1–3 minutes per view. Run them in the foreground with a long timeout.

## Disk space (2026-10-01)
- The cache costs about 1 GB per view: the raw-size `_mask.npy` is 177 MB, and `_rgba`, `_ungraded`, `_psmask` and `_alpha` add the rest. A 46-view batch filled Chance's disk mid-export, and Photoshop's Save a Copy failed with "disk is full".
- Check `df -h /` before a batch. `*_ps_input.tif` and `*_psrender.png` are always safe to delete, because export recreates them.
- When the disk fills, Bash itself can't run (it writes its output to /private/tmp). Use the terminal panel (`run_in_terminal`) to free space.
- A failed save leaves Photoshop's temporary **"verify"** duplicate open and unsaved, and the unsaved-document guard then blocks every later JSX. If "verify" is the only unsaved document, it's the script's own scratch copy: close it without saving, then rerun.
- Re-export with `--overwrite` only the views whose TIFF didn't verify; the log line `-> OK` marks finished views.
- On 2026-10-01 Chance had the cache cleared (42 GB → 378 MB) after export. Product caches now hold only meta.json and JPEGs, so changing an exported product means starting again from `mask`. The anchor donors were kept: `WheyIsolate-Vanilla/Front_{ungraded,alpha}.npy` (match-light, bundle white anchor) and `CitrullineMalate/Front_{ungraded,alpha}.npy` (Sep 4 tub anchor). Clear the same way after future batches, and keep the donors.
