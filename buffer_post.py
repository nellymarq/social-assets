#!/usr/bin/env python3
"""Canonical Buffer GraphQL post scheduler with built-in char-limit validation.

WHY: The 2026-06-03 batch hit two over-limit failures (CAL X 299>280, SCY IG
~2400>2196). The Buffer MCP servers are covered by
~/.claude/hooks/buffer-post-length-guard.py — but ad-hoc Python GraphQL helpers
bypass that hook. This module is the ONLY sanctioned ad-hoc GraphQL path; the
hook redirects ad-hoc helpers here.

Supports four lanes (all share length-validation + token auto-resolution):
  - schedule_post     : single image (back-compat)
  - schedule_carousel : 2-10 images (IG carousel / TikTok Photo Mode)
  - schedule_video    : a video asset + thumbnail (IG Reel / TikTok video)
  - X link-in-first-reply: pass reply_url= to any lane on a twitter channel →
    body stays link-free, URL posts as the first reply (zero reach cost).

Asset shapes are schema-verified (2026-06-13 Phase-0 spike); see
reference_buffer_api_playbook.md "Carousel / video / X-reply asset shapes".

USAGE (Python):
    from buffer_post import schedule_post, schedule_carousel, schedule_video
    schedule_carousel(channel_id="69d1f3a0...", due_at="2026-..-..T..-04:00",
        text="...", image_urls=[u1,u2,u3], alt_texts=["a","b","c"],
        metadata={"instagram": {"shouldShareToFeed": True}})  # IG type forced to "post" (carousel enum is rejected; multi-image post auto-renders as a carousel)

USAGE (CLI):
    # single image
    python3 buffer_post.py --channel <id> --due <iso> --text-file <f> --image <url> [--alt <t>] [--meta-json <j>]
    # carousel (comma-separated image URLs; alts pipe-separated)
    python3 buffer_post.py --channel <id> --due <iso> --text-file <f> --images <u1,u2,u3> [--alts "a|b|c"] [--w 1080 --h 1920] [--meta-json <j>]
    # video
    python3 buffer_post.py --channel <id> --due <iso> --text-file <f> --video <url> [--thumbnail <url>] [--video-title <t>] [--meta-json <j>]
    # X link-in-first-reply (any lane, twitter channel)
    ... --reply-url https://calsanova.com/for-athletes

Token sources (auto-detected by channel platform):
    BUFFER_API_TOKEN           — Calsanova org default
    BUFFER_API_TOKEN_SCYTHENE  — Scythene org default

Exits non-zero on Buffer rejection or length violation.
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

BUFFER_GRAPHQL_URL = "https://api.buffer.com/graphql"

# Org IDs (for future list_posts / query calls; createPost only needs channelId).
ORGS = {
    "calsanova": "69d1c0f10f822245c9a6cf75",
    "scythene": "69d1f2811c3d1fa55c0ff9c6",
}

# Single-source-of-truth channel map — keep in sync with the hook.
CHANNELS = {
    # Calsanova
    "69d1ea5b031bfa423ccf11e5": ("instagram", "calsanova"),
    "69d1eaee031bfa423ccf12be": ("twitter", "calsanova"),
    "69d1ef84031bfa423ccf2495": ("tiktok", "calsanova"),
    # Scythene
    "69d1f3a0031bfa423ccf2e82": ("instagram", "scythene"),
    "69d1f504031bfa423ccf3152": ("twitter", "scythene"),
    "69d1f3cb031bfa423ccf2ed2": ("tiktok", "scythene"),
}

LIMITS = {
    "twitter": 280,
    "instagram": 2196,
    "tiktok": 2200,
    "facebook": 63206,
    "linkedin": 3000,
}

# URL boundary excludes trailing punctuation so it isn't absorbed into the
# token (t.co wraps only the real URL; trailing ).,!?;] count as real chars).
URL_RE = re.compile(r"https?://[^\s)\]\.,!?;]+")

# Engagement-bait patterns — IG/TikTok DEMOTE these; X invites block/report.
# High-precision: must NOT match genuine prompts ("drop it in the comments",
# "what's your routine?", "which supplement did you waste money on?",
# "save this", "comment yours", "take a picture of your shelf"). Verified zero-FP
# against the real-caption corpus 2026-06-15 (incl. spelled-number + DM/follow variants).
# KEEP IN SYNC with ~/.claude/hooks/buffer-post-length-guard.py BAIT_PATTERNS.
BAIT_PATTERNS = [
    re.compile(r"\btag\s+(\d+|one|two|three|four|five|a\s+few|several|some|your|a)\s+(\w+\s+)?(friend|people|person|buddies|buddy|pal|mate)", re.I),
    re.compile(r"\btag\s+(someone|somebody)\b", re.I),
    re.compile(r"\btag\s+(a\s+friend|your\s+\w+)\s+who\b", re.I),
    re.compile(r"\b(comment|dm|type|reply|drop)\s+['\"]?(yes|done|info|guide|link|word|me)\b", re.I),
    re.compile(r"\b(comment|dm|type|reply|drop)\b.{0,20}\bfor\s+(the|your|a)?\s*(link|guide|plan|free|code)\b", re.I),
    re.compile(r"\bfollow\s*(for|4)\s*follow\b", re.I),
    re.compile(r"\bf4f\b", re.I),
    re.compile(r"\b(smash|hit|tap)\s+(that\s+)?follow\b", re.I),
    re.compile(r"\bfollow\s+for\s+(more|a\s+chance)\b", re.I),
    re.compile(r"\b(like|share|rt|retweet|double[ -]?tap)\s+if\s+you\b", re.I),
]


def validate_no_bait(text: str) -> None:
    for pat in BAIT_PATTERNS:
        m = pat.search(text)
        if m:
            raise ValueError(
                f"engagement-bait detected: {m.group(0)!r} — IG/TikTok demote "
                f"bait and X invites block/report. Rephrase as a genuine prompt "
                f"(a real question / 'save this') or remove."
            )

CREATE_POST_MUTATION = """
mutation($input: CreatePostInput!) {
  createPost(input: $input) {
    __typename
    ... on PostActionSuccess { post { id status dueAt channelService } }
    ... on NotFoundError { message }
    ... on UnauthorizedError { message }
    ... on UnexpectedError { message }
    ... on InvalidInputError { message }
    ... on LimitReachedError { message }
    ... on RestProxyError { message }
  }
}
"""


def count_chars(platform: str, text: str) -> int:
    if platform == "twitter":
        return len(URL_RE.sub("X" * 23, text))
    return len(text)


def platform_of(channel_id: str) -> str:
    platform, _ = CHANNELS.get(channel_id, ("", ""))
    if not platform:
        raise ValueError(
            f"channel_id {channel_id!r} not in CHANNELS map (known: {list(CHANNELS)})"
        )
    return platform


def validate_length(channel_id: str, text: str) -> None:
    platform = platform_of(channel_id)
    limit = LIMITS.get(platform, 100000)
    count = count_chars(platform, text)
    if count > limit:
        raise ValueError(
            f"text length {count} > {platform} limit {limit} "
            f"(channel {channel_id}). Trim before re-firing."
        )


def resolve_token(channel_id: str) -> str:
    """Pick the right env var based on channel's org."""
    _, org = CHANNELS.get(channel_id, ("", ""))
    if org == "scythene":
        tok = os.environ.get("BUFFER_API_TOKEN_SCYTHENE")
        if not tok:
            raise RuntimeError("BUFFER_API_TOKEN_SCYTHENE not set (Scythene channel)")
        return tok
    tok = os.environ.get("BUFFER_API_TOKEN")
    if not tok:
        raise RuntimeError("BUFFER_API_TOKEN not set (Calsanova channel)")
    return tok


def _image_asset(url: str, alt: str, width: int, height: int) -> dict:
    return {
        "image": {
            "url": url,
            "metadata": {
                "altText": alt or "",
                "dimensions": {"width": width, "height": height},
            },
        }
    }


def _apply_reply(metadata: dict | None, channel_id: str, reply_url: str | None,
                 body_text: str = "") -> dict | None:
    """For a twitter channel, attach reply_url as the first thread reply
    (link-in-first-reply pattern). No-op for non-twitter or no reply_url.

    Enforces the link-free-body rule (body links suppress X reach — the whole
    point is to move the link OUT of the body), validates the reply text for
    length + bait (the thread reply bypasses the top-level text checks otherwise),
    and dedups so the same URL isn't appended twice.
    """
    if not reply_url:
        return metadata
    if platform_of(channel_id) != "twitter":
        # Links on IG/TikTok go in the bio /links page, not a reply.
        return metadata
    if URL_RE.search(body_text or ""):
        raise ValueError(
            "X body contains a URL but reply_url is set — body must be LINK-FREE "
            "(body links suppress X reach; the link goes in the first reply)."
        )
    validate_length(channel_id, reply_url)
    validate_no_bait(reply_url)
    meta = dict(metadata or {})
    tw = dict(meta.get("twitter") or {})
    thread = list(tw.get("thread") or [])
    if not any((e or {}).get("text") == reply_url for e in thread):
        thread.append({"text": reply_url, "assets": []})
    tw["thread"] = thread
    meta["twitter"] = tw
    return meta


def _fire(inp: dict, channel_id: str, token: str | None) -> dict:
    tok = token or resolve_token(channel_id)
    body = json.dumps({"query": CREATE_POST_MUTATION, "variables": {"input": inp}}).encode()
    req = urllib.request.Request(
        BUFFER_GRAPHQL_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {tok}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (buffer_post.py)",
        },
    )
    try:
        resp = urllib.request.urlopen(req, timeout=30).read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Buffer HTTP {e.code}: {e.read().decode()[:400]}")
    out = json.loads(resp)
    if out.get("errors"):
        raise RuntimeError(f"Buffer GraphQL errors: {out['errors']}")
    cp = (out.get("data") or {}).get("createPost") or {}
    if cp.get("__typename") not in (None, "PostActionSuccess"):
        raise RuntimeError(f"Buffer rejected ({cp.get('__typename')}): {cp.get('message')}")
    return cp


def schedule_post(
    *,
    channel_id: str,
    due_at: str,
    text: str,
    image_url: str,
    alt_text: str = "",
    metadata: dict | None = None,
    reply_url: str | None = None,
    token: str | None = None,
    mode: str = "customScheduled",
    scheduling_type: str = "automatic",
    width: int = 1080,
    height: int = 1350,
) -> dict:
    """Single-image post. reply_url → X link-in-first-reply (twitter only)."""
    validate_length(channel_id, text)
    validate_no_bait(text)
    inp: dict = {
        "channelId": channel_id,
        "mode": mode,
        "schedulingType": scheduling_type,
        "dueAt": due_at,
        "text": text,
        "assets": [_image_asset(image_url, alt_text, width, height)],
    }
    meta = _apply_reply(metadata, channel_id, reply_url, text)
    if meta:
        inp["metadata"] = meta
    return _fire(inp, channel_id, token)


def schedule_carousel(
    *,
    channel_id: str,
    due_at: str,
    text: str,
    image_urls: list,
    alt_texts: list | None = None,
    metadata: dict | None = None,
    reply_url: str | None = None,
    token: str | None = None,
    mode: str = "customScheduled",
    scheduling_type: str = "automatic",
    width: int = 1080,
    height: int = 1350,
) -> dict:
    """2-10 image carousel (IG carousel / TikTok Photo Mode).

    On IG, metadata.instagram.type is forced to "post" — Buffer REJECTS the
    "carousel" enum value ("Instagram does not support the 'carousel' post type.
    Valid types are post, story, or reel."); multiple image assets on a "post"
    are auto-rendered as a carousel by IG. (Verified live 2026-06-13.)
    For TikTok, multiple images = Photo Mode (no flag) — verified accepted live
    2026-06-13.
    """
    if not (2 <= len(image_urls) <= 10):
        raise ValueError(f"carousel needs 2-10 images, got {len(image_urls)}")
    validate_length(channel_id, text)
    validate_no_bait(text)
    alts = alt_texts or []
    assets = [
        _image_asset(u, alts[i] if i < len(alts) else "", width, height)
        for i, u in enumerate(image_urls)
    ]
    meta = dict(metadata or {})
    if platform_of(channel_id) == "instagram":
        ig = dict(meta.get("instagram") or {})
        ig["type"] = "post"  # NOT "carousel" — Buffer rejects that; multi-image post = carousel
        ig.setdefault("shouldShareToFeed", True)
        meta["instagram"] = ig
    meta = _apply_reply(meta, channel_id, reply_url, text) or None
    inp: dict = {
        "channelId": channel_id,
        "mode": mode,
        "schedulingType": scheduling_type,
        "dueAt": due_at,
        "text": text,
        "assets": assets,
    }
    if meta:
        inp["metadata"] = meta
    return _fire(inp, channel_id, token)


def schedule_video(
    *,
    channel_id: str,
    due_at: str,
    text: str,
    video_url: str,
    thumbnail_url: str | None = None,
    thumbnail_offset: int | None = None,
    video_title: str = "",
    metadata: dict | None = None,
    reply_url: str | None = None,
    token: str | None = None,
    mode: str = "customScheduled",
    scheduling_type: str = "automatic",
) -> dict:
    """Video post (IG Reel / TikTok video). thumbnail_url strongly recommended.

    On IG, metadata.instagram.type defaults to "reel"; on TikTok the video_title
    is mirrored into metadata.tiktok.title (TikTok's caption/title slot).
    """
    validate_length(channel_id, text)
    validate_no_bait(text)
    video: dict = {"url": video_url}
    if thumbnail_url:
        video["thumbnailUrl"] = thumbnail_url
    vmeta: dict = {}
    if video_title:
        vmeta["title"] = video_title
    if thumbnail_offset is not None:
        vmeta["thumbnailOffset"] = thumbnail_offset
    if vmeta:
        video["metadata"] = vmeta
    plat = platform_of(channel_id)
    meta = dict(metadata or {})
    if plat == "instagram":
        ig = dict(meta.get("instagram") or {})
        ig.setdefault("type", "reel")
        ig.setdefault("shouldShareToFeed", True)
        meta["instagram"] = ig
    elif plat == "tiktok" and video_title:
        tt = dict(meta.get("tiktok") or {})
        tt.setdefault("title", video_title)
        meta["tiktok"] = tt
    meta = _apply_reply(meta, channel_id, reply_url, text) or None
    inp: dict = {
        "channelId": channel_id,
        "mode": mode,
        "schedulingType": scheduling_type,
        "dueAt": due_at,
        "text": text,
        "assets": [{"video": video}],
    }
    if meta:
        inp["metadata"] = meta
    return _fire(inp, channel_id, token)


def _cli() -> None:
    p = argparse.ArgumentParser(description="Schedule a Buffer post (image/carousel/video).")
    p.add_argument("--channel", required=True, help="Buffer channelId")
    p.add_argument("--due", required=True, help="ISO 8601 dueAt")
    p.add_argument("--text-file", required=True, help="Path to caption text file")
    p.add_argument("--image", help="Single image URL")
    p.add_argument("--images", help="Comma-separated image URLs for a carousel")
    p.add_argument("--video", help="Video URL")
    p.add_argument("--thumbnail", help="Video thumbnail URL")
    p.add_argument("--video-title", default="", help="Video title")
    p.add_argument("--alt", default="", help="Alt text (single image)")
    p.add_argument("--alts", default="", help="Pipe-separated alt texts (carousel)")
    p.add_argument("--reply-url", default="", help="X link-in-first-reply URL (twitter only)")
    p.add_argument("--w", type=int, default=1080, help="Image width (default 1080)")
    p.add_argument("--h", type=int, default=1350, help="Image height (default 1350; use 1920 for 9:16)")
    p.add_argument("--meta-json", default="", help='JSON metadata, e.g. {"instagram":{"shouldShareToFeed":true}}')
    args = p.parse_args()

    with open(args.text_file, "r") as f:
        text = f.read()
    meta = json.loads(args.meta_json) if args.meta_json else None
    reply = args.reply_url or None

    try:
        if args.video:
            out = schedule_video(
                channel_id=args.channel, due_at=args.due, text=text,
                video_url=args.video, thumbnail_url=args.thumbnail or None,
                video_title=args.video_title, metadata=meta, reply_url=reply,
            )
        elif args.images:
            urls = [u.strip() for u in args.images.split(",") if u.strip()]
            alts = [a.strip() for a in args.alts.split("|")] if args.alts else None
            out = schedule_carousel(
                channel_id=args.channel, due_at=args.due, text=text,
                image_urls=urls, alt_texts=alts, metadata=meta, reply_url=reply,
                width=args.w, height=args.h,
            )
        elif args.image:
            out = schedule_post(
                channel_id=args.channel, due_at=args.due, text=text,
                image_url=args.image, alt_text=args.alt, metadata=meta,
                reply_url=reply, width=args.w, height=args.h,
            )
        else:
            print("FAIL: one of --image / --images / --video required", file=sys.stderr)
            sys.exit(2)
        post = out.get("post", {})
        print(f"OK id={post.get('id')} status={post.get('status')} due={post.get('dueAt')}")
    except (ValueError, RuntimeError) as e:
        print(f"FAIL: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _cli()
