# Matching several views to one approved outline (rigid products)

**When to use this:** only when Chance asks for it, or when they notice that the back/side cutouts "look off" compared with the front. It was done for the creatine jar on 2026-09-24. The idea: a cylindrical jar has the same silhouette from every side, so every view should reuse the approved front's hand-made outline. The views then match exactly when a shopper flips through the gallery. It doesn't apply to pouches; their outline really differs per side.

It isn't scripted as a subcommand because it depends on the product's geometry. Implement it with these steps and parameters, which are what Chance approved.

## Steps (canvas coordinates unless noted)

1. **Reference outline:** the approved view's alpha, e.g. the creatine front from Chance's PSD mask, warped to the canvas.
2. **Rigid alignment per view, lid and body separately:**
   - Split the outline at the neck (the narrowest row between lid and body; creatine raw row 2286, canvas row 1418).
   - Fit each part's horizontal and vertical offset to the view's Select Subject mask by soft IoU (coarse 1px search, then 0.25px).
   - The body's vertical position is poorly constrained by a mask fit (IoU barely changes between 0 and −15px). Pin it from fixed horizontal features instead: the podium edge rows were identical across shots, so the body's dy = 0.
   - Creatine results: back body −4.5px, lid −9px; side body −21px, lid −26.75px (raw px).
3. **Lid re-centring:** shift the lid rows horizontally onto the reference lid position, easing the shift in over the neck (rows 1385–1445, smoothstep) so there's no hard step. The lid is plain plastic, so no printing moves.
4. **Edge nudge (Liquify-style, no painting):**
   - For each reference-outline pixel, find the nearest real-edge pixel, matching lid to lid and body to body, with 40px slack across the neck split. Drop pairs longer than 32px.
   - Smooth the displacement (Gaussian sigma 14).
   - Spread it inward from the edge: full strength for the first 25px, then fade exp(−(d−25)/18).
   - Resample the jar with Lanczos. Creatine: up to about 30px at the base corners, under 0.5px at 100px inside, so label text doesn't move. Wrap-around graphics right at the edge moved 2–5px, smoothly.
5. **Base:** treat the podium contact line as not-jar by eroding the real mask about 7px in the bottom band before matching. This does what Chance's hand retouch did on the front.
6. **Neck exception:** in a thin band around the neck (rows 1375–1472), keep the photo's own edge and alpha (blend 15 rows each side). The lid seats differently in each shot, and forcing the outline there stretches pixels into fuzz.
7. **Leftover background inside the outline** (should be a 2–3px ring): fill by 3×3 normalised averaging from valid neighbours. Judge "valid" against the un-eroded real mask. If the fill is large (over about 30k px, or visible streaks), the alignment is wrong; fix that instead of filling more.
8. **Verify:** ink more than 60px inside the edge should change by under 2 levels. Compare 100% crops of the lid corner, neck, and base corners with the reference view.

## What failed along the way (don't retry)

- Copying the reference outline on rigidly and filling the gaps: visible streaks at the base corners, because the back/side jars were very slightly tilted and their base curvature differs.
- A vertical body shift fitted to the mask (−7 / −15px): fixed the corners but pushed the shoulders out of line.
- Nearest-point matching without the neck slack: 60–160px false pairs across the neck notch.
- A fast fade (exp(−d/18) from the edge): pixels just inside the edge moved too little and picked up background.
