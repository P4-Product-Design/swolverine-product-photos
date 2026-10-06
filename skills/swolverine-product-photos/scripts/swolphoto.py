#!/usr/bin/env python3
"""Swolverine product-photo pipeline: raw studio TIFF -> Final PNG + layered WIP TIFF.

Non-generative by design. Every step only masks, moves, resamples or tone-maps real
camera pixels. Nothing is painted, synthesised or "enhanced" (so label fine print survives).

Subcommands (run in order; each prints what it wrote and what to look at):
  preview      small sRGB JPEGs of the raws, so you can see what you're working with
  mask         Photoshop Select Subject -> raw-size mask per view (via crash-safe JSX)
  podium-cut   remove the podium from the mask by tracing the dark gap under the product
  render       exposure-match to the house podium, frame on the canvas, apply the look, add the contact shadow
  match-light  optional: match broad lighting to an approved donor shot of the same packaging (Multiply layer)
  qa           100% fine-print crops, edge crops, clipping and brand-blue check
  export       sRGB transparent PNG -> Final/PNG/ (+ q90 WebP -> Final/WebP/), layered 16-bit TIFF (built in Photoshop) -> WIP/
               --subdir Bundles puts group shots in Final/PNG/Bundles/ + Final/WebP/Bundles/ + WIP/Bundles/
  webp         WebP copies of PNGs already in Final/PNG/ (--views all for every PNG)
  reshadow     Chance edited a master by hand (e.g. trimmed the bottom): refit the contact shadow to the master as it is
               now. Preview first; --apply swaps only the Shadow layer, verifies, and rewrites the PNG + WebP

Typical run:
  PY=~/.cache/swolverine-product-photos/venv/bin/python
  $PY swolphoto.py preview    --product WheyIsolate --views Front,Back
  $PY swolphoto.py mask       --product WheyIsolate --views Front,Back
  $PY swolphoto.py podium-cut --product WheyIsolate --views Front,Back
  $PY swolphoto.py render     --product WheyIsolate --views Front,Back
  $PY swolphoto.py qa         --product WheyIsolate --views Front,Back
  (show Chance the previews and wait for approval)
  $PY swolphoto.py export     --product WheyIsolate --views Front,Back
"""
import argparse, glob, json, os, shutil, subprocess, sys, textwrap, time
import numpy as np

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(SKILL, 'assets')
CFG = json.load(open(os.path.join(ASSETS, 'config.json')))
N = CFG['canvas_px']; BASE = CFG['baseline_y']; MARGIN = CFG['margin_px']
SHADOW = CFG['shadow']

def default_root():
    """Product-images folder (holds Raw/, WIP/, Final/): $SWOL_PHOTO_ROOT, else "root" in
    ~/.config/swolverine-product-photos/config.json, else None (then --root is required)."""
    r = os.environ.get('SWOL_PHOTO_ROOT')
    local = os.path.expanduser('~/.config/swolverine-product-photos/config.json')
    if not r and os.path.exists(local): r = json.load(open(local)).get('root')
    return os.path.expanduser(r) if r else None

def lazy():
    global cv2, tifffile, Image
    import cv2 as _cv2, tifffile as _tf
    from PIL import Image as _Im
    cv2, tifffile, Image = _cv2, _tf, _Im

# ---------------------------------------------------------------- paths / state
def work_dir(a):
    d = a.work or os.path.expanduser(f'~/Library/Caches/swolverine-product-photos/{a.product}')
    os.makedirs(d, exist_ok=True); return d

def meta_path(a): return os.path.join(work_dir(a), 'meta.json')
def load_meta(a):
    p = meta_path(a); return json.load(open(p)) if os.path.exists(p) else {}
def save_meta(a, m): json.dump(m, open(meta_path(a), 'w'), indent=2)

def raw_path(a, view):
    for kv in (a.raw or []):
        k, v = kv.split('=', 1)
        if k == view: return v
    p = os.path.join(a.root, 'Raw', f'{a.product}-{view}.tif')
    if not os.path.exists(p):
        sys.exit(f'Raw not found: {p}\n  Pass --raw {view}=/path/to/file.tif if it is named differently.')
    return p

def read_raw(path):
    """16-bit Adobe RGB TIFF -> float32 RGB 0..1 (Adobe RGB, gamma-encoded)."""
    a = tifffile.imread(path)
    if a.dtype != np.uint16: sys.exit(f'{path}: expected 16-bit, got {a.dtype}')
    return a[..., :3].astype(np.float32) / 65535

def views(a): return [v.strip() for v in a.views.split(',') if v.strip()]

# ---------------------------------------------------------------- colour
_A2X = np.array([[0.5767309, 0.1855540, 0.1881852], [0.2973769, 0.6273491, 0.0752741], [0.0270343, 0.0706872, 0.9911085]])
_X2S = np.array([[3.2404542, -1.5371385, -0.4985314], [-0.9692660, 1.8760108, 0.0415560], [0.0556434, -0.2040259, 1.0572252]])
_M = (_X2S @ _A2X).astype(np.float32)
def adobe_to_srgb(rgb):
    """Adobe RGB (1998) encoded -> sRGB encoded. Matches macOS ColorSync to ~0.1 level."""
    lin = np.clip(rgb, 0, 1) ** (563 / 256); s = np.clip(lin @ _M.T, 0, 1)
    return np.where(s <= 0.0031308, 12.92 * s, 1.055 * s ** (1 / 2.4) - 0.055).astype(np.float32)

def apply_look(rgb):
    """The house look: per-channel tone curve decoded from Chance's creatine PSD (Brightness + Curves)."""
    lut = np.load(os.path.join(ASSETS, 'tone_lut.npy')); x = np.arange(lut.shape[1])
    return np.stack([np.interp(np.clip(rgb[..., c], 0, 1) * (lut.shape[1] - 1), x, lut[c]) for c in range(3)], -1).astype(np.float32)

# ---------------------------------------------------------------- contact shadow
# The house shadow (Chance, 2026-10-06) is the one on the old website images, lifted off the site's creatine
# render once and stored as assets/shadow_template.npz (darkness 0-255 around the site jar's base, the jar itself
# filled from its surroundings). It is laid along each product's own bottom outline: x scales with the body width
# (side edges -> side edges), and each column's distance below the outline maps to the same, scaled distance below
# the site jar's outline. Black, at SHADOW['strength'] of the site's density. Never touches product pixels.
_TPL = {}
def _template():
    if not _TPL:
        t = np.load(os.path.join(ASSETS, SHADOW['template']))
        cx, hw, a0, a1 = t['meta']; arc = np.full(t['D'].shape[1], np.nan); arc[int(a0):int(a1) + 1] = t['arc']
        _TPL.update(D=t['D'], arc=arc, cx=float(cx), hw=float(hw), a0=int(a0), a1=int(a1))
    return _TPL

def _bottom(alpha):
    """Lowest product row per column (NaN where the column is empty)."""
    col = alpha > .5; has = col.any(0); yb = np.full(alpha.shape[1], np.nan)
    yb[has] = alpha.shape[0] - 1 - np.argmax(col[::-1, has], 0); return yb

def _shadow_from(alpha, dist, cx, hw, ybo, a_lo, a_hi, win=None):
    """Sample the template for one product: centre cx, body half-width hw, smoothed bottom outline ybo
    (held flat past the arc ends a_lo..a_hi). win = (r0, r1, c0, c1) limits the work to a window."""
    T = _template(); k = T['hw'] / hw; n = alpha.shape[0]
    r0, r1, c0, c1 = win or (0, n, 0, n)
    X = np.arange(c0, c1, dtype=np.float32); xw = T['cx'] + (X - cx) * k
    yrw = T['arc'][np.clip(np.round(xw).astype(int), T['a0'], T['a1'])]
    yy = np.arange(r0, r1, dtype=np.float32)[:, None]
    mapx = np.broadcast_to(xw[None, :], (r1 - r0, c1 - c0)).astype(np.float32)
    mapy = (yrw[None, :] + (yy - ybo[None, c0:c1]) * k).astype(np.float32)
    samp = cv2.remap(T['D'], mapx, mapy, cv2.INTER_LINEAR, borderValue=0)
    # de-block the site's WebP: light blur right at the edge, more out in the soft halo (sigmas in site pixels)
    d = dist[r0:r1, c0:c1] * k
    near = cv2.GaussianBlur(samp, (0, 0), SHADOW['blur_near'] / k); far = cv2.GaussianBlur(samp, (0, 0), SHADOW['blur_far'] / k)
    w = np.exp(-d / 3.0); out = np.zeros((n, n), np.float32)
    out[r0:r1, c0:c1] = np.clip((w * near + (1 - w) * far) / 255, 0, 1); return out

def _smooth_outline(yb, a_lo, a_hi):
    ybo = yb.copy(); v = ybo[a_lo:a_hi + 1]; good = ~np.isnan(v)
    v = np.interp(np.arange(len(v)), np.where(good)[0], v[good])
    ybo[a_lo:a_hi + 1] = np.convolve(np.pad(v, 4, mode='edge'), np.ones(9) / 9, 'valid')
    ybo[:a_lo] = ybo[a_lo]; ybo[a_hi + 1:] = ybo[a_hi]; return ybo

def shadow_single(alpha):
    """One product: scale from the body width 8% of the product width above its base. The base is the product's own
    lowest row: the baseline for a render, higher where Chance trimmed the bottom by hand (reshadow)."""
    n = alpha.shape[0]; yb = _bottom(alpha); ys, xs = np.where(alpha > .5); base = ys.max()
    H = ys.max() - ys.min() + 1; W = xs.max() - xs.min() + 1
    bx = np.where(yb >= base - .04 * H)[0]; a_lo, a_hi = bx[0], bx[-1]
    xb = np.where(alpha[int(base - round(.08 * W))] > .5)[0]; xl, xr = xb[0], xb[-1]
    dist = cv2.distanceTransform((alpha < .5).astype(np.uint8), cv2.DIST_L2, 5)
    return _shadow_from(alpha, dist, (xl + xr) / 2, (xr - xl) / 2, _smooth_outline(yb, a_lo, a_hi), a_lo, a_hi)

def shadow_group(alpha, min_prom=12, min_w=60, step_px=18):
    """Group/bundle shot: split the bottom contour into products and give each its own shadow. Splits at upward
    cusps (two bases meeting), gaps (empty columns) and >=18px steps near the base (a pouch behind a tub).
    Scaling the whole group as one product made the shadow ~2x too deep and streaked the gaps (2026-10-06)."""
    from scipy.signal import find_peaks
    from scipy.ndimage import median_filter
    n = alpha.shape[0]; yb = _bottom(alpha); xs = np.where(~np.isnan(yb))[0]; x0, x1 = xs[0], xs[-1]
    v = yb[x0:x1 + 1].copy(); gap = np.isnan(v)
    vf = np.interp(np.arange(len(v)), np.where(~gap)[0], v[~gap]); vs = median_filter(vf, 15)
    pk, _ = find_peaks(-vs, prominence=min_prom, distance=120)
    v5 = median_filter(vf, 5); h = 4; dd = np.zeros_like(v5); dd[h:-h] = v5[2 * h:] - v5[:-2 * h]
    low = np.nanmax(v) - 0.12 * len(v); steps = []           # the group's outer edges are steep too, but far above the base
    for i in np.argsort(-np.abs(dd)):
        if abs(dd[i]) < step_px: break
        if min(v5[max(0, i - 30)], v5[min(len(v5) - 1, i + 30)]) > low and all(abs(i - q) > 40 for q in steps): steps.append(i)
    merged = []
    for c in sorted(list(pk) + steps):
        if not merged or c - merged[-1] > 40: merged.append(c)
    cuts = set([0, len(v) - 1] + merged); gi = np.where(gap)[0]
    if len(gi):
        for r in np.split(gi, np.where(np.diff(gi) > 1)[0] + 1): cuts |= {r[0] - 1, r[-1] + 1}
    cuts = sorted(c for c in cuts if 0 <= c < len(v))
    dist = cv2.distanceTransform((alpha < .5).astype(np.uint8), cv2.DIST_L2, 5)
    s = np.zeros((n, n), np.float32); segs = []
    for c, d in zip(cuts[:-1], cuts[1:]):
        xa, xb = x0 + c, x0 + d; w = xb - xa + 1
        if w - 1 < min_w or np.all(np.isnan(yb[xa:xb + 1])): continue
        seg = yb[xa:xb + 1]; ia = np.where(seg >= np.nanmax(seg) - 0.045 * w)[0]; a_lo, a_hi = xa + ia[0], xa + ia[-1]
        ybo = np.full(n, np.nan); ybo[a_lo:a_hi + 1] = yb[a_lo:a_hi + 1]; ybo = _smooth_outline(ybo, a_lo, a_hi)
        win = (max(0, int(np.nanmin(ybo[a_lo:a_hi + 1]) - 0.35 * w)), min(n, int(np.nanmax(ybo) + 0.3 * w) + 1),
               max(0, int(xa - 0.7 * w)), min(n, int(xb + 0.7 * w) + 1))
        s = 1 - (1 - s) * (1 - _shadow_from(alpha, dist, (xa + xb) / 2, w / 2, ybo, a_lo, a_hi, win)); segs.append((int(xa), int(xb)))
    return s, segs

def contact_shadow(alpha, group=False):
    """Shadow alpha (0..1, black) for the canvas. group=True for bundle/group shots."""
    if group:
        s, segs = shadow_group(alpha); print(f'   group shot: {len(segs)} products along the base, each with its own shadow (x ranges {segs})')
    else: s = shadow_single(alpha)
    return (s * SHADOW['strength']).astype(np.float32)

def compose(graded, alpha, shadow):
    """Straight-alpha RGBA over transparency + flat-on-white, both Adobe RGB. The shadow is black."""
    out_a = alpha + shadow * (1 - alpha)
    rgb = graded * alpha[..., None] / np.maximum(out_a, 1e-6)[..., None]
    flat = graded * alpha[..., None] + (1 - shadow[..., None]) * (1 - alpha[..., None])
    return np.dstack([rgb, out_a]).astype(np.float32), flat.astype(np.float32)

def load_shadow(wd, v, A):
    """The shadow render saved for this view. Renders made before v0.2.0 have none: use the single-product one."""
    p = os.path.join(wd, f'{v}_shadow.npy')
    if os.path.exists(p): return np.load(p)
    print(f'{v}: no {v}_shadow.npy (rendered before v0.2.0); using the single-product shadow. Rerun render for a group shot.')
    return contact_shadow(A)

def to_jpg(path, rgb_adobe, max_side=None, q=88):
    im = Image.fromarray((adobe_to_srgb(rgb_adobe) * 255 + 0.5).astype(np.uint8))
    if max_side and max(im.size) > max_side:
        s = max_side / max(im.size); im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
    im.save(path, quality=q); return path

# ---------------------------------------------------------------- photoshop
def ps_app():
    name = os.environ.get('SWOL_PS_APP') or CFG.get('photoshop_app')
    if name and os.path.exists(f'/Applications/{name}'): return name
    c = sorted(glob.glob('/Applications/Adobe Photoshop 20*'))
    if not c: sys.exit('Photoshop not found in /Applications.')
    return os.path.basename(c[-1])

JSX_GUARD = r'''
app.displayDialogs = DialogModes.NO;
function __guard(){ var u=[]; for (var i=0;i<app.documents.length;i++){ try{ if(!app.documents[i].saved) u.push(app.documents[i].name);}catch(e){} } return u; }
var __uns = __guard();
'''

def run_jsx(body, label, timeout=1800):
    """Run JSX in Photoshop. Refuses to run if Chance has unsaved documents open (a crash would lose them)."""
    app = ps_app()
    wd = os.path.expanduser('~/Library/Caches/swolverine-product-photos/_jsx'); os.makedirs(wd, exist_ok=True)
    f = os.path.join(wd, f'{label}_{int(time.time())}.jsx')
    open(f, 'w').write(JSX_GUARD + '\nvar __r;\nif (__uns.length) { __r = "UNSAVED: " + __uns.join(", "); } else { __r = (function(){\n' + body + '\n})(); }\n__r;')
    print(f'[photoshop] running {label} in {app} (can take a few minutes for 44MP raws)...', flush=True)
    t0 = time.time()
    r = subprocess.run(['osascript', '-e', f'with timeout of {timeout} seconds',
                        '-e', f'tell application "{app}" to do javascript file (POSIX file "{f}")', '-e', 'end timeout'],
                       capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip()
    print(f'[photoshop] {label} finished in {time.time() - t0:.0f}s: {out[:600]}')
    if out.startswith('UNSAVED'):
        sys.exit('Stopped: Photoshop has unsaved documents open. Ask Chance to save/close them first (a script failure could lose that work).')
    if 'Connection is invalid' in out or r.returncode != 0 and 'timed out' not in out:
        running = subprocess.run(['pgrep', '-f', f'{app}.app/Contents/MacOS'], capture_output=True).returncode == 0
        sys.exit(f'Photoshop call failed ({out[:300]}). Photoshop running: {running}. See references/troubleshooting.md.')
    if 'timed out' in out:
        sys.exit('AppleEvent timed out: Photoshop is probably showing a dialog (first-run prompt, script permission, colour profile). Ask Chance to look at Photoshop and click through, then rerun.')
    return out

# ---------------------------------------------------------------- preview
def cmd_preview(a):
    wd = work_dir(a)
    for v in views(a):
        p = raw_path(a, v); raw = read_raw(p)
        print(f'{v}: {p}  {raw.shape[1]}x{raw.shape[0]}')
        print('  wrote', to_jpg(os.path.join(wd, f'{v}_raw.jpg'), raw, 1400))

# ---------------------------------------------------------------- mask
def cmd_mask(a):
    wd = work_dir(a); m = load_meta(a); jobs = []
    for v in views(a):
        p = raw_path(a, v)
        crop = [int(x) for x in a.crop.split(',')] if a.crop else None
        jobs.append(dict(view=v, raw=p, out=os.path.join(wd, f'{v}_psmask.png'), crop=crop))
    body = 'var jobs = ' + json.dumps(jobs) + ';\n' + r'''
var log=[];
for (var i=0;i<jobs.length;i++){ var j=jobs[i]; var doc=null;
  try{
    doc=app.open(new File(j.raw));
    if (j.crop) doc.crop([UnitValue(j.crop[0],"px"),UnitValue(j.crop[1],"px"),UnitValue(j.crop[2],"px"),UnitValue(j.crop[3],"px")]);
    var d=new ActionDescriptor(); d.putBoolean(stringIDToTypeID("sampleAllLayers"), false);
    executeAction(stringIDToTypeID("autoCutout"), d, DialogModes.NO);          // Select > Subject
    doc.bitsPerChannel = BitsPerChannelType.EIGHT;
    doc.artLayers.add();
    var w=new SolidColor(); w.rgb.red=255; w.rgb.green=255; w.rgb.blue=255; var k=new SolidColor(); k.rgb.red=0; k.rgb.green=0; k.rgb.blue=0;
    doc.selection.fill(w); doc.selection.invert(); doc.selection.fill(k); doc.selection.deselect();
    doc.saveAs(new File(j.out), new PNGSaveOptions(), true, Extension.LOWERCASE);
    log.push(j.view+" ok");
  }catch(e){ log.push(j.view+" ERR "+e); }
  if (doc) doc.close(SaveOptions.DONOTSAVECHANGES);   // raw is never saved
}
return log.join(" | ");'''
    run_jsx(body, 'select_subject')
    for j in jobs:
        v = j['view']
        if not os.path.exists(j['out']): print(f'{v}: no mask written'); continue
        raw = read_raw(j['raw']); H, W = raw.shape[:2]
        mk = cv2.imread(j['out'], cv2.IMREAD_GRAYSCALE)
        full = np.zeros((H, W), np.uint8); x0, y0 = (j['crop'][0], j['crop'][1]) if j['crop'] else (0, 0)
        full[y0:y0 + mk.shape[0], x0:x0 + mk.shape[1]] = mk
        np.save(os.path.join(wd, f'{v}_psmask.npy'), full)
        ys, xs = np.where(full > 127)
        m.setdefault(v, {})['psmask_bbox'] = [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]
        overlay(os.path.join(wd, f'{v}_psmask_overlay.jpg'), raw, full > 127)
        print(f'{v}: mask bbox x {xs.min()}-{xs.max()} y {ys.min()}-{ys.max()}  -> {v}_psmask_overlay.jpg (check whether the podium got included)')
    save_meta(a, m)

def overlay(path, raw, mask_bool, box=None, max_side=1400):
    img = (adobe_to_srgb(raw) * 255).astype(np.uint8).copy()
    c, _ = cv2.findContours(mask_bool.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    cv2.drawContours(img, c, -1, (255, 0, 0), max(2, raw.shape[1] // 1500))
    if box: cv2.rectangle(img, (box[0], box[1]), (box[2], box[3]), (0, 200, 0), max(2, raw.shape[1] // 1500))
    if box is None:
        ys, xs = np.where(mask_bool)
        if len(ys):
            pad = 300; img = img[max(0, ys.min() - pad):ys.max() + pad, max(0, xs.min() - pad):xs.max() + pad]
    im = Image.fromarray(img); s = max_side / max(im.size)
    if s < 1: im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
    im.save(path, quality=85)

# ---------------------------------------------------------------- podium cut
def find_gap_row(mb):
    """Row where the product meets the podium, from an abrupt width change in the lower part of the mask."""
    w = mb.sum(1).astype(np.float32); ys = np.where(w > 0)[0]; y0, y1 = ys.min(), ys.max()
    w = np.convolve(w, np.ones(9) / 9, 'same')
    lo = int(y0 + 0.55 * (y1 - y0)); best = (0, None)
    for y in range(lo, y1 - 150):   # stay clear of the podium's own curved bottom edge
        a_, b_ = w[y - 20], w[y + 20]
        r = abs(a_ - b_) / max(a_, b_, 1)
        if r > best[0]: best = (r, y)
    return best

def cmd_podium_cut(a):
    from scipy.ndimage import median_filter
    wd = work_dir(a); m = load_meta(a); order = views(a)
    if a.guide_view and a.guide_view in order: order = [a.guide_view] + [v for v in order if v != a.guide_view]
    for v in order:
        raw = read_raw(raw_path(a, v)); H, W = raw.shape[:2]
        pm = np.load(os.path.join(wd, f'{v}_psmask.npy')) > 127
        ratio, gap = find_gap_row(pm)
        if a.gap_row: gap, ratio = int(a.gap_row), 1.0
        if a.no_podium or gap is None or ratio < 0.12:
            np.save(os.path.join(wd, f'{v}_mask.npy'), (np.load(os.path.join(wd, f'{v}_psmask.npy')) / 255).astype(np.float32))
            m.setdefault(v, {})['podium'] = None; m[v]['group'] = bool(a.no_podium)
            print(f'{v}: ' + ('--no-podium: ' if a.no_podium else f'no podium found in the mask (largest width change {ratio:.2f}); ') + 'Select Subject mask used as-is.')
            continue
        if a.body_cols:   # product narrower than what it stands on (jar on the riser disc): trace and keep only the body's columns
            cols = np.where(pm[gap - 60])[0]; x0, x1 = int(cols.min()), int(cols.max())
        else:
            cols = np.where(pm[min(H - 1, gap + 120)])[0]
            x0, x1 = int(cols.min()) - 40, int(cols.max()) + 40
        L = cv2.GaussianBlur(raw.mean(-1) * 255, (0, 0), 1.2)
        xs = np.arange(x0, x1 + 1); good = np.ones(len(xs), bool)
        if a.from_below:
            # Bottle/jar on the riser disc with a dark base (gummies behind clear glass) or print right at the base:
            # tracing down from the product stops at the label's lower edge. Trace UP from the disc's plain top
            # instead, to the first clear drop in brightness. A thin contact line (<=6px) is climbed through so it
            # stays outside the mask; a tall dark base (glass) keeps the lower crossing.
            S = gap + 150; bot = np.full(len(xs), np.nan, np.float32)
            for i, x in enumerate(xs):
                col = L[:S + 13, x]; hit = None
                for y in range(S, gap - 40, -1):
                    ref = np.median(col[y + 1:y + 13])
                    drop = ref - col[y]   # a big drop (dark base), or a thin dark line that brightens again above (contact line);
                    if drop >= 40 or (drop >= 15 and col[max(0, y - 6):y].max() - col[y] > 0.6 * drop): hit = y; break   # not a soft shading step on the disc
                if hit is None: continue
                y = hit; seg = col[max(0, y - 8):y + 1]; ym = max(0, y - 8) + int(np.argmin(seg)); thr = (ref + seg.min()) / 2
                t = ym   # climb from the darkest point of the band (the first hit can be only half dark)
                while t > ym - a.band_max and col[t] < thr: t -= 1
                if t > ym - a.band_max:   # out of a thin contact shadow: edge = its top
                    v0, v1 = col[t], col[t + 1]; bot[i] = t + (float(np.clip((v0 - thr) / (v0 - v1), 0, 1)) if v0 > v1 else 0.5)
                else:             # dark base (glass with dark contents): edge = the lower crossing
                    t = ym
                    while t < y + 1 and col[t] < thr: t += 1
                    v0, v1 = col[t], col[t - 1]; bot[i] = t - (float(np.clip((v0 - thr) / (v0 - v1), 0, 1)) if v0 > v1 else 0.5)
            ok = ~np.isnan(bot); bot[~ok] = np.interp(xs[~ok], xs[ok], bot[ok]); wgt = ok.astype(float) + 1e-6
            for _ in range(8):   # robust quadratic: flags columns that hit a reflection or speck
                pq = np.polyfit(xs, bot, 2, w=wgt); r = bot - np.polyval(pq, xs); sig = 1.4826 * np.median(np.abs(r[ok])) + 1e-3
                wgt = ((np.abs(r) < 3 * sig) & ok).astype(float) + 1e-6
            good = wgt > 0.5; used_guide = False
            np.save(os.path.join(wd, f'{v}_basetrace.npy'), np.stack([xs, bot, good, ok]).astype(np.float32))
            print(f'{v}: traced up from the disc top (row {S}): {ok.mean() * 100:.0f}% of columns hit, {good.mean() * 100:.0f}% inliers, sigma {sig:.1f}px, bottom rows {np.percentile(bot, 2):.0f}-{bot.max():.0f}')
        else:
            # 1) first pass: from the product above, first row that is clearly darker = the shadow gap
            Y0, Y1 = gap - 190, gap + 180
            first = np.full(x1 - x0 + 1, np.nan, np.float32)
            for i, x in enumerate(range(x0, x1 + 1)):
                col = L[Y0:Y1, x]; ref = np.median(col[:40]); d = np.where(col < ref - 45)[0]
                if len(d): first[i] = Y0 + d[0]
            ok = ~np.isnan(first)
            first[~ok] = np.interp(xs[~ok], xs[ok], first[ok])
            # 2) robust quadratic through it (rejects hits on printed text or the podium seam)
            wgt = np.ones_like(first)
            for _ in range(8):
                p = np.polyfit(xs, first, 2, w=wgt); r = first - np.polyval(p, xs); sig = 1.4826 * np.median(np.abs(r)) + 1e-3
                wgt = (np.abs(r) < 2.5 * sig).astype(float) + 1e-6
            guide = m.get('_gap_fit'); used_guide = False
            if sig > 20 and guide:
                p = np.array(guide); used_guide = True
                print(f'{v}: own gap fit too noisy (sigma {sig:.0f}px, likely print near the base); using the guide view gap curve instead (same camera + podium).')
            elif sig <= 20: m['_gap_fit'] = p.tolist()
            else:
                print(f'{v}: WARNING gap fit noisy (sigma {sig:.0f}px) and no guide yet. Run the cleanest view first with --guide-view.')
            fit = np.polyval(p, xs)
            # 3) darkest continuous path within +-40px of the fit (dynamic programming)
            B = 40; Hh = 2 * B + 1; cost = np.zeros((Hh, len(xs)), np.float32)
            for i, x in enumerate(xs):
                yc = int(round(fit[i])); cost[:, i] = L[yc - B:yc + B + 1, x]
            acc = cost.copy(); back = np.zeros_like(acc, np.int8); steps = np.array([1, 0, -1, 2, -2]); pen = np.array([3, 0, 3, 9, 9])
            for i in range(1, len(xs)):
                pr = acc[:, i - 1]; cand = np.stack([np.r_[pr[1:], 1e9], pr, np.r_[1e9, pr[:-1]], np.r_[pr[2:], 1e9, 1e9], np.r_[1e9, 1e9, pr[:-2]]])
                k = np.argmin(cand + pen[:, None], 0); acc[:, i] += cand[k, np.arange(Hh)] + pen[k]; back[:, i] = steps[k]
            path = np.zeros(len(xs), int); path[-1] = np.argmin(acc[:, -1])
            for i in range(len(xs) - 1, 0, -1): path[i - 1] = path[i] + back[path[i], i]
            gy = np.round(fit).astype(int) - B + path
            # 4) product bottom edge = climb out of the dark gap to the half-way brightness (sub-pixel)
            bot = np.zeros(len(xs), np.float32)
            for i, x in enumerate(xs):
                y = gy[i]; thr = (L[y, x] + np.median(L[y - 60:y - 30, x])) / 2
                while y > gy[i] - 40 and L[y, x] < thr: y -= 1
                v0, v1 = L[y, x], L[y + 1, x]
                bot[i] = y + (float(np.clip((v0 - thr) / (v0 - v1), 0, 1)) if v0 > v1 else 0.5)
        mm = np.load(os.path.join(wd, f'{v}_psmask.npy')).astype(np.float32) / 255
        yy = np.arange(H, dtype=np.float32)[:, None]
        mm[:, x0:x1 + 1] *= np.clip(bot[None, :] - yy + 0.5, 0, 1)
        if a.body_cols:
            # A jar seen from slightly above: straight sides, base = front half of an ellipse that meets the sides
            # tangentially. Near the corners jar and disc are the same brightness, so no edge can be traced there;
            # fit the ellipse to the reliable middle 70% of the traced base and draw the base band from it
            # (mask geometry only - no pixels are changed).
            band = pm[gap - 150:gap - 60].astype(np.float32).sum(1); hw = float(np.median(band)) / 2
            cx = (x0 + x1) / 2; mid = np.abs(xs - cx) < 0.35 * (x1 - x0); best = None
            for f in [1.0]:   # sides are tangent to the base ellipse; a free width fit left a flat ledge at the corners
                aa = hw * f; sq = np.sqrt(np.clip(1 - ((xs[mid] - cx) / aa) ** 2, 0, 1))
                # tilt term: the base line slopes a little (camera not dead level), otherwise one corner lets the dark contact line in
                Aq = np.stack([np.ones_like(sq), (xs[mid] - cx) / aa, sq], 1); gm = good[mid].copy()
                for _ in range(6):   # robust: a dented rim (Collagen) or a speck on the disc must not bend the whole base
                    (yc, tl, bb), *_ = np.linalg.lstsq(Aq[gm], bot[mid][gm], rcond=None); r_ = Aq @ [yc, tl, bb] - bot[mid]
                    gm = good[mid] & (np.abs(r_) < max(1.0, 3 * 1.4826 * np.median(np.abs(r_[gm]))))
                res = np.abs(r_[gm]).mean(); dent = 100 * (1 - gm.sum() / max(good[mid].sum(), 1))
                if a.base_shape:   # warped/dented tub: borrow depth+tilt from a same-size tub; the ellipse goes through the contact points (lowest)
                    bb, tl = [float(q) for q in a.base_shape.split(',')]; off = bot[mid] - (tl * Aq[:, 1] + bb * Aq[:, 2])
                    yc = float(np.percentile(off[good[mid]], 85)); r_ = Aq @ [yc, tl, bb] - bot[mid]; res = float(np.median(np.abs(r_[good[mid]])))
                if best is None or res < best[0]: best = (res, aa, yc, tl, bb, dent)
            res, aa, yc, tl, bb, dent = best; bb -= 0.75; y0b = int(min(gap - 60, yc - abs(tl))) - 5; ss = 4   # 0.75px inside the edge keeps the dark gap out
            gy_, gx_ = np.mgrid[y0b * ss:H * ss, int(cx - hw - 4) * ss:int(cx + hw + 5) * ss].astype(np.float32) / ss + 0.5 / ss
            yct = yc + tl * (gx_ - cx) / aa
            inside = (np.abs(gx_ - cx) <= hw) & ((gy_ <= yct) | (((gx_ - cx) / aa) ** 2 + ((gy_ - yct) / bb) ** 2 <= 1))
            geo = inside.reshape(H - y0b, ss, -1, ss).mean((1, 3))
            mm[y0b:] = 0; mm[y0b:, int(cx - hw - 4):int(cx - hw - 4) + geo.shape[1]] = geo
            if a.follow_trace:   # where the rim lifts off the disc (dent/warp), cut up to the traced rim so the gap under it is background
                bt = cv2.GaussianBlur(np.where(good, bot, np.inf).astype(np.float32)[None], (0, 0), 1.5)[0] if False else bot.copy()
                bt = np.minimum.reduce([np.r_[bt[1:], bt[-1]], bt, np.r_[bt[0], bt[:-1]]])   # tiny min-filter against single-column noise
                lift = (bt < (yc + tl * (xs - cx) / aa + bb * np.sqrt(np.clip(1 - ((xs - cx) / aa) ** 2, 0, 1))) - 0.5)
                cut = np.clip(bt[None, :] - np.arange(y0b, H, dtype=np.float32)[:, None] + 0.5, 0, 1)
                sub = mm[y0b:, x0:x1 + 1]; sub[:, lift] = np.minimum(sub[:, lift], cut[:, lift])
                print(f'{v}: follow-trace: rim lifted off the disc in {lift.mean() * 100:.0f}% of columns (up to {np.max(np.where(lift, yc + tl * (xs - cx) / aa + bb * np.sqrt(np.clip(1 - ((xs - cx) / aa) ** 2, 0, 1)) - bt, 0)):.0f}px) - a dent or warp; flag it for Chance')
            print(f'{v}: jar base ellipse: centre x {cx:.0f}, half-width {hw:.1f}, yc {yc:.1f}, tilt {tl:+.1f}px, depth {bb:.1f}px; residual {res:.2f}px' + (f'; {dent:.0f}% of the base ignored as off-ellipse (dent or speck? check {v}_podiumcut.jpg)' if dent > 5 else ''))
        n_, lab, st, _ = cv2.connectedComponentsWithStats((mm > 0.5).astype(np.uint8))
        keep = cv2.dilate((lab == 1 + np.argmax(st[1:, 4])).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
        mm *= keep
        np.save(os.path.join(wd, f'{v}_mask.npy'), mm.astype(np.float32))
        m.setdefault(v, {})['podium'] = dict(gap_row=int(gap), cols=[x0, x1], bottom_range=[float(bot.min()), float(bot.max())])
        # review image: base of the product, contrast-boosted, with the cut drawn
        yb = int(bot.max()); c0, c1 = max(0, x0 - 150), min(W, x1 + 150)
        crop = raw[yb - 300:yb + 250, c0:c1]; lo_, hi_ = np.percentile(crop, 1), np.percentile(crop, 99.5)
        img = (np.clip((crop - lo_) / (hi_ - lo_), 0, 1) * 255).astype(np.uint8).copy()
        cc, _ = cv2.findContours((mm[yb - 300:yb + 250, c0:c1] > 0.5).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        cv2.drawContours(img, cc, -1, (255, 0, 0), 2)
        Image.fromarray(img).resize((img.shape[1] // 2, img.shape[0] // 2)).save(os.path.join(wd, f'{v}_podiumcut.jpg'), quality=85)
        ys, xs_ = np.where(mm > 0.5)
        print(f'{v}: podium removed (gap near row {gap}, ' + ('guide curve' if used_guide else f'fit sigma {sig:.1f}px') + f'). Product bbox x {xs_.min()}-{xs_.max()} y {ys.min()}-{ys.max()}  -> {v}_podiumcut.jpg')
    save_meta(a, m)

# ---------------------------------------------------------------- render
def capture_info(path):
    t = tifffile.TiffFile(path); pg = t.pages[0]; ex = pg.tags.get('ExifTag'); d = ex.value if ex else {}
    dt = d.get('DateTimeOriginal') if isinstance(d, dict) else None
    iso = (d.get('ISOSpeedRatings') or d.get('ISOSpeed')) if isinstance(d, dict) else None
    return (dt[:10].replace(':', '-') if dt else None), iso

def podium_box(mask):
    ys, xs = np.where(mask > 0.5); bot = ys.max(); r0, r1 = CFG['podium_sample_rows_below_bottom']
    cx = (xs.min() + xs.max()) // 2; half = (xs.max() - xs.min()) // 6
    return [int(cx - half), int(bot + r0), int(cx + half), int(bot + r1)]

def box_median(raw, box):
    s = raw[box[1]:box[3], box[0]:box[2]].reshape(-1, 3) * 255
    return np.median(s, 0), np.percentile(s, 90, 0) - np.percentile(s, 10, 0)

def find_ref_raw(root, name):
    """Session reference raw: Raw/ first; Chance moves finished raws to Archive/ (Finder may add ' (1)' to the copy)."""
    stem = name[:-4]
    for c in [os.path.join(root, 'Raw', name), os.path.join(root, 'Archive', name)] + sorted(glob.glob(os.path.join(root, 'Archive', glob.escape(stem) + ' (*).tif'))):
        if os.path.exists(c): return c
    return os.path.join(root, 'Raw', name)

def podium_gain(raw, mask, a, v, rawp):
    """Exposure match. Known capture session -> its approved gain (x exact same-pixel podium ratio to the
    session's reference raw). Unknown session -> podium estimate vs the creatine reference, flagged for approval."""
    if a.gain == 'none': return np.ones(3, np.float32), None, 'none'
    if a.gain.startswith('white:'):
        # Group/bundle shots on the tabletop: products sit at different distances from the lights than the wall
        # (the same bottle measured 11% brighter in one setup than another), so neither podium nor wall predicts
        # them. Anchor on the product itself: one neutral gain so its flat white label areas (p75, linear) land
        # where the approved donor's do (vanilla pouch -> whites around 237-241 after the look).
        dprod, dview = a.gain[6:].split(':'); dwd = os.path.expanduser(f'~/Library/Caches/swolverine-product-photos/{dprod}')
        def p75(img, A, blur):
            er = cv2.erode((A > 0.99).astype(np.uint8), np.ones((2 * blur + 1,) * 2, np.uint8)) > 0
            m = cv2.blur(np.clip(img, 0, 1) ** (563 / 256), (blur, blur)); L = m.mean(2)
            return np.percentile(L[er & (m.max(2) - m.min(2) < 0.05) & (L > 0.3)], 75)
        ref = p75(np.load(os.path.join(dwd, f'{dview}_ungraded.npy')), np.load(os.path.join(dwd, f'{dview}_alpha.npy')), 15)
        mine = p75(np.ascontiguousarray(raw[::2, ::2]), np.ascontiguousarray(mask[::2, ::2]), 11)
        g = np.full(3, (ref / mine) ** (256 / 563), np.float32)
        print(f'{v}: product-white anchor to {dprod}:{dview}: white p75 {mine:.3f} vs {ref:.3f} (linear) -> neutral gain {g[0]:.4f}')
        return g, None, f'white anchor {dprod}:{dview}'
    if a.gain and a.gain != 'auto': return np.array([float(x) for x in a.gain.split(',')], np.float32), None, 'manual'
    date, iso = capture_info(rawp); box = podium_box(mask)
    sess = CFG.get('sessions', {}).get(date) if date else None
    if sess:
        g = np.array(sess['gain'], np.float32); refp = find_ref_raw(a.root, sess['reference_raw'])
        if os.path.basename(rawp) == sess['reference_raw'] or not os.path.exists(refp):
            miss = '' if os.path.exists(refp) else f' (reference raw {sess["reference_raw"]} not found, so exposure/ISO changes within the session are NOT corrected; check white panels on the contact sheet)'
            print(f'{v}: capture session {date} (ISO {iso}) -> approved session gain {g.round(4)}{miss}')
            return g, box, f'session {date}' + (' (unverified)' if miss else '')
        rr = read_raw(refp); mine, sp = box_median(raw, box); ref, _ = box_median(rr, box)
        rel = (ref / mine).astype(np.float32)
        # The same-pixel podium ratio assumes the camera and podium didn't move. If they did (e.g. the vanilla whey:
        # same session as creatine, but zoomed in with the bag on the riser), the box lands on different objects.
        # The top wall corners are always bare wall under the session's light, so they catch that.
        wall = [[300, 300, 900, 800], [raw.shape[1] - 900, 300, raw.shape[1] - 300, 800]]
        wrel = np.mean([box_median(rr, b)[0] / box_median(raw, b)[0] for b in wall], 0).astype(np.float32)
        if np.abs(rel - wrel).max() > 0.03:
            use = wrel if np.abs(wrel - 1).max() >= 0.005 else np.ones(3, np.float32)
            print(f'{v}: capture session {date} (ISO {iso}); podium ratio {rel.round(4)} disagrees with the wall ratio {wrel.round(4)}, '
                  f'so the framing/podium moved since {sess["reference_raw"]}. Using the wall -> gain {(g * use).round(4)}. Check the contact sheet.')
            return (g * use).astype(np.float32), box, f'session {date} (framing moved; wall ratio)'
        if np.abs(rel - 1).max() < 0.005:   # <0.5% is strobe noise; keep views of one product on the identical session gain
            print(f'{v}: capture session {date} (ISO {iso}); podium matches {sess["reference_raw"]} within 0.5% ({rel.round(4)}) -> session gain {g.round(4)}')
            return g, box, f'session {date}'
        print(f'{v}: capture session {date} (ISO {iso}); podium at identical pixels vs {sess["reference_raw"]}: ratio {rel.round(4)} '
              f'-> gain {(g * rel).round(4)}')
        if np.abs(rel - 1).max() > 0.03:
            print(f'{v}: NOTE exposure differs from the session reference by >3% (ISO or light change); the ratio corrects it, but check the contact sheet.')
        return (g * rel).astype(np.float32), box, f'session {date} x podium ratio'
    med, spread = box_median(raw, box)
    g = (np.array(CFG['podium_reference_rgb'], np.float32) / med).astype(np.float32)
    print(f'{v}: NEW capture session {date} (ISO {iso}) - not in config.json. Podium estimate {g.round(4)} is only a starting point: '
          f'the podium is side-lit, so the reading depends on where it is sampled. Compare the contact sheet with existing Finals '
          f'(white panels about 237-241, brand blue), adjust with --gain r,g,b, get Chance\'s approval, then add the session to assets/config.json.')
    return g, box, 'NEW-SESSION estimate'

def cmd_render(a):
    wd = work_dir(a); m = load_meta(a); vs = views(a); ref = a.ref_view or vs[0]
    masks = {v: np.load(os.path.join(wd, f'{v}_mask.npy')) for v in vs}
    ys, xs = np.where(masks[ref] > 0.5); Hr = ys.max() - ys.min() + 1; Wr = xs.max() - xs.min() + 1
    scale = (BASE - MARGIN) / Hr
    if a.scale: scale = float(a.scale)
    wmax = N - 2 * CFG['side_margin_min_px']
    if Wr * scale > wmax:
        print(f'product too wide for fill-height; scaling to fit width instead'); scale = wmax / Wr
    print(f'scale {scale:.5f} (ref view {ref}: {Wr}x{Hr}px raw -> {Wr * scale:.0f}x{Hr * scale:.0f}px on the {N}px canvas)')
    if scale > 1: print('NOTE: this upscales the camera pixels (no new detail is created). Fine print will be as sharp as the raw allows.')
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_LANCZOS4
    sheet = []
    for v in vs:
        raw = read_raw(raw_path(a, v)); mask = masks[v]
        rawp = raw_path(a, v); gain, box, how = podium_gain(raw, mask, a, v, rawp)
        if box: overlay(os.path.join(wd, f'{v}_podium_sample.jpg'), raw, mask > 0.5, box)
        raw = raw * gain
        ys, xs = np.where(mask > 0.5); pad = 60
        cy0, cy1, cx0, cx1 = max(0, ys.min() - pad), ys.max() + pad, max(0, xs.min() - pad), xs.max() + pad
        crop = raw[cy0:cy1, cx0:cx1]; mc = mask[cy0:cy1, cx0:cx1]
        tw, th = round(crop.shape[1] * scale), round(crop.shape[0] * scale)
        img = cv2.resize(crop, (tw, th), interpolation=interp); al = np.clip(cv2.resize(mc, (tw, th), interpolation=interp), 0, 1)
        bys, bxs = np.where(al > 0.5); ox = int(round(N / 2 - 0.5 - (bxs.min() + bxs.max()) / 2)); oy = int(BASE - bys.max())
        # full-frame surroundings under the product layer (lets Chance paint the mask out later)
        full = cv2.resize(raw, (round(raw.shape[1] * scale), round(raw.shape[0] * scale)), interpolation=interp)
        full = cv2.warpAffine(full, np.float32([[1, 0, ox - scale * cx0], [0, 1, oy - scale * cy0]]), (N, N), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        ung = full.copy(); A = np.zeros((N, N), np.float32)
        ys0, xs0 = max(0, oy), max(0, ox); ys1, xs1 = min(N, oy + th), min(N, ox + tw)
        ung[ys0:ys1, xs0:xs1] = img[ys0 - oy:ys1 - oy, xs0 - ox:xs1 - ox]; A[ys0:ys1, xs0:xs1] = al[ys0 - oy:ys1 - oy, xs0 - ox:xs1 - ox]
        group = a.shadow == 'group' or (a.shadow == 'auto' and m.get(v, {}).get('group', False))
        sh = contact_shadow(A, group) if a.shadow != 'none' else np.zeros_like(A)
        graded = apply_look(ung); rgba, flat = compose(graded, A, sh)
        lmp = os.path.join(wd, f'{v}_lightmap.npy')
        if os.path.exists(lmp): os.remove(lmp); print(f'{v}: removed old light match (rerun match-light if it is still wanted)')
        np.save(os.path.join(wd, f'{v}_ungraded.npy'), ung); np.save(os.path.join(wd, f'{v}_alpha.npy'), A); np.save(os.path.join(wd, f'{v}_rgba.npy'), rgba)
        np.save(os.path.join(wd, f'{v}_shadow.npy'), sh)
        to_jpg(os.path.join(wd, f'{v}_preview.jpg'), flat, 1000)
        yy, xx = np.where(A > 0.5)
        m.setdefault(v, {}).update(scale=scale, gain=gain.tolist(), gain_source=how, canvas_bbox=[int(xx.min()), int(yy.min()), int(xx.max()), int(yy.max())],
                                   shadow='none' if a.shadow == 'none' else ('group' if group else 'single'))
        print(f'{v}: on canvas x {xx.min()}-{xx.max()} y {yy.min()}-{yy.max()}  -> {v}_preview.jpg')
        sheet.append(Image.open(os.path.join(wd, f'{v}_preview.jpg')))
    ref_png = sorted(glob.glob(os.path.join(a.root, 'Final', 'PNG', '*-Front.png')))
    ref_png = [p for p in ref_png if not os.path.basename(p).startswith(a.product)][:1]
    for p in ref_png:
        im = Image.open(p).resize((1000, 1000), Image.LANCZOS); bg = Image.new('RGBA', im.size, (255, 255, 255, 255)); bg.alpha_composite(im); sheet.append(bg.convert('RGB'))
    c = Image.new('RGB', (1020 * len(sheet) - 20, 1000), (210, 210, 210))
    for i, im in enumerate(sheet): c.paste(im, (i * 1020, 0))
    c.save(os.path.join(wd, 'contact_sheet.jpg'), quality=88)
    print(f'contact sheet -> {os.path.join(wd, "contact_sheet.jpg")}' + (f' (last tile = existing {os.path.basename(ref_png[0])} for consistency)' if ref_png else ''))
    save_meta(a, m)

# ---------------------------------------------------------------- match-light
def _bbox(A):
    ys, xs = np.where(A > 0.5); return xs.min(), ys.min(), xs.max(), ys.max()

def _white_samples(ung, A, step=4):
    """Low-res linear-light means of flat, bright, neutral label areas (no print, no crease edges, no outline)."""
    er = cv2.erode((A > 0.99).astype(np.uint8), np.ones((61, 61), np.uint8)) > 0
    lin = np.clip(ung, 0, 1) ** (563 / 256); m = cv2.blur(lin, (25, 25)); sd = np.sqrt(np.clip((cv2.blur(lin ** 2, (25, 25)) - m ** 2).mean(2), 0, None))
    ok = er & (m.mean(2) > 0.45) & (m.max(2) - m.min(2) < 0.06) & (sd < 0.02)
    return m[::step, ::step], ok[::step, ::step]

LF_GRID = 48; LF_LO, LF_HI = -0.05, 1.05   # light field lives on a grid in bag-relative coords (0..1 = product bbox)

def light_map(A, field):
    """Light field (grid, linear-light gain per channel) -> canvas-size multiplier in Adobe RGB *encoded* values
    (what a Photoshop Multiply layer does), placed by this view's product bbox."""
    x0, y0, x1, y1 = _bbox(A); s = 8; yy, xx = np.mgrid[0:N:s, 0:N:s].astype(np.float32)
    gx = (((xx - x0) / (x1 - x0) - LF_LO) / (LF_HI - LF_LO) * LF_GRID - 0.5).astype(np.float32); gy = (((yy - y0) / (y1 - y0) - LF_LO) / (LF_HI - LF_LO) * LF_GRID - 0.5).astype(np.float32)
    g = np.stack([cv2.remap(np.ascontiguousarray(field[..., c]), gx, gy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE) for c in range(3)], -1)
    return np.clip(cv2.resize(g ** (256 / 563), (N, N), interpolation=cv2.INTER_CUBIC), 0, 1).astype(np.float32)

def cmd_match_light(a):
    """Match this product's lighting to an approved donor shot of the same packaging (e.g. the chocolate pouch to the vanilla).
    Measures donor/this ratio on flat white label areas, smooths it into a gentle light field (no local detail), caps it at 1
    so it only darkens, and applies it as a multiplier. Pure tone change on real pixels; export puts it on its own Multiply layer."""
    wd = work_dir(a); m = load_meta(a); vs = views(a); ref = a.ref_view or vs[0]
    dprod, dview = a.to.split(':'); dwd = os.path.expanduser(f'~/Library/Caches/swolverine-product-photos/{dprod}')
    uc, Ac = np.load(os.path.join(wd, f'{ref}_ungraded.npy')), np.load(os.path.join(wd, f'{ref}_alpha.npy'))
    ud, Ad = np.load(os.path.join(dwd, f'{dview}_ungraded.npy')), np.load(os.path.join(dwd, f'{dview}_alpha.npy'))
    mc, okc = _white_samples(uc, Ac); md, okd = _white_samples(ud, Ad)
    yy, xx = np.mgrid[0:N:4, 0:N:4].astype(np.float32); cx0, cy0, cx1, cy1 = _bbox(Ac); dx0, dy0, dx1, dy1 = _bbox(Ad)
    U = (xx - cx0) / (cx1 - cx0); V = (yy - cy0) / (cy1 - cy0)
    mx = ((dx0 + U * (dx1 - dx0)) / 4).astype(np.float32); my = ((dy0 + V * (dy1 - dy0)) / 4).astype(np.float32)   # donor sampled at the same bag-relative spot
    mds = np.stack([cv2.remap(np.ascontiguousarray(md[..., c]), mx, my, cv2.INTER_LINEAR) for c in range(3)], -1)
    okds = cv2.remap(okd.astype(np.float32), mx, my, cv2.INTER_NEAREST) > 0.5
    ok = okc & okds & (mds.min(2) > 0)
    lr = np.log(mds[ok] / mc[ok]); gi = np.clip(((U[ok] - LF_LO) / (LF_HI - LF_LO) * LF_GRID).astype(int), 0, LF_GRID - 1)
    gj = np.clip(((V[ok] - LF_LO) / (LF_HI - LF_LO) * LF_GRID).astype(int), 0, LF_GRID - 1)
    num = np.zeros((LF_GRID, LF_GRID, 3), np.float32); cnt = np.zeros((LF_GRID, LF_GRID), np.float32)
    cell = gj * LF_GRID + gi
    for k in np.unique(cell):   # per-cell median: wrinkles and crease shading don't drag it
        sel = cell == k; num.flat[k * 3:k * 3 + 3] = np.median(lr[sel], 0); cnt.flat[k] = sel.sum()
    w = (cnt >= 20).astype(np.float32); sg = a.smooth * LF_GRID
    num = np.stack([cv2.GaussianBlur(num[..., c] * w, (0, 0), sg) for c in range(3)], -1); den = cv2.GaussianBlur(w, (0, 0), sg)
    field = np.exp(num / np.maximum(den, 1e-6)[..., None]); raw_max = field.max()
    field = np.minimum(field, 1.0).astype(np.float32)          # darken only -> a clean Multiply layer
    np.save(os.path.join(wd, 'light_field.npy'), field)
    print(f'light field from {ok.sum()} white samples vs {a.to}: gain {field.min():.3f}..{field.max():.3f} (linear light; uncapped max {raw_max:.3f}, capped at 1 = darken only)')
    for v in vs:
        ung = np.load(os.path.join(wd, f'{v}_ungraded.npy')); A = np.load(os.path.join(wd, f'{v}_alpha.npy'))
        lm = light_map(A, field); np.save(os.path.join(wd, f'{v}_lightmap.npy'), lm)
        rgba, flat = compose(apply_look(ung * lm), A, load_shadow(wd, v, A)); np.save(os.path.join(wd, f'{v}_rgba.npy'), rgba)
        to_jpg(os.path.join(wd, f'{v}_preview.jpg'), flat, 1000)
        m.setdefault(v, {})['light_match'] = dict(donor=a.to, smooth=a.smooth, field_min=float(field.min()))
        print(f'{v}: light match applied (multiplier {lm[A > 0.99].min():.3f}..{lm[A > 0.99].max():.3f} in encoded values) -> {v}_preview.jpg')
    save_meta(a, m)

# ---------------------------------------------------------------- qa
def cmd_qa(a):
    wd = work_dir(a)
    for v in views(a):
        rgba = np.load(os.path.join(wd, f'{v}_rgba.npy')); A = np.load(os.path.join(wd, f'{v}_alpha.npy'))
        s = (adobe_to_srgb(rgba[..., :3]) * 255 + 0.5).astype(np.uint8); op = A > 0.99
        clip = ((s[op] >= 255).all(1)).mean() * 100
        blue = op & (s[..., 2].astype(int) - s[..., 0] > 90) & (s[..., 1] > 120)
        bm = np.median(s[blue], 0).astype(int) if blue.sum() > 1000 else None
        flat = np.where(op[..., None], s, 255).astype(np.uint8)
        # fine-print crops: the 4 densest high-contrast 600px windows inside the product, at 100%
        g = cv2.cvtColor(flat, cv2.COLOR_RGB2GRAY).astype(np.float32)
        e = (np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 0)) + np.abs(cv2.Sobel(g, cv2.CV_32F, 0, 1)) > 120).astype(np.float32) * op
        dens = cv2.boxFilter(e, -1, (600, 600)); picks = []
        for _ in range(4):
            y, x = np.unravel_index(np.argmax(dens), dens.shape); picks.append((int(y), int(x)))
            dens[max(0, y - 600):y + 600, max(0, x - 600):x + 600] = 0
        outs = []
        for i, (y, x) in enumerate(picks):
            y0, x0 = int(np.clip(y - 300, 0, N - 600)), int(np.clip(x - 300, 0, N - 600))
            p = os.path.join(wd, f'{v}_qa_print{i + 1}.jpg'); Image.fromarray(flat[y0:y0 + 600, x0:x0 + 600]).save(p, quality=92); outs.append(os.path.basename(p))
        # edge crops around the outline (on white) at 100%
        ys, xs = np.where(op); x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max(); mx = (x0 + x1) // 2; my = (y0 + y1) // 2
        pts = [(x0, y0), (mx, y0), (x1, y0), (x0, my), (x1, my), (x0, y1), (mx, y1), (x1, y1)]
        tiles = []; labels = ['top-left', 'top', 'top-right', 'left', 'right', 'bottom-left', 'bottom', 'bottom-right']
        for (x, y), lab in zip(pts, labels):
            xa, ya = int(np.clip(x - 120, 0, N - 240)), int(np.clip(y - 120, 0, N - 240)); t = flat[ya:ya + 240, xa:xa + 240].copy()
            cv2.putText(t, lab, (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 0, 0), 1, cv2.LINE_AA); tiles.append(t)
        Image.fromarray(np.vstack([np.hstack(tiles[:4]), np.hstack(tiles[4:])])).save(os.path.join(wd, f'{v}_qa_edges.jpg'), quality=92)
        ref = CFG['qa']['brand_blue_srgb'].get(a.product)
        print(f'{v}: clipped-to-white {clip:.2f}% of product' + ('  (WARNING high)' if clip > CFG['qa']['max_clipped_pct_warn'] else '') +
              f' | brand blue {("#%02X%02X%02X" % tuple(bm)) if bm is not None else "n/a"}' + (f' (approved {a.product}: {ref})' if ref else ' (compare with #00BEF4 creatine / #00C9F0 whey)'))
        print(f'   look at: {", ".join(outs)}, {v}_qa_edges.jpg  (fine print must be crisp and complete; edges clean, no halo, no podium)')

# ---------------------------------------------------------------- webp
WEBP_QUALITY = 90   # Chance wanted much smaller WebPs (2026-09-29). At q90 fine print is indistinguishable from the PNG at 2x zoom

def png_dir(a):
    """Where web PNGs go: Final/PNG/[<subdir>/] (Chance's layout since 2026-10-01), or --final-dir."""
    return a.final_dir or os.path.join(a.root, 'Final', 'PNG', *([a.subdir] if a.subdir else []))

def webp_path(png):
    """Final/PNG/<sub>/<name>.png -> Final/WebP/<sub>/<name>.webp. A PNG outside a Final/PNG tree (custom --final-dir)
    gets its WebP in a WebP/ folder beside it."""
    d, name = os.path.split(os.path.abspath(png)); parts = d.split(os.sep)
    if 'PNG' in parts and parts[parts.index('PNG') - 1] == 'Final':
        i = parts.index('PNG'); d = os.sep.join(parts[:i] + ['WebP'] + parts[i + 1:])
    else: d = os.path.join(d, 'WebP')
    return os.path.join(d, os.path.splitext(name)[0] + '.webp')

def write_png(png, rgba_adobe):
    """Straight-alpha Adobe RGB float RGBA -> the web PNG: 8-bit sRGB, transparent, embedded sRGB profile, 300 dpi."""
    from PIL import ImageCms
    icc_srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    px = np.dstack([adobe_to_srgb(rgba_adobe[..., :3]), np.clip(rgba_adobe[..., 3], 0, 1)])
    Image.fromarray((px * 255 + 0.5).astype(np.uint8)).save(png, icc_profile=icc_srgb, dpi=(300, 300), optimize=True)

def write_webp(png, overwrite=False, lossless=False):
    """Final/PNG/<name>.png -> Final/WebP/<name>.webp: lossy q90 (about 0.2-0.5 MB vs 3-6 MB PNG), alpha and sRGB profile kept.
    Decoded back and checked: alpha must match the PNG exactly, colour mean error under 2.5 levels. --lossless = pixel-identical."""
    out = webp_path(png); os.makedirs(os.path.dirname(out), exist_ok=True)
    if os.path.exists(out) and not overwrite: sys.exit(f'{out} exists. Rerun with --overwrite only if Chance said to replace it.')
    im = Image.open(png); icc = im.info.get('icc_profile'); im = im.convert('RGBA')
    if lossless: im.save(out, 'WEBP', lossless=True, quality=100, method=6, exact=True, icc_profile=icc)
    else: im.save(out, 'WEBP', quality=WEBP_QUALITY, alpha_quality=100, method=6, icc_profile=icc)
    ref = np.asarray(im).astype(np.int16); dec = np.asarray(Image.open(out).convert('RGBA')).astype(np.int16)
    op = ref[..., 3] > 250; err = np.abs(dec[..., :3] - ref[..., :3])[op].mean(); da = np.abs(dec[..., 3] - ref[..., 3]).max()
    if da > 1 or err > (0 if lossless else 2.5): os.remove(out); sys.exit(f'{out}: decoded WebP off (colour mean {err:.2f}, alpha max {da}); not kept.')
    print(f'   wrote {out} ({os.path.getsize(out) / 1e6:.2f} MB vs PNG {os.path.getsize(png) / 1e6:.1f} MB; ' +
          ('pixel-identical)' if lossless else f'q{WEBP_QUALITY}, colour mean error {err:.2f} levels, alpha exact)'))
    return out

def cmd_webp(a):
    """WebP copies of PNGs already in Final/PNG/ (e.g. after Chance re-exports a hand-edited PNG). --views all = every PNG
    in that folder (add --subdir Bundles for the group shots)."""
    final = png_dir(a)
    pngs = sorted(glob.glob(os.path.join(final, '*.png'))) if a.views == 'all' else [os.path.join(final, f'{a.product}-{v}.png') for v in views(a)]
    for p in pngs:
        if not os.path.exists(p): sys.exit(f'{p} not found')
        write_webp(p, a.overwrite, a.lossless)

# ---------------------------------------------------------------- export
def cmd_export(a):
    wd = work_dir(a); final = png_dir(a); wip = a.wip_dir or os.path.join(a.root, 'WIP', *([a.subdir] if a.subdir else []))   # bundle masters in WIP/Bundles/ (Chance, 2026-10-06)
    os.makedirs(final, exist_ok=True); os.makedirs(wip, exist_ok=True)
    icc_adobe = open(os.path.join(ASSETS, 'AdobeRGB1998.icc'), 'rb').read()
    todo = []
    for v in views(a):
        name = f'{a.product}-{v}'; png = os.path.join(final, name + '.png'); tif = os.path.join(wip, name + '.tif')
        for p in ([png] + ([] if a.no_tiff else [tif])):
            if os.path.exists(p) and not a.overwrite: sys.exit(f'{p} exists. Rerun with --overwrite only if Chance said to replace it.')
        wp = webp_path(png)
        if os.path.exists(wp) and not a.overwrite: sys.exit(f'{wp} exists. Rerun with --overwrite only if Chance said to replace it.')
        write_png(png, np.load(os.path.join(wd, f'{v}_rgba.npy')))
        print(f'{v}: wrote {png}'); write_webp(png, a.overwrite)
        if a.no_tiff: continue
        ung = np.load(os.path.join(wd, f'{v}_ungraded.npy')); A = np.load(os.path.join(wd, f'{v}_alpha.npy'))
        inp = os.path.join(wd, f'{v}_ps_input.tif')
        # masks ride along as ordinary alpha channels (extrasamples=0) so Photoshop never has to load a
        # selection from another document (that crashes Photoshop 2026, see troubleshooting.md)
        stack = np.dstack([ung, A, load_shadow(wd, v, A)])
        tifffile.imwrite(inp, (np.clip(stack, 0, 1) * 65535 + 0.5).astype(np.uint16), photometric='rgb', extrasamples=[0, 0],
                         compression='zlib', planarconfig='contig', extratags=[(34675, 7, len(icc_adobe), icc_adobe, True)])
        light = ''; lmp = os.path.join(wd, f'{v}_lightmap.npy')
        if os.path.exists(lmp):   # light match rides in its own file and is pasted in as a Multiply layer
            light = os.path.join(wd, f'{v}_ps_light.tif')
            tifffile.imwrite(light, (np.load(lmp) * 65535 + 0.5).astype(np.uint16), photometric='rgb', compression='zlib',
                             extratags=[(34675, 7, len(icc_adobe), icc_adobe, True)])
        todo.append(dict(view=v, inp=inp, light=light, out=os.path.join(wd, f'{v}_layered.tif'), render=os.path.join(wd, f'{v}_psrender.png'), final=tif))
    if not todo: return
    curves = json.load(open(os.path.join(ASSETS, 'look_curves.json')))
    body = 'var jobs=' + json.dumps(todo) + '; var CURVES=' + json.dumps(curves) + ';\n' + r'''
function cid(s){return charIDToTypeID(s);} function sid(s){return stringIDToTypeID(s);}
function maskFromSel(){ var d=new ActionDescriptor(); d.putClass(cid("Nw  "), cid("Chnl")); var r=new ActionReference(); r.putEnumerated(cid("Chnl"), cid("Chnl"), cid("Msk ")); d.putReference(cid("At  "), r); d.putEnumerated(cid("Usng"), cid("UsrM"), cid("RvlS")); executeAction(cid("Mk  "), d, DialogModes.NO); }
function curvesLayer(name){
  var d=new ActionDescriptor(); var r=new ActionReference(); r.putClass(cid("AdjL")); d.putReference(cid("null"), r);
  var l=new ActionDescriptor(); l.putString(cid("Nm  "), name); l.putBoolean(cid("Grup"), true);
  var c=new ActionDescriptor(); c.putEnumerated(sid("presetKind"), sid("presetKindType"), sid("presetKindCustom"));
  var list=new ActionList(); var ch=[["R","Rd  "],["G","Grn "],["B","Bl  "]];
  for (var i=0;i<3;i++){ var cd=new ActionDescriptor(); var cr=new ActionReference(); cr.putEnumerated(cid("Chnl"), cid("Chnl"), cid(ch[i][1])); cd.putReference(cid("Chnl"), cr);
    var pl=new ActionList(); var P=CURVES[ch[i][0]]; for (var k=0;k<P.length;k++){ var p=new ActionDescriptor(); p.putDouble(cid("Hrzn"), P[k][0]); p.putDouble(cid("Vrtc"), P[k][1]); pl.putObject(cid("Pnt "), p); }
    cd.putList(cid("Crv "), pl); list.putObject(cid("CrvA"), cd); }
  c.putList(cid("Adjs"), list); l.putObject(cid("Type"), cid("Crvs"), c); d.putObject(cid("Usng"), cid("AdjL"), l); executeAction(cid("Mk  "), d, DialogModes.NO); }
function fillAll(doc, v){ var c=new SolidColor(); c.rgb.red=v; c.rgb.green=v; c.rgb.blue=v; doc.selection.selectAll(); doc.selection.fill(c); doc.selection.deselect(); }
var log=[];
for (var i=0;i<jobs.length;i++){ var j=jobs[i]; var doc=null;
  try{
    doc=app.open(new File(j.inp));
    var prod=doc.activeLayer; prod.isBackgroundLayer=false; prod.name="Product";
    doc.selection.load(doc.channels[3], SelectionType.REPLACE); maskFromSel(); doc.selection.deselect();
    var top=prod;
    if (j.light){ var ld=app.open(new File(j.light)); ld.selection.selectAll(); ld.selection.copy(); ld.close(SaveOptions.DONOTSAVECHANGES);
      app.activeDocument=doc; doc.activeLayer=prod; var lt=doc.paste(); lt=doc.activeLayer; lt.name="Light match (Multiply)"; lt.blendMode=BlendMode.MULTIPLY; lt.grouped=true; top=lt; }
    doc.activeLayer=top; curvesLayer("Look (Curves)");
    var shd=doc.artLayers.add(); shd.name="Shadow"; fillAll(doc,0);
    doc.selection.load(doc.channels[4], SelectionType.REPLACE); maskFromSel(); doc.selection.deselect(); shd.opacity=100; shd.move(prod, ElementPlacement.PLACEAFTER);
    var wb=doc.artLayers.add(); wb.name="White background"; fillAll(doc,255); wb.move(shd, ElementPlacement.PLACEAFTER); wb.visible=false;
    doc.channels[4].remove(); doc.channels[3].remove();
    var o=new TiffSaveOptions(); o.layers=true; o.transparency=true; o.embedColorProfile=true; o.imageCompression=TIFFEncoding.TIFFZIP; o.layerCompression=LayerCompression.ZIP; o.alphaChannels=false;
    doc.saveAs(new File(j.out), o, true, Extension.LOWERCASE);
    var vd=doc.duplicate("verify"); vd.layers[vd.layers.length-1].remove(); vd.mergeVisibleLayers(); vd.saveAs(new File(j.render), new PNGSaveOptions(), true, Extension.LOWERCASE); vd.close(SaveOptions.DONOTSAVECHANGES);
    log.push(j.view+" ok");
  }catch(e){ log.push(j.view+" ERR line "+e.line+": "+e); }
  if (doc) doc.close(SaveOptions.DONOTSAVECHANGES);
}
return log.join(" | ");'''
    run_jsx(body, 'build_layered')
    for j in todo:
        if not os.path.exists(j['out']): print(f"{j['view']}: layered TIFF was not produced"); continue
        ps = cv2.cvtColor(cv2.imread(j['render'], cv2.IMREAD_UNCHANGED), cv2.COLOR_BGRA2RGBA).astype(np.float32) / 65535
        ap = np.load(os.path.join(wd, f"{j['view']}_rgba.npy"))
        op = (ap[..., 3] > 0.99) & (ps[..., 3] > 0.99); d = np.abs(ps[..., :3] - ap[..., :3])[op] * 255; da = np.abs(ps[..., 3] - ap[..., 3]).max() * 255
        ok = d.mean() < 1.0 and np.percentile(d, 99) < 3 and da < 2
        print(f"{j['view']}: Photoshop render of layered TIFF vs PNG: colour mean {d.mean():.2f} p99 {np.percentile(d, 99):.2f} max {d.max():.1f} levels, alpha max {da:.1f} -> {'OK' if ok else 'MISMATCH, not moved to WIP'}")
        if ok:
            os.replace(j['out'], j['final']); print(f"   wrote {j['final']}")

# ---------------------------------------------------------------- reshadow (hand-edited masters)
# Chance retouches masters in Photoshop (2026-10-06: trimmed the protein-bag bottoms up to 24px and removed the gusset),
# which leaves the Shadow layer where the old bottom was: the bag looks like it floats. reshadow refits the shadow to
# the master as it is now and swaps only the Shadow layer. Two traps it guards against:
#  - The TIFF composite is ASSOCIATED (premultiplied) alpha. Reading it as straight alpha darkened every anti-aliased
#    edge pixel by ~100 levels, and a check on opaque pixels only did not see it.
#  - Duplicating a layer in and removing the old one un-clipped "Look (Curves)" and "Light match (Multiply)". With
#    Light match the whole canvas turned opaque; without it the render still matched, so the change was silent.
def _layer_info(path):
    """Layer records + channel data of a layered TIFF master (Photoshop tag 37724), its composite as premultiplied
    0..1 RGBA, and the bit depth."""
    import struct, io
    from psd_tools.psd.layer_and_mask import LayerInfo
    with tifffile.TiffFile(path) as t:
        pg = t.pages[0]; comp = pg.asarray()
        assoc = bool(pg.extrasamples) and int(pg.extrasamples[0]) == 1
        if 37724 not in pg.tags: sys.exit(f'{path}: no Photoshop layers in this TIFF.')
        blob = bytes(pg.tags[37724].value)
    for key, depth in ((b'Lr16', 16), (b'Layr', 8)):
        i = blob.find(b'8BIM' + key)
        if i >= 0: break
    else: sys.exit(f'{path}: no 8- or 16-bit layer block found.')
    ln = struct.unpack('>I', blob[i + 8:i + 12])[0]
    li = LayerInfo.read(io.BytesIO(struct.pack('>I', ln) + blob[i + 12:i + 12 + ln]), version=1)
    C = comp.astype(np.float32) / np.iinfo(comp.dtype).max
    if C.ndim != 3 or C.shape[2] < 4 or C[..., 3].min() > 0.99:
        sys.exit(f'{path}: the saved composite has no transparency. In Photoshop, hide "White background", save, and rerun.')
    C = C[..., :4]
    if not assoc: C[..., :3] *= C[..., 3:]
    return li, C, depth

def _channel(rec, data, cid, depth, shape):
    """One layer channel as a full-canvas 0..1 array (None if the layer has no such channel). -1 = transparency, -2 = layer mask."""
    ids = [c.id for c in rec.channel_info]
    if cid not in ids: return None
    if cid == -2:
        m = rec.mask_data
        if m is None or m.flags.mask_disabled: return None
        t, l, b, r, fill = m.top, m.left, m.bottom, m.right, (m.background_color or 0) / 255
    else: t, l, b, r, fill = rec.top, rec.left, rec.bottom, rec.right, 0.0
    full = np.full(shape, fill, np.float32); h, w = b - t, r - l
    if h > 0 and w > 0:
        dt, mx = ('>u2', 65535) if depth == 16 else ('u1', 255)
        px = np.frombuffer(data[ids.index(cid)].get_data(w, h, depth, 1), dt).reshape(h, w).astype(np.float32) / mx
        y0, x0, y1, x1 = max(0, t), max(0, l), min(shape[0], b), min(shape[1], r)
        if y1 > y0 and x1 > x0: full[y0:y1, x0:x1] = px[y0 - t:y1 - t, x0 - l:x1 - l]
    return full

def _shadow_record(li):
    k = [n for n, r in enumerate(li.layer_records) if r.name == 'Shadow']
    if len(k) != 1: return None, None
    return k[0], li.layer_records[k[0]]

def _effective_shadow(li, depth, shape):
    """What the Shadow layer adds: its transparency x layer mask x opacity (0 when hidden). It must be black and Normal."""
    k, rec = _shadow_record(li)
    if rec is None: return None, None
    data = li.channel_image_data[k]
    a = _channel(rec, data, -1, depth, shape); a = np.ones(shape, np.float32) if a is None else a
    mk = _channel(rec, data, -2, depth, shape)
    if mk is not None: a *= mk
    rgb = [_channel(rec, data, c, depth, shape) for c in (0, 1, 2)]
    if any(c is not None and c[a > .01].max(initial=0) > 2 / 255 for c in rgb):
        sys.exit('The Shadow layer is not black (painted by hand?). reshadow only replaces a black shadow; fix it in Photoshop.')
    if rec.blend_mode not in (b'norm', 'norm') and str(rec.blend_mode).split('.')[-1].lower() != 'normal':
        sys.exit(f'The Shadow layer blend mode is {rec.blend_mode}, not Normal.')
    return a * (rec.opacity / 255) * (1.0 if rec.flags.visible else 0.0), rec

def _signature(li, depth):
    """Per-layer fingerprint (name, clipping, visibility, opacity, blend, decoded channel data) to prove that only the
    Shadow layer changed."""
    import hashlib
    out = []
    for rec, data in zip(li.layer_records, li.channel_image_data):
        h = hashlib.sha1()
        for ci, c in zip(rec.channel_info, data):
            m = rec.mask_data
            t, l, b, r = (m.top, m.left, m.bottom, m.right) if ci.id == -2 and m is not None else (rec.top, rec.left, rec.bottom, rec.right)
            h.update(f'{ci.id}:{t},{l},{b},{r}'.encode())
            try: h.update(c.get_data(r - l, b - t, depth, 1) if r > l and b > t else b'')
            except Exception: h.update(c.data)
        out.append((rec.name, int(rec.clipping), bool(rec.flags.visible), rec.opacity, str(rec.blend_mode), h.hexdigest()))
    return out

def _base_gap(alpha, s):
    """Median and 90th-percentile distance (px) from the product's bottom edge down to the darkest shadow row,
    over the middle 60% of the base. ~2px when the shadow hugs the edge; 20+ when it was left behind."""
    yb = _bottom(alpha); ys = np.where(alpha > .5)[0]; H = ys.max() - ys.min() + 1
    cols = np.where(yb >= ys.max() - .04 * H)[0]; lo, hi = cols[0], cols[-1]
    xs = [x for x in range(int(lo + .2 * (hi - lo)), int(hi - .2 * (hi - lo))) if not np.isnan(yb[x])]
    g = [int(np.argmax(s[int(yb[x]) + 1:int(yb[x]) + 200, x])) for x in xs]
    return (float(np.median(g)), float(np.percentile(g, 90))) if g else (float('nan'), float('nan'))

def _reshadow_names(a):
    names = [f'{a.product}-{v}' for v in views(a)] if a.product and a.views else []
    names += a.name or []
    if not names: sys.exit('reshadow needs --product and --views, and/or --name "<master file name>" (repeatable).')
    return names

def cmd_reshadow(a):
    """Refit the contact shadow to a master Chance edited by hand, from the master's own composite. Without --apply it
    only writes a before/after preview to the cache. With --apply it swaps the Shadow layer in Photoshop (nothing else),
    proves every other layer is untouched, then rewrites the PNG + WebP. Old master + PNG -> WIP/Previous versions/."""
    wip = a.wip_dir or os.path.join(a.root, 'WIP', *([a.subdir] if a.subdir else [])); final = png_dir(a)
    wd = a.work or os.path.expanduser('~/Library/Caches/swolverine-product-photos/_reshadow'); os.makedirs(wd, exist_ok=True)
    jobs, plans = [], {}
    for name in _reshadow_names(a):
        tif = os.path.join(wip, name + '.tif')
        if not os.path.exists(tif): sys.exit(f'{tif} not found.')
        li, C, depth = _layer_info(tif); shape = C.shape[:2]
        s_old, rec = _effective_shadow(li, depth, shape)
        if s_old is None: sys.exit(f'{tif}: no single layer named "Shadow" (a master from before v0.2.0 still has its Reflection; re-export it).')
        ca = C[..., 3]; A = np.clip((ca - s_old) / np.maximum(1 - s_old, 1e-6), 0, 1)       # product = composite minus the old shadow
        A[A < 0.5 / 65535] = 0
        rgb = np.where(A[..., None] > 1e-4, C[..., :3] / np.maximum(A, 1e-4)[..., None], 0).clip(0, 1)   # premultiplied -> straight
        group = a.shadow == 'group' or (a.shadow == 'auto' and os.path.basename(os.path.dirname(os.path.abspath(tif))) == 'Bundles')
        s_new = contact_shadow(A, group)
        op = (rec.opacity / 255) * (1.0 if rec.flags.visible else 0.0)
        rgba, _ = compose(rgb, A, s_new * op)
        ys = np.where(A > .5)[0]; g0, g1 = _base_gap(A, s_old), _base_gap(A, s_new * op)
        cur = C[..., :3] + (1 - ca[..., None]); new = rgba[..., :3] * rgba[..., 3:] + (1 - rgba[..., 3:])   # both on white
        dw = np.abs(adobe_to_srgb(np.clip(new, 0, 1)) - adobe_to_srgb(np.clip(cur, 0, 1))).max(-1) * 255
        print(f'{name}: {"group shot, one shadow per product" if group else "single product"}; lowest product row {ys.max()} '
              f'({BASE - ys.max():+d}px vs the {BASE} baseline); Shadow layer opacity {rec.opacity / 2.55:.0f}%{"" if rec.flags.visible else " (hidden)"}')
        print(f'   darkest shadow row below the bottom edge: now {g0[0]:.0f}px (p90 {g0[1]:.0f}), refit {g1[0]:.0f}px (p90 {g1[1]:.0f}); '
              f'change on white: max {dw.max():.0f}, {(dw > 2).sum()} px over 2 levels')
        # preview: base strip, current over refit, on white
        ys_, xs_ = np.where(A > .5); x0, x1 = max(0, xs_.min() - 120), min(shape[1], xs_.max() + 120); y0, y1 = max(0, ys_.max() - 160), min(shape[0], ys_.max() + 160)
        strips = []
        for img, label in ((cur, 'current master'), (new, 'refit shadow')):
            st = (adobe_to_srgb(np.clip(img[y0:y1, x0:x1], 0, 1)) * 255 + .5).astype(np.uint8); sc = 1600 / st.shape[1]
            st = cv2.resize(st, (1600, int(st.shape[0] * sc)), interpolation=cv2.INTER_AREA)
            cv2.putText(st, f'{name}: {label}', (12, 30), cv2.FONT_HERSHEY_SIMPLEX, .8, (40, 40, 40), 2); strips += [st, np.full((6, 1600, 3), 170, np.uint8)]
        pv = os.path.join(wd, f'{name}_reshadow.jpg'); Image.fromarray(np.vstack(strips[:-1])).save(pv, quality=88); print(f'   preview: {pv}')
        plans[name] = dict(tif=tif, li=li, depth=depth, s_new=s_new, op=op, rgba=rgba)
        if a.apply:
            sh = np.zeros(shape + (4,), np.uint16); sh[..., 3] = (np.clip(s_new, 0, 1) * 65535 + .5).astype(np.uint16)
            cv2.imwrite(os.path.join(wd, f'{name}_shadow16.png'), sh)
            jobs.append(dict(name=name, tif=tif, shadow=os.path.join(wd, f'{name}_shadow16.png'), out=os.path.join(wd, f'{name}_reshadowed.tif')))
    if not a.apply:
        print('Preview only. Show Chance the previews; rerun with --apply once approved.'); return
    body = 'var jobs=' + json.dumps(jobs) + r''';
function walk(c, f){ for (var i=0;i<c.layers.length;i++){ var l=c.layers[i]; f(l); if (l.typename=="LayerSet") walk(l, f); } }
var log=[];
for (var i=0;i<jobs.length;i++){ var j=jobs[i]; var doc=null, sd=null;
  try{
    doc=app.open(new File(j.tif));
    var clip={}, old=null, n=0; walk(doc, function(l){ if (l.typename=="ArtLayer"){ clip[l.id]=l.grouped; if (l.name=="Shadow"){ old=l; n++; } } });
    if (n!=1) throw "expected one Shadow layer, found "+n;
    var vis=old.visible, op=old.opacity;
    sd=app.open(new File(j.shadow)); var src=sd.layers[0]; if (src.isBackgroundLayer) src.isBackgroundLayer=false;
    src.duplicate(doc, ElementPlacement.PLACEATBEGINNING); sd.close(SaveOptions.DONOTSAVECHANGES); sd=null;
    app.activeDocument=doc; var sh=doc.layers[0]; sh.name="Shadow"; sh.blendMode=BlendMode.NORMAL; sh.opacity=op;
    sh.move(old, ElementPlacement.PLACEBEFORE); old.remove(); sh.visible=vis;
    var fixed=[]; walk(doc, function(l){ if (l.typename=="ArtLayer" && (l.id in clip) && l.grouped!=clip[l.id]){ doc.activeLayer=l; l.grouped=clip[l.id]; fixed.push(l.name); } });
    var o=new TiffSaveOptions(); o.layers=true; o.transparency=true; o.embedColorProfile=true; o.imageCompression=TIFFEncoding.TIFFZIP; o.layerCompression=LayerCompression.ZIP; o.alphaChannels=false;
    doc.saveAs(new File(j.out), o, true, Extension.LOWERCASE);
    log.push(j.name+"\t ok"+(fixed.length ? " (re-clipped "+fixed.join(", ")+")" : ""));
  }catch(e){ log.push(j.name+"\t ERR line "+e.line+": "+e); }
  if (sd) sd.close(SaveOptions.DONOTSAVECHANGES); if (doc) doc.close(SaveOptions.DONOTSAVECHANGES);
}
return log.join(" | ");'''
    out = run_jsx(body, 'reshadow', timeout=3600)
    status = dict(s.split('\t', 1) for s in out.split(' | ') if '\t' in s)
    pv_dir = os.path.join(a.root, 'WIP', 'Previous versions'); os.makedirs(pv_dir, exist_ok=True)
    def backup(path):
        stem, ext = os.path.splitext(os.path.basename(path)); dst = os.path.join(pv_dir, f'{stem}-pre-reshadow{ext}'); k = 2
        while os.path.exists(dst): dst = os.path.join(pv_dir, f'{stem}-pre-reshadow-{k}{ext}'); k += 1
        shutil.move(path, dst); return dst
    for j in jobs:
        name, p = j['name'], plans[j['name']]; st = status.get(name, ' no result from Photoshop')
        if not st.strip().startswith('ok') or not os.path.exists(j['out']): print(f'{name}: Photoshop:{st}; master left as it was.'); continue
        li2, C2, depth2 = _layer_info(j['out']); shape = C2.shape[:2]
        k_old, _ = _shadow_record(p['li']); k_new, rec2 = _shadow_record(li2)
        s1, s2 = _signature(p['li'], p['depth']), _signature(li2, depth2)
        problems = []
        if k_new != k_old or len(s1) != len(s2): problems.append('layer order changed')
        else:
            for n, (u, v) in enumerate(zip(s1, s2)):
                if n == k_old: continue
                if u != v: problems.append(f'layer "{u[0]}" changed ' + ', '.join(f for f, x, y in zip(('name', 'clipping', 'visibility', 'opacity', 'blend', 'pixels'), u, v) if x != y))
            s2a = _channel(rec2, li2.channel_image_data[k_new], -1, depth2, shape)
            if s2a is None or np.abs(s2a - np.clip(p['s_new'], 0, 1)).max() > 2 / 255: problems.append('new Shadow pixels off')
            if rec2.opacity != p['li'].layer_records[k_old].opacity or rec2.flags.visible != p['li'].layer_records[k_old].flags.visible: problems.append('Shadow opacity/visibility not kept')
        ca2 = C2[..., 3]; m = ca2 > 0.05; st2 = C2[..., :3] / np.maximum(ca2, 1e-6)[..., None]
        d = np.abs(st2 - p['rgba'][..., :3])[m] * 255; da = np.abs(ca2 - p['rgba'][..., 3]).max() * 255
        if not (d.mean() < 1 and np.percentile(d, 99) < 3 and da < 2.5): problems.append(f'render off (colour mean {d.mean():.2f}, p99 {np.percentile(d, 99):.2f}, alpha max {da:.1f})')
        if problems: print(f'{name}: NOT applied, master left as it was: ' + '; '.join(problems) + f' (Photoshop output kept at {j["out"]})'); continue
        print(f'{name}: Photoshop{st}; only the Shadow layer changed; render vs new PNG colour mean {d.mean():.3f} (every pixel above 5% alpha), alpha max {da:.2f}')
        print(f'   master: old -> {backup(j["tif"])}'); shutil.move(j['out'], j['tif']); print(f'   wrote {j["tif"]}')
        png = os.path.join(final, name + '.png'); os.makedirs(final, exist_ok=True)
        if os.path.exists(png): print(f'   PNG: old -> {backup(png)}')
        write_png(png, p['rgba']); print(f'   wrote {png}'); write_webp(png, overwrite=True)

# ---------------------------------------------------------------- cli
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    def common(p):
        p.add_argument('--product', required=True, help='file prefix, e.g. WheyIsolate (Raw/WheyIsolate-Front.tif)')
        p.add_argument('--views', required=True, help='comma list, e.g. Front,Back')
        p.add_argument('--root', default=default_root(), help='product-images folder holding Raw/, WIP/, Final/ (default: $SWOL_PHOTO_ROOT, else ~/.config/swolverine-product-photos/config.json)'); p.add_argument('--work', help='intermediate files (default ~/Library/Caches/swolverine-product-photos/<product>)')
        p.add_argument('--raw', action='append', help='View=path override for oddly named raws (repeatable)')
    for name in ['preview', 'mask', 'podium-cut', 'render', 'match-light', 'qa', 'export', 'webp']:
        p = sub.add_parser(name); common(p)
        if name == 'mask': p.add_argument('--crop', help='x0,y0,x1,y1 in raw px to speed up Select Subject (default full frame)')
        if name == 'podium-cut':
            p.add_argument('--guide-view', help='cleanest view (no text near the base); processed first and its gap curve guides the others')
            p.add_argument('--gap-row', help='override the detected product/podium contact row (raw px)')
            p.add_argument('--body-cols', action='store_true', help='jar/tub on the riser disc: Select Subject took the whole disc; with --gap-row at the jar base (where the mask widens), trace only under the jar and drop the disc either side')
            p.add_argument('--from-below', action='store_true', help='with --body-cols: trace the base up from the disc top instead of down from the product (bottles with dark contents or print right at the base)')
            p.add_argument('--band-max', type=int, default=8, help='with --from-below: thickest contact shadow (px) to climb through; a darker band is treated as the dark base of glass. Use ~30 for white tubs with raking shadows/dents')
            p.add_argument('--base-shape', help='with --body-cols: depth,tilt of the base ellipse borrowed from a same-size product (warped/dented tub whose own trace is unreliable)')
            p.add_argument('--follow-trace', action='store_true', help='with --body-cols: where the traced rim is above the ellipse (dent/warp lifting off the disc), cut up to the trace')
            p.add_argument('--no-podium', action='store_true', help='products stand on the tabletop sweep, not the podium (group/bundle shots): keep the Select Subject mask as-is. Mixed-height groups otherwise fool the gap finder')
        if name == 'render':
            p.add_argument('--ref-view', help='view whose height sets the scale (default first view)')
            p.add_argument('--gain', default='auto', help='auto (podium match) | none | r,g,b | white:Product:View (anchor on the product whites; for tabletop group/bundle shots)')
            p.add_argument('--scale', help='override the fill-height scale (only with Chance sign-off)')
            p.add_argument('--shadow', default='auto', choices=['auto', 'single', 'group', 'none'], help='contact shadow: auto = group (one shadow per product) for views cut with podium-cut --no-podium, else single')
        if name == 'match-light':
            p.add_argument('--to', required=True, help='approved donor shot as Product:View, e.g. WheyIsolate-Vanilla:Front (must be rendered in its cache)')
            p.add_argument('--ref-view', help='view of this product compared with the donor (default first view); the field is reused for the other views')
            p.add_argument('--smooth', type=float, default=0.08, help='smoothing, fraction of product size (default 0.08: broad light only; below ~0.05 it starts copying the donor'"'"'s creases)')
        if name == 'webp':
            p.add_argument('--final-dir', help='PNG folder (default Final/PNG/[--subdir])'); p.add_argument('--subdir', help='e.g. Bundles: Final/PNG/Bundles/ (WebPs in Final/WebP/Bundles/)'); p.add_argument('--overwrite', action='store_true')
            p.add_argument('--lossless', action='store_true', help='pixel-identical WebP (about 35%% smaller than PNG) instead of the default q90 (about 10-15x smaller)')
        if name == 'export':
            p.add_argument('--final-dir', help='PNG folder (default Final/PNG/[--subdir])'); p.add_argument('--subdir', help='e.g. Bundles for group shots: Final/PNG/Bundles/ + Final/WebP/Bundles/ + WIP/Bundles/ (the layered TIFF)')
            p.add_argument('--wip-dir'); p.add_argument('--overwrite', action='store_true'); p.add_argument('--no-tiff', action='store_true')
    p = sub.add_parser('reshadow', help='refit the contact shadow to a master Chance edited by hand (preview; --apply to swap it in)')
    p.add_argument('--product', help='file prefix, e.g. WheyIsolate'); p.add_argument('--views', help='comma list, e.g. Front,Back')
    p.add_argument('--name', action='append', help='master file name without .tif, e.g. "Energy - Best" (repeatable); also with --product/--views')
    p.add_argument('--root', default=default_root(), help='product-images folder (same default as the other commands)')
    p.add_argument('--subdir', help='e.g. Bundles: WIP/Bundles/ + Final/PNG/Bundles/ + Final/WebP/Bundles/')
    p.add_argument('--wip-dir'); p.add_argument('--final-dir'); p.add_argument('--work', help='previews + Photoshop scratch (default ~/Library/Caches/swolverine-product-photos/_reshadow)')
    p.add_argument('--shadow', default='auto', choices=['auto', 'single', 'group'], help='auto = group (one shadow per product) for masters in WIP/Bundles/, else single')
    p.add_argument('--apply', action='store_true', help='Chance approved the preview: swap the Shadow layer, verify, rewrite PNG + WebP (old master + PNG backed up to WIP/Previous versions/<name>-pre-reshadow)')
    a = ap.parse_args()
    if not a.root:
        sys.exit('No product-images folder set. Pass --root /path/to/folder, or set SWOL_PHOTO_ROOT, or save {"root": "/path/to/folder"} in '
                 '~/.config/swolverine-product-photos/config.json. The folder holds Raw/, WIP/ and Final/.')
    lazy()
    {'preview': cmd_preview, 'mask': cmd_mask, 'podium-cut': cmd_podium_cut, 'render': cmd_render, 'match-light': cmd_match_light, 'qa': cmd_qa, 'export': cmd_export, 'webp': cmd_webp, 'reshadow': cmd_reshadow}[a.cmd](a)

if __name__ == '__main__':
    main()
