#!/usr/bin/env python3
"""Scythene product-post generator — real Supliful product photo + pixel-on-brand
HTML render (navy-dominant, real logo, FDA disclaimer baked in). The brand-exact
alternative to Canva's loose generate-design (verified 2026-06-29).

Renders at 4:5 (IG) or 9:16 (TikTok Photo Mode). One product photo + a short spec.

Spec JSON:
{
  "photo": "/home/nelly/scythene/public/images/products/creatine-monohydrate.jpg",
  "name": "CREATINE MONOHYDRATE",      # use \n for a line break
  "claim_lead": "5 g per serving.",     # green emphasis lead
  "claim": "The most studied supplement in sports nutrition — saturates in 3-4 weeks, no loading needed.",
  "pills": ["5g dose", "No loading", "50 servings"],
  "kicker": "Evidence-based supplements",   # optional (default)
  "badge": "In stock"                        # optional (default)
}
FDA disclaimer is auto-included (override with "disclaimer": "...").

USAGE:
  python3 build_product_post.py --spec creatine.json --out-prefix /tmp/scy-creatine-prod --ratio 4:5
  # -> <prefix>.png
"""
from __future__ import annotations
import argparse, html, json, os, shutil, subprocess, sys

RENDER_DIR = "/home/nelly/social-render"
RENDER_JS = os.path.join(RENDER_DIR, "render.js")
RATIOS = {"4:5": (1080, 1350), "9:16": (1080, 1920)}
FDA = ("*This statement has not been evaluated by the Food and Drug Administration. "
       "This product is not intended to diagnose, treat, cure, or prevent any disease.")

TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;700&display=swap" rel="stylesheet">
<style>
 *{{margin:0;box-sizing:border-box}}
 body{{width:{W}px;height:{H}px;background:#0d1a2c;color:#fff;font-family:'Space Grotesk',sans-serif;
   position:relative;overflow:hidden;display:flex;flex-direction:column;padding:64px 72px;}}
 .top{{display:flex;justify-content:space-between;align-items:center;font-family:'IBM Plex Mono',monospace;
   font-size:18px;letter-spacing:0.28em;text-transform:uppercase;}}
 .top .k{{color:#00D26A;}}
 .top .n{{color:#0d1a2c;background:#00D26A;font-weight:700;padding:6px 14px;border-radius:4px;letter-spacing:0.2em;}}
 .card{{margin:30px 0 0;background:#f3f5f7;border-radius:28px;height:{CARD}px;
   display:flex;align-items:center;justify-content:center;overflow:hidden;flex:0 0 auto;}}
 .card img{{height:96%;object-fit:contain;}}
 .name{{margin-top:38px;font-weight:700;font-size:82px;line-height:0.98;letter-spacing:-0.02em;}}
 .claim{{margin-top:18px;font-size:30px;line-height:1.35;color:rgba(255,255,255,0.82);font-weight:400;}}
 .claim b{{color:#00D26A;font-weight:600;}}
 .pills{{margin-top:30px;display:flex;gap:14px;flex-wrap:wrap;}}
 .pill{{border:1.5px solid #00D26A;color:#00D26A;border-radius:999px;font-family:'IBM Plex Mono',monospace;
   font-size:20px;font-weight:500;letter-spacing:0.08em;padding:12px 22px;text-transform:uppercase;}}
 .foot{{margin-top:auto;display:flex;justify-content:space-between;align-items:flex-end;gap:30px;}}
 .foot .logo{{height:46px;flex:0 0 auto;}}
 .foot .logo img{{height:100%;display:block;}}
 .foot .disc{{font-family:'IBM Plex Mono',monospace;font-size:13px;line-height:1.45;
   color:rgba(255,255,255,0.4);max-width:560px;text-align:right;}}
</style></head>
<body>
 <div class="top"><span class="k">{KICKER}</span><span class="n">{BADGE}</span></div>
 <div class="card"><img src="{IMG}" alt="{ALT}"></div>
 <div class="name">{NAME}</div>
 <div class="claim"><b>{CLAIM_LEAD}</b> {CLAIM}</div>
 <div class="pills">{PILLS}</div>
 <div class="foot"><div class="logo"><img src="scy-logo.png" alt="Scythene"></div><div class="disc">{DISC}</div></div>
</body></html>"""


def esc(s): return html.escape(str(s))


def build(spec, out_prefix, ratio):
    if ratio not in RATIOS:
        raise SystemExit(f"ratio must be one of {list(RATIOS)}")
    for k in ("photo", "name", "claim"):
        if not spec.get(k):
            raise SystemExit(f"spec.{k} is required")
    src = spec["photo"]
    if not os.path.isfile(src):
        raise SystemExit(f"photo not found: {src}")
    w, h = RATIOS[ratio]
    card = 600 if ratio == "4:5" else 760  # a touch taller on 9:16 to balance
    # stage the product photo into the render dir so render.js (file://) can load it
    img_name = "_product_" + os.path.basename(src).replace(" ", "_")
    shutil.copyfile(src, os.path.join(RENDER_DIR, img_name))
    pills = "".join(f'<span class="pill">{esc(p)}</span>' for p in spec.get("pills", []))
    name_html = "<br>".join(esc(line) for line in spec["name"].split("\n"))
    doc = TEMPLATE.format(
        W=w, H=h, CARD=card,
        KICKER=esc(spec.get("kicker", "Evidence-based supplements")),
        BADGE=esc(spec.get("badge", "In stock")),
        IMG=img_name, ALT=esc(spec.get("name", "Scythene product").replace("\n", " ")),
        NAME=name_html,
        CLAIM_LEAD=esc(spec.get("claim_lead", "")),
        CLAIM=esc(spec["claim"]),
        PILLS=pills,
        DISC=esc(spec.get("disclaimer", FDA)),
    )
    tmp = os.path.join(RENDER_DIR, f"_product_tmp_{os.getpid()}.html")
    out = f"{out_prefix}.png"
    with open(tmp, "w") as f:
        f.write(doc)
    r = subprocess.run(["node", RENDER_JS, tmp, out, str(w), str(h)], capture_output=True, text=True)
    os.remove(tmp)
    os.remove(os.path.join(RENDER_DIR, img_name))
    if r.returncode != 0:
        raise SystemExit(f"render failed: {r.stderr}")
    print(r.stdout.strip())
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--spec", required=True)
    p.add_argument("--out-prefix", required=True)
    p.add_argument("--ratio", default="4:5")
    a = p.parse_args()
    with open(a.spec) as f:
        spec = json.load(f)
    build(spec, a.out_prefix, a.ratio)


if __name__ == "__main__":
    main()
