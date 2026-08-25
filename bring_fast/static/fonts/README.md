# Fonts

IBM Plex Mono, served by Bring Fast itself so no page waits on a font CDN and an
offline launch looks like the app.

- `ibm-plex-mono-<weight>-<subset>.woff2` — the latin and latin-ext subsets of
  weights 400, 500, 600 and 700, taken from the Google Fonts build of IBM Plex
  Mono v20.
- A filename names exactly one set of bytes, which is why `/static/fonts/` is
  served `immutable` for a year. Replacing a subset means a new filename, in
  `bring_fast/templates/_fonts.html` and in the service worker precache list.

Licensed under the SIL Open Font License 1.1 — see `LICENSE.txt`.
Copyright © 2017 IBM Corp. with Reserved Font Name "Plex".
