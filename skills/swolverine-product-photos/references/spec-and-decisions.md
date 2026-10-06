# Spec, decisions and product notes

This explains why each number in `assets/config.json` is what it is. Read it before changing anything, or when Chance asks why something looks the way it does.

## Contents
1. Where the look came from
2. Framing
3. Exposure matching between shoots
4. Contact shadow
5. Colour management and outputs
6. Products
7. What Chance corrected along the way

## 1. Where the look came from

Chance hand-edited one image, the creatine front, in `WIP/Product-Images.psd` (group Creatine > Front):
- **"Remove tool edits copy 4"**: the raw, cropped to a 3247 square and scaled 160.17%, no rotation. Its layer mask is the jar cutout. The Remove tool was used on it in two places: the edge of the supplement-facts panel showing along the right side of the jar, and the dark contact line and notch at the base.
- **"Light 1"**: Photoshop 2026's new Brightness adjustment with Whites +10, Highlights +10, Shadows +10. The legacy brightness/contrast fields read 0; the real values are in the content-generator data.
- **"Curves 1"**: per-channel black and white points: R 39→0 / 225→255, G 34 / 223, B 32 / 224. This neutralises the lavender cast of the wall and lifts contrast.
- **"Remove tool edits"** at 10/255 opacity with a black Colour Overlay: the reflection. It was the house look until 2026-10-06, when Chance replaced it with the contact shadow (section 4).
- A drop shadow (20%, 8px, multiply) is defined, but layer effects are **disabled**, so it doesn't render. Don't turn it on; the contact shadow in section 4 is a separate layer.

The Brightness adjustment isn't documented, so the combined adjustment was measured as a per-channel lookup table (`assets/tone_lut.npy`, 1024 entries per channel, input = raw Adobe RGB encoded value). It matches Photoshop's own render to 0.1 of a level on average; it's pointwise, not local. `assets/look_curves.json` is the same curve as 8 to 14 Curves points per channel for the layered TIFF, within about 0.7 of a level. On the creatine front only, 0.08% of pixels (text edges) differ by up to 8 levels, because the Brightness tool treats hard edges slightly differently.

## 2. Framing

- Canvas 3247 × 3247, which is the size of Chance's PSD. Chance wants images "as large as possible", and this is that size.
- Baseline y = 2913: the creatine jar's bottom in Chance's edit. Every product's lowest pixel sits there, so products line up in a grid.
- Horizontal: product bounding-box centre on the canvas centre.
- **Fill-height rule for new products** (Chance's choice, 2026-09-25): the product spans from y = 334 to y = 2913, so the top margin equals the 334px bottom margin. Every view of the same product uses the first view's scale, so front and back are the same size. The whey pouch came out at 0.648× (downscaled with area averaging, so it's sharper than the raw), about 1908 × 2579px.
- The creatine jar is the exception: it kept Chance's hand-set 160.17% (about 1759 × 1794px). It predates the rule, and it's an upscale, which adds no detail.
- Products don't appear at real-world relative size. If Chance ever wants that, it's a new decision.

## 3. Exposure matching between shoots

Each capture session has slightly different light, so exposure is matched **per session**, identified by the EXIF `DateTimeOriginal` date.
- **2026-08-31** (creatine, 1/200s f/8 ISO 320): gain (1, 1, 1). Chance's edit defines the look.
- **2026-09-04** (whey front/back, `+Shaker`, `PreBundle`; 1/200s f/8, ISO 320 except PreBundle at ISO 400): gain (0.977, 0.984, 0.987), approved by Chance on the whey. It was derived by matching the podium's left-centre side band (whey rows 4700–4850, cols 3700–4300) to the creatine podium (rows 3450–3600, cols 3800–4200, median RGB 209.5 / 206.3 / 208.3).
- **Within a session**, the camera and podium don't move. So another raw is matched by comparing the podium at identical pixel coordinates with the session's reference raw. That's exact, and it absorbs ISO or strobe changes. Ratios within 0.5% are treated as strobe noise and ignored, so every view of one product shares the same gain.
- **Framing can change within a session.** WheyIsolate-Vanilla-Front was shot on 2026-08-31 (the creatine session, ISO 320), but zoomed in with the bag on the riser disc. So the same-pixel box landed on the riser in one raw and the draped cloth in the other, giving a false 0.80 gain. The script now also compares the top wall corners. If the wall ratio and the podium ratio disagree by more than 3%, it trusts the wall. For the vanilla, the wall ratio was 1.004, so the gain is 1, and the riser discs measured the same in both raws (209.5/206.3/208.3 vs 209.6/206.8/208.3).
- **A new session** can't be matched automatically with confidence. The podium is side-lit, from the right in both known sessions, and the gradient differed a lot: creatine podium 202→212 left to right, whey 197→223. So "the podium's brightness" depends on where you sample; a centre sample made the whey about 3% too dark in testing. The script gives a first estimate. Confirm it against the existing Finals, get Chance's approval, and record it in `config.json`.
- Don't use the wall as a reference: it's farther from the product and lit differently.

## 4. Contact shadow

Since 2026-10-06 every product sits on a soft contact shadow; the reflection is retired. Chance compared it with the website's current images and asked for "a similar subtle shadow instead of the reflection", then for the closer-to-site version, "a tad lighter".

- **Source.** The shadow is lifted from the old website creatine render (800px WebP), not drawn. `assets/shadow_template.npz` holds its darkness (255 − L) around that jar's base. The jar itself is filled in from its surroundings (Telea inpaint) so the lookup never reads jar pixels, and the template is zeroed more than about 50 site px above the base so the site lid's grey edge can't leak in. It's neutral grey (R≈G≈B).
- **Fit to each product.** x scales with the body width measured 8% of the product width above the baseline, so side edges map to side edges. Each column's distance below the product's own (lightly smoothed) bottom outline maps to the same scaled distance below the site jar's outline. The shadow follows a pouch's crinkled bottom or a jar's curved base. The site's WebP blocks are smoothed with a 0.7 site px blur at the edge and 2.2 px further out.
- **Density.** 0.8× the site (`config.json` → `shadow.strength`). At website size, the shadow just under the creatine base reads 222/232/240 (left), 241/246/248 (centre) and 241/245/248 (right) on white, against the site's 212/228/238, 240/246/247 and 242/246/250. It reaches about 95–120px below the base on the canvas and is darker under the left with a halo a little further right, like the site.
- **What was tried first.** A two-layer synthetic shadow fitted to the site's numbers (option A) halved the error of a hand-tuned one but couldn't reproduce the site's denser shadow under the left of the jar. Lifting the site's own shadow (option B) matched within 1–3 levels everywhere.
- **Group shots.** One shadow for the whole group came out about 2× too deep (it scales with width) and streaked the gaps between bottles. `shadow_group` splits the bottom outline into products at upward notches (prominence ≥ 12px), gaps, and ≥ 18px steps near the base (a pouch behind a tub), and gives each product its own shadow, combined as 1 − Π(1 − s).
- **Product pixels are never changed.** The shadow is black behind the product: alpha = a + s(1 − a), colour = graded · a / alpha.
- **Migration.** All 96 Finals and their TIFF masters were converted on 2026-10-06 without re-rendering: the reflection was stripped from each PNG, the shadow added, and the master's Reflection layer swapped for a Shadow layer. The reflection versions are in `WIP/Previous versions/<name>-reflection.png/.tif`.

## 5. Colour management and outputs

- Raws, WIP masters and all processing are in **Adobe RGB (1998)**, 16-bit, gamma-encoded values.
- **Final PNGs are converted to sRGB** (8-bit, profile embedded). Shopify and other web pipelines often strip profiles, and Adobe RGB data shown as sRGB looks dull. The script's conversion matches macOS ColorSync to within 0.1 of a level.
- The layered WIP TIFF is built by Photoshop. The export step renders it in Photoshop and compares that with the PNG data, and only keeps it if they agree (mean under 1 level, p99 under 3).
- Chance's re-export instructions after hand edits: in Photoshop, File > Export > Export As > PNG, tick Transparency and "Convert to sRGB".

## 6. Products

### CreatineMonohydrate: jar with a white lid, 30 servings (views Front, Back, Side)
- Raws: `Raw/CreatineMonohydrate-1 1.tif` (Front), `-1 6.tif` (Back), `-1 12.tif` (Side). All three are the same camera framing with the jar on a turntable; the lid sits a little off-centre in the back and side shots.
- Fine print hot spots: the small blue "ULTRA-PREMIUM MICRONIZED CREATINE MONOHYDRATE" beside the product name; "NET WT. 150g (5.3oz) DIETARY SUPPLEMENT"; the supplement facts; the address; the FDA disclaimer; the barcode.
- Select Subject did **not** include the podium for the jar. It gave good lid and neck edges, but soft or flat edges at the base corners. GrabCut failed: it clipped the lid corners and missed about 20px of the lid rim.
- The back and side were matched to the front's outline (see multiview-outline-match.md). That was done because Chance said the outline shape differed and the lid sat off-centre.
- Left for Chance to fix by hand: a dust speck on the back's lower left, a notch at the side's lower-left base, and a faint texture at the back and side base corners.

### Equilone: small jar with a ribbed white lid, on the riser disc (views Front = `Equilone-1 1`, Back = `-1 8`, Side = `-1 15`)
- Creatine session (2026-08-31) and the same camera framing, so the same-pixel podium match gives gain 1.
- Select Subject took the whole riser, so it was cut with `--body-cols --gap-row 3134` (the tilted-ellipse base).
- Chance chose the creatine's scale, 1.60172, over fill-width (2.38×, soft print) on 2026-09-29, so jars look consistent side by side.
- Brand blue on the label is #00B0E0; that's the label's own print.

### The 2026-09-30 batch: 15 products on the riser disc, plus Bundle 3 (approved and exported 2026-10-01)
- **Views and raws.** The raw-to-view map is in `~/Library/Caches/swolverine-product-photos/_batch/products.txt`. Front = the logo panel, Back = Supplement Facts, Side = the description panel.
  - Raw names had typos ("Ashwadonda", "Beta-Alalnine", "KreAlk"), so the output names are corrected.
  - Flavours become views: BCAA-LemonLime-Front, -Pineapple-Front, -Pomegranate-Front; GreensReds-Orange-Front; PRE-Mango-Front. PRE-Back (`PRE 9`, exposure ×1.072) and PRE-Side (`PRE 26`) were added on 2026-10-01 at the same 1.4675 scale.
- **Exposure.**
  - All Aug 31 raws match the creatine reference (gain 1), except two.
  - BCAA lemon lime/pineapple/2/3 were 7.8% darker; the same-pixel podium ratio corrected them.
  - Incivra was at ISO 500 and corrected ×0.907.
  - Beta-Alanine and L-Glutamine (Sep 4, tabletop riser) are anchored to the Citrulline tub's white (see SKILL.md).
- **Scale.**
  - 1.60172 for bottles and short tubs.
  - 1.4675 for tall tubs (BCAA, Collagen, Intra, GreensReds, PRE).
  - 1.4369 for Beta-Alanine and L-Glutamine.
  - Bundle 3 is at fill-width 0.927.
- **Label whites.** Tubs come out at 239–242 sRGB, matching the creatine. Bottles are darker from real shading on the narrow cylinder: Ashwagandha about 225, B-Complex/Krill/Multivitamin 231–233, Colostrum 235. Collagen's label stock is warm (R−B ≈ 5).
- **Left for Chance to fix by hand.**
  - The warped bases of Collagen and Intra, cut to the real rim.
  - Scuffs on Incivra Back.
  - A beige stain on BCAA Back.
  - Specks on Collagen Front/Back, Citrulline Side and Intra Back.

### WheyIsolate-Vanilla: same pouch, Vanilla Milkshake (view Front)
- Creatine session (2026-08-31), gain 1 (see section 3). Select Subject left out the riser, so no podium cut was needed. Scale 0.733.
- Lit by the creatine setup, so the lower third of the pouch is about 16 levels darker than the chocolate pouch (lower-middle sRGB 216/220/223 vs 234/236/239). The top white and the blue band match within about 4 levels. That's real light falloff in the raw.

### WheyIsolate: stand-up pouch, glossy blue top, white body, Chocolate Milkshake (views Front, Back)
- `Raw/WheyIsolate+Shaker.tif` is a lifestyle shot; it was not processed.
- Select Subject included the podium, so it was cut by the gap trace. The back's "Lot:2504693 EXP:11/2027" print sits about 120px above the base, and a naive first-dark-pixel trace locks onto it.
- Fine print hot spots: "NATURALLY FLAVORED / NET WT 1.71LB (775G)"; the nutrition facts; ingredients; the Prop 65 and FDA text; the address; the Lot/EXP code.
- Glossy highlights: about 1% of the pouch clips to white, mostly the blue top. That matches Chance's look.

## 7. What Chance corrected along the way (don't repeat these)
- Preferred the vanilla pouch's lighting (the creatine setup: more shape, a darker lower body) over the chocolate's flatter, brighter light. The chocolate Front/Back were re-exported with `match-light` to the vanilla. The pre-match versions are in `WIP/Previous versions/`.
- "AI generators drop the fine print": this is the reason for the whole non-generative approach.
- "The cutout is slightly off from the original": the creatine back and side had a different outline from the front, and the lid was off-centre. Fixed with outline matching.
- Wanted a single transparent PNG per view, not several variants. PNGs go in Final/PNG/ (moved there 2026-10-01; WebPs in Final/WebP/, bundles in a Bundles/ subfolder of each). Layered TIFFs go in WIP/, and there's no extra folder.
- Wanted editable layers in the TIFFs, not flattened images.
- Replaced the faint reflection with a soft contact shadow like the one on the current website images (2026-10-06): the closer-to-site option, a tad lighter (0.8×), on every product.
