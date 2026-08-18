# Instagram-first social resolver strategy

## Decision order

1. **Authentication boundary**: use login state only inside an authorized discovery UI; never pass it to media transfer.
2. **yt-dlp without cookies**: primary resolver/downloader for direct post/video URLs.
3. **Platform-aware discovery**: use anonymous Instaloader for bounded Instagram Reel URLs;
   use yt-dlp collections only when the installed extractor explicitly supports them.
4. **Known canonical URL or info JSON**: reuse it without revisiting the profile.
5. **Discovery adapter**: bounded URL discovery through browser control, Computer Use,
   authorized CDP/Playwright, Kimi WebBridge, another local extension bridge, or manual export.
6. **Normalize and hand off**: convert adapter output to canonical URL lines before logged-out yt-dlp.
7. **Public resolver website**: optional single-item comparison only; do not depend on undocumented endpoints.
8. **Stop**: rate limits, checkpoints, login challenges, repeated failures, or private content.

## Platform priority

- Instagram: use yt-dlp for direct Reel/post URLs. Enumerate profiles with anonymous
  Instaloader or a bounded discovery adapter; stop at the first known shortcode during updates.
- TikTok: secondary. Let yt-dlp distinguish a `/video/` URL from an `@creator` profile.
- YouTube: secondary. Use native video, Shorts, playlist, handle, channel, and live extractors.
- Other platforms: accept public HTTP(S) URLs and let yt-dlp select a supported extractor; do not add platform-specific code until a verified safety need exists.

## iGram pattern worth reusing

Public iGram frontend code separates resolution from transfer:

- `/api/instagram?url=...` resolves a post into media records.
- `/api/download?url=...` handles the final transfer and filename.
- The browser does not transcode the media.

Reuse the architecture, not the undocumented service endpoint. yt-dlp already provides info JSON, direct-media transfer, download archives, and extractor updates; prefer those native features over custom resolver code.

Public descriptions:

- https://igram.site/about
- https://igram.site/faq
- https://igram.site/blog/instagram-download-without-watermark

## Verified comparison (2026-07-20)

One public Reel was downloaded through three methods:

| Method | Resolver time | Transfer/total time | Login state | Result |
|---|---:|---:|---|---|
| Logged-in browser bridge | page-dependent | page-dependent | required | valid MP4 |
| iGram public workflow | 10.10 s | 3.46 s transfer | none | byte-identical MP4 |
| yt-dlp 2026.07.04 | included | 7.15 s total | none | byte-identical MP4 |

All three produced the same 2,027,694-byte SHA-256 result with H.264 video, AAC audio, 720×1280 resolution, and 10.110708-second duration. Treat these measurements as a point-in-time observation, not a permanent service guarantee.

## Risk controls

- Normalize and deduplicate URLs before network access.
- Keep a download archive and per-item state.
- Set yt-dlp request, fragment, extractor, and file-access retries to zero.
- Abort the queue on the first error; do not add an outer automatic retry loop.
- Apply native request pacing and a randomized 12–18 second profile-download delay.
- Open the circuit immediately for `429`, checkpoint, challenge, or login-required signals.
- Persist a 15-minute per-source cooldown for transient TLS, timeout, or connection failures.
- Open the circuit after three consecutive other failures.
- Never loop browser reloads as a recovery mechanism.
- Keep adapter output portable: persist canonical post URLs/media IDs, not temporary CDN URLs.
- Keep extension bridges on loopback and never log Cookie, localStorage, authorization headers, or credentials.
- Never solve a public transfer failure by exporting or injecting a logged-in browser session.
- Cache canonical post URLs and compare media IDs locally before any repeat profile access.

## Upgrade playbook

1. Run `doctor --check-updates` without mutation.
2. Review the latest stable yt-dlp release and current executable source.
3. Apply an update only outside an active batch and only after user approval.
4. Run unit tests.
5. Resolve and download one public test item with default limits.
6. Compare size, codecs, duration, and (when the same representation is expected) SHA-256.
7. If the extractor still fails, stop. Do not compensate with rapid refreshes, cookie harvesting, proxy rotation, or undocumented API guessing.
8. Patch the resolver/fallback instructions only after a working method is verified.
