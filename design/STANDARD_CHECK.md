# Standard check: Mash ∞ Loop

Evidence: `.qa/` (QA suite run against the live app, 2026-10-15), `.scratch/shots/` (states at 1440, 1280,
390), `.scratch/moments/` and the contact sheets `.scratch/sheet-hero-touch.jpg`, `.scratch/sheet-story.jpg`.
These are local working artifacts and are not committed; rerun the QA suite to regenerate them.

| Quality | Pass | Evidence | Fixed on the way |
|---|---|---|---|
| **Aesthetic value** | yes | `CONCEPT.md`: one concept (each song is a loop of the ∞, the crossing is the mix) appearing in the stage, the mark, the setlist of linked loops, the library's mini rings, the loading glyph and the slider thumbs. Swap test fails for any other product. | — |
| **Aesthetic** | yes | `fold-1920/1440/1280/1024/768/375.png`, `interact-playing*.png`: one focal loop per screen, warm single-hue ground, one amber light, Unbounded only for the mark and the song, mono for numbers. | Long YouTube titles made the display line three lines deep: titles are now split into song (display) and artist (text line). |
| **Premium** | yes | Stippled dust ring (Byotōne technique), dial with minute labels, hairline segmented controls with a sliding amber fill (Austensor technique), floating dashed deck (Aether), selection colour, amber focus glow, favicon ∞, `og.png`, designed empty, loading, analysing, error and stale-server states. | Tracked capitals on long Vietnamese labels (E3) replaced by sentence case. |
| **Polished** | yes | `detail-*.png`, `interact-*.png` (hover, pressed, focus, tabs, search focus with a Vietnamese test line, track hover), `fold-1280-reduced.png`. Lighthouse a11y 100. | Contrast of the caption over the chords: a dark pool under the ring centre. Active segment label measured 1.18:1 (indicator invisible to the checker): the chosen option now has its own amber fill once the indicator lands. Keyboard exits list now hidden the standard way (1px clip) until focused. |
| **Complete** | yes | Every function from the inventory in `DESIGN_BRIEF.md` is present and wired; QA crawl: no `#` links, title, description, og:image, favicon, one h1; `tools/e2e_test.py` PASS (0 page errors, no dropouts), `tools/sim_player.mjs` 0 problems. | `og.png` was referenced before it existed: generated from the ∞ state. |
| **Wow** | yes | Hero (`hero-*.png`): the loop sweeps in clockwise in 1.2 s. Story (`story-form-*`, `story-cross-*`): the loop turns its exit toward the next song, the second loop grows in and touches it, the crossing glows with blend progress, the new loop glides to the centre and the setlist links it with an ∞ glyph. Touch (`touch-hover.png`, `touch-picked.png`): hover an exit to see where it leads, click to make it the planned crossing. Mobile: the ∞ stands as an 8 (`infinity-390.png`), tap twice to pick. Reduced motion: no sweep, turn, glide or breathing; 160 ms fades. | The pick confirmation was covered by the beat caption of the ring turning under the pointer: the confirmation now wins for 1.8 s. |

## Numbers
- Lighthouse desktop 95 / 100 / 100 / 100, mobile 98 / 100 / 100 / 100; mobile LCP 2.0 s, CLS 0.021, TBT 0 ms.
- Scroll and animation: 60 fps in headless capture, 0 long frames.
- Fonts: Unbounded 500/600, Be Vietnam Pro 400/500/600, JetBrains Mono 400/500, each verified for all 134
  Vietnamese letters (`font_check.mjs`).

## Not done / honest limits
- The two-loop layout is judged from captures and headless playback, not by a person watching a long
  session; tuning of motion speeds (rotation 0.3 s time constant, glide 0.22 s) may want a listen-and-watch
  pass by the user.
- S15 note: three colour literals live in data-URI SVGs (setlist glyphs) where custom properties cannot reach.
