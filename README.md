# swolverine-product-photos

A Claude Code plugin that turns raw studio shots of Swolverine (SWOL) supplement packaging into website-ready product images: a cut-out on a 3247px transparent canvas with the house tone curve and a faint reflection, saved as an sRGB PNG (plus a small WebP) for the site and a layered 16-bit TIFF master.

It reproduces the look Chance built by hand in Photoshop, and it never uses generative fill, AI upscaling or inpainting. Every step only masks, moves, resamples or tone-maps real camera pixels, so label fine print (the Net Wt line, nutrition facts, Lot/EXP) survives untouched.

## Prerequisites

1. **A Mac with Adobe Photoshop 2026.** The pipeline drives Photoshop through AppleScript (Select Subject for the mask, and building the layered TIFF). It won't run on Windows or Linux. If your Photoshop has a different name, set `SWOL_PS_APP` (for example `SWOL_PS_APP="Adobe Photoshop 2027"`).
2. **Python 3.** `scripts/setup.sh` builds a private virtualenv at `~/.cache/swolverine-product-photos/venv` (numpy, opencv, tifffile, pillow, scipy). Claude runs it for you the first time.
3. **Your own product-images folder.** It holds `Raw/` (camera TIFFs named `<Product>-<View>.tif`), `WIP/` (layered masters) and `Final/` (web PNGs). Nothing in this repo is tied to anyone's location. Tell the pipeline where yours is by any one of:
   - `export SWOL_PHOTO_ROOT="/path/to/your/Product images"` in your shell
   - saving `{"root": "/path/to/your/Product images"}` in `~/.config/swolverine-product-photos/config.json`
   - passing `--root /path/to/folder` on each run

   If none is set, the script stops and says so.
4. **Claude Code access to this repo.** It's public, so there's nothing to authenticate.

## Install (one time, per person)

Claude Desktop has three tabs: Chat, Cowork, and Code. `/plugin` commands only work in **Code**.

In the **Code tab**, type:

```
/plugin marketplace add P4-Product-Design/swolverine-product-photos
/plugin install swolverine-product-photos@swolverine-product-photos
```

Once installed, it shows up under **Customize → Manage plugins** in the Code tab, where you can also disable or remove it.

## Update

Whenever the repo gets new commits, type this in the Code tab:

```
/plugin marketplace update swolverine-product-photos
```

## Use it

Point Claude at new raws and ask for the usual treatment, for example:

> Process the new shots in my Raw folder, same as the creatine and whey ones.

or "do the next product", or "make these look like the other product images". Claude previews the raws, cuts out the product, matches exposure to the house podium, renders, runs QA crops on the fine print, and **stops for your approval before anything is written to `Final/` or `WIP/`**. It never overwrites an existing export unless you say so.

## Exposure matching

Exposure is matched per capture session (the EXIF capture date). `assets/config.json` holds the approved gain for each known session and the reference raw it was measured against. If a reference raw isn't in your `Raw/` or `Archive/` folder, the approved session gain is still applied but within-session exposure drift isn't corrected, and the script says so. A new session gets an estimated gain only; check it against the existing Finals and get the look approved before adding it to `config.json`.

## What's in this repo

```
swolverine-product-photos/
├── .claude-plugin/
│   ├── plugin.json         # plugin manifest
│   └── marketplace.json    # marketplace catalog (this repo is both, for a single-plugin install)
├── skills/
│   └── swolverine-product-photos/
│       ├── SKILL.md                 # the skill: rules, folder layout, and the step-by-step run
│       ├── scripts/
│       │   ├── setup.sh             # one-time Python environment setup
│       │   └── swolphoto.py         # the pipeline (preview, mask, podium-cut, render, qa, export, ...)
│       ├── assets/
│       │   ├── config.json          # house spec and approved capture-session gains
│       │   ├── look_curves.json     # tone curve points
│       │   ├── tone_lut.npy         # tone lookup table
│       │   └── AdobeRGB1998.icc     # colour profile for the raws
│       └── references/
│           ├── spec-and-decisions.md       # why each number in config.json is what it is
│           ├── troubleshooting.md          # Photoshop crashes, mask problems, fixes
│           └── multiview-outline-match.md  # keeping Front/Back/Side outlines consistent
└── README.md
```

## Updating the skill itself

Edit the files under `skills/swolverine-product-photos/`, bump the `version` in both `.claude-plugin/plugin.json` and the plugin entry in `.claude-plugin/marketplace.json`, commit, and push. Everyone who's installed the plugin picks up the change the next time they run the update command above. The house look in `assets/config.json` should only change with Chance's sign-off.
