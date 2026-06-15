#!/usr/bin/env python3
"""Carousel slide-set generator for Calsanova + Scythene.

Renders a JSON spec into N brand-styled slide PNGs ready for
buffer_post.schedule_carousel(). Slides are flexbox-centered so the SAME
template renders cleanly at 1080x1350 (4:5 IG carousel) AND 1080x1920
(9:16 TikTok Photo Mode).

Spec JSON shape:
{
  "topic_slug": "cortisol-drift",
  "issue": "019",                      # FIELD NOTES / REPORT number
  "cover":  {"hook": "Cortisol drift is the tell most lifters miss.",
             "emphasis": "tell"},      # optional word rendered in accent color
  "slides": [                           # 3-8 body slides
     {"label": "THE SIGNAL", "head": "Week 8, the scale stalls.",
      "body": "Sleep degrades, RPE creeps, morning bodyweight noise widens."},
     ...
  ],
  "cta": {"payoff": "Read the body before you change the macros.",
          "action": "Save this for your next cut."}   # action must be bait-free
}

USAGE:
  python3 build_carousel.py --brand calsanova --spec spec.json \
      --out-prefix /home/nelly/social-render/cal-cortisol-car --ratio 4:5
  # → <prefix>-1.png ... <prefix>-N.png  (cover, slides..., cta)

Then schedule with buffer_post.schedule_carousel(image_urls=[...those, hosted...]).
"""
from __future__ import annotations
import argparse
import html
import json
import os
import subprocess
import sys

RENDER_DIR = "/home/nelly/social-render"
RENDER_JS = os.path.join(RENDER_DIR, "render.js")

RATIOS = {"4:5": (1080, 1350), "9:16": (1080, 1920)}

FONTS = {
    "calsanova": '<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,700;0,900;1,400;1,900&family=Inter:wght@400;600;700;800;900&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">',
    "scythene": '<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;700&family=Inter:wght@400;700;900&display=swap" rel="stylesheet">',
}

BRAND = {
    "calsanova": {
        "bg": "#0a1628", "accent": "#4ECDC4", "fg": "#ffffff",
        "muted": "rgba(255,255,255,0.55)", "logo": "cal-logo.png",
        "issue_label": "FIELD NOTES", "org": "CALSANOVA RESEARCH",
        "mono": "'JetBrains Mono',monospace", "sans": "'Inter',sans-serif",
        "serif": "'Playfair Display',serif",
    },
    "scythene": {
        "bg": "#0d1a2c", "accent": "#00D26A", "fg": "#ffffff",
        "muted": "rgba(255,255,255,0.55)", "logo": "scy-logo.png",
        "issue_label": "REPORT", "org": "SCYTHENE",
        "mono": "'IBM Plex Mono',monospace", "sans": "'Inter',sans-serif",
        "serif": "'Space Grotesk',sans-serif",
    },
}


def _shell(b: dict) -> str:
    """Common <head> + body open with brand vars; flwhere centering handles both ratios."""
    return f"""<!doctype html><html><head><meta charset="utf-8">{FONTS_LOOKUP}
<style>
*{{margin:0;box-sizing:border-box}}
body{{width:{{W}}px;height:{{H}}px;background:{b['bg']};color:{b['fg']};
  font-family:{b['sans']};position:relative;overflow:hidden;
  display:flex;flex-direction:column;justify-content:center;
  padding:120px 80px;}}
.topbar{{position:absolute;top:56px;left:80px;right:80px;display:flex;
  justify-content:space-between;font-family:{b['mono']};font-size:18px;
  letter-spacing:0.22em;color:{b['muted']};text-transform:uppercase;}}
.accent{{color:{b['accent']}}}
.logo{{position:absolute;bottom:50px;left:80px;height:50px;}}
.logo img{{height:100%;display:block}}
.counter{{position:absolute;bottom:58px;right:80px;font-family:{b['mono']};
  font-size:18px;letter-spacing:0.2em;color:{b['muted']}}}
.swipe{{position:absolute;bottom:56px;right:80px;font-family:{b['mono']};
  font-size:20px;letter-spacing:0.15em;color:{b['accent']};text-transform:uppercase}}
</style></head><body>"""


def cover_html(b, brand, issue, hook, emphasis):
    h = html.escape(hook)
    if emphasis and emphasis in hook:
        esc = html.escape(emphasis)
        h = h.replace(esc, f'<span class="accent">{esc}</span>', 1)
    big = f"font-family:{b['sans']};font-weight:900;font-size:96px;line-height:1.02;letter-spacing:-0.03em"
    return _shell(b) + f"""
<div class="topbar"><span>{b['issue_label']} / №{html.escape(issue)}</span><span>{b['org']}</span></div>
<div style="{big}">{h}</div>
<div class="logo"><img src="{b['logo']}"></div>
<div class="swipe">swipe →</div>
</body></html>"""


def body_html(b, brand, label, head, body, n, total):
    label_s = f"font-family:{b['mono']};font-size:20px;letter-spacing:0.2em;color:{b['accent']};text-transform:uppercase;margin-bottom:22px"
    head_s = f"font-family:{b['serif']};font-weight:900;font-size:78px;line-height:1.05;letter-spacing:-0.02em;color:{b['fg']}" if brand == "calsanova" else f"font-family:{b['serif']};font-weight:700;font-size:74px;line-height:1.04;letter-spacing:-0.02em;color:{b['fg']}"
    body_s = f"font-family:{b['sans']};font-weight:400;font-size:34px;line-height:1.4;color:rgba(255,255,255,0.8);margin-top:30px"
    parts = [f'<div style="{label_s}">{html.escape(label)}</div>'] if label else []
    parts.append(f'<div style="{head_s}">{html.escape(head)}</div>')
    if body:
        parts.append(f'<div style="{body_s}">{html.escape(body)}</div>')
    return _shell(b) + f"""
<div class="topbar"><span>{b['issue_label']} / №{html.escape(ISSUE_HOLDER)}</span><span>{b['org']}</span></div>
{''.join(parts)}
<div class="logo"><img src="{b['logo']}"></div>
<div class="counter">{n} / {total}</div>
</body></html>"""


def cta_html(b, brand, payoff, action):
    pay_s = f"font-family:{b['serif']};font-weight:900;font-size:72px;line-height:1.08;letter-spacing:-0.02em;color:{b['accent']}" if brand == "calsanova" else f"font-family:{b['serif']};font-weight:700;font-size:68px;line-height:1.06;letter-spacing:-0.02em;color:{b['accent']}"
    act_s = f"font-family:{b['sans']};font-weight:600;font-size:34px;line-height:1.4;color:{b['fg']};margin-top:40px"
    return _shell(b) + f"""
<div class="topbar"><span>{b['issue_label']} / №{html.escape(ISSUE_HOLDER)}</span><span>{b['org']}</span></div>
<div style="{pay_s}">{html.escape(payoff)}</div>
<div style="{act_s}">{html.escape(action)}</div>
<div class="logo"><img src="{b['logo']}"></div>
</body></html>"""


# module-level holders set per-run (keeps _shell signature simple)
FONTS_LOOKUP = ""
ISSUE_HOLDER = ""


def build(brand, spec, out_prefix, ratio):
    global FONTS_LOOKUP, ISSUE_HOLDER
    if brand not in BRAND:
        raise SystemExit(f"unknown brand {brand}")
    if ratio not in RATIOS:
        raise SystemExit(f"ratio must be one of {list(RATIOS)}")
    w, h = RATIOS[ratio]
    b = BRAND[brand]
    FONTS_LOOKUP = FONTS[brand]
    ISSUE_HOLDER = spec.get("issue", "")
    slides = spec.get("slides", [])
    # Validate spec shape upfront — clear errors instead of raw KeyError mid-render.
    for key in ("cover", "cta"):
        if not isinstance(spec.get(key), dict):
            raise SystemExit(f"spec missing '{key}' object")
    if not spec["cover"].get("hook"):
        raise SystemExit("spec.cover.hook is required")
    if not spec["cta"].get("payoff"):
        raise SystemExit("spec.cta.payoff is required")
    for i, s in enumerate(slides):
        if not isinstance(s, dict) or not s.get("head"):
            raise SystemExit(f"spec.slides[{i}] must be an object with a 'head'")
    if not (3 <= len(slides) + 2 <= 10):
        raise SystemExit(f"total slides (cover+{len(slides)}+cta) must be 3-10, got {len(slides)+2}")
    total = len(slides) + 2
    if total < 6:
        print(f"WARN: {total} total slides — 6-10 is optimal for IG saves.", file=sys.stderr)

    htmls = []
    htmls.append(cover_html(b, brand, spec.get("issue", ""), spec["cover"]["hook"], spec["cover"].get("emphasis", "")))
    for i, s in enumerate(slides, start=2):
        htmls.append(body_html(b, brand, s.get("label", ""), s["head"], s.get("body", ""), i, total))
    htmls.append(cta_html(b, brand, spec["cta"]["payoff"], spec["cta"].get("action", "")))

    outputs = []
    for idx, doc in enumerate(htmls, start=1):
        doc = doc.replace("{W}", str(w)).replace("{H}", str(h))
        tmp = os.path.join(RENDER_DIR, f"_carousel_tmp_{os.getpid()}_{idx}.html")
        out = f"{out_prefix}-{idx}.png"
        with open(tmp, "w") as f:
            f.write(doc)
        r = subprocess.run(["node", RENDER_JS, tmp, out, str(w), str(h)],
                           capture_output=True, text=True)
        os.remove(tmp)
        if r.returncode != 0:
            raise SystemExit(f"render failed slide {idx}: {r.stderr}")
        outputs.append(out)
        print(r.stdout.strip())
    print("CAROUSEL:", json.dumps(outputs))
    return outputs


def main():
    p = argparse.ArgumentParser(description="Build a carousel slide-set PNG sequence.")
    p.add_argument("--brand", required=True)
    p.add_argument("--spec", required=True, help="Path to spec JSON")
    p.add_argument("--out-prefix", required=True)
    p.add_argument("--ratio", default="4:5", help="4:5 (IG) or 9:16 (TikTok Photo Mode)")
    args = p.parse_args()
    with open(args.spec) as f:
        spec = json.load(f)
    build(args.brand, spec, args.out_prefix, args.ratio)


if __name__ == "__main__":
    main()
