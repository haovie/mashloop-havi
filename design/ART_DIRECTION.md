# Art direction: Mash ∞ Loop

**Type.** Unbounded 500/600 (display: the wordmark and the playing song's title, nothing else), Be Vietnam
Pro 400/500/600 (all UI text and sentences), JetBrains Mono 400/500 (numbers, BPM, keys, time, ETA,
corner labels; tabular). All three verified for every Vietnamese glyph (`font_check.mjs`, 134/134).
Scale: title clamp(22px, 2.6vw, 44px) at line-height 1.15, tracking -0.02em; body 14-15px / 1.55; labels
11-12px mono, tracked +0.08em, Latin uppercase only (Vietnamese labels stay sentence case, E3).

**Color.** One emissive light, and it is the colour of the song that is playing (`web/js/theme.js`).
Each song gets a colour from its mood: energy and valence from the analysis, spread over the library by
rank, mapped around a hue wheel (content green, joyful amber, excited orange, tense red, gloomy violet,
sad blue, calm teal) at L 0.79. That colour is `--accent` (`--acc-l/c/h`, registered and animated over
1.4 s): playhead, crossing glow, play button, slider fill, active states, focus, selection. During a
mix it passes into the next song's colour (`--accent-2`) with the mix's progress. Ground `--ink-0`
oklch(0.16 · `--gh`) carries the song's hue at chroma ~0.016; surfaces step up in lightness only. Each
loop's beats keep their timbre colouring, folded into ±40° of the song's hue, so a loop is one colour
world and the ∞ is two. Presets: *Theo mood* (default), *Neon* (chroma 0.24, night-blue ground),
*Hổ phách* (the first design: fixed amber oklch(0.83 0.13 72), rainbow timbre rings). Text stays near
white with a trace of the ground hue (≥ 4.5:1 on `--ink-2`). Error oklch(0.74 0.14 30), used for one
line of text, never a fill.

**Composition.** Desktop: a stage that owns the screen (left, fluid) and one panel (right, 380px). The
stage's focal point is the loop; its title sits inside the ring; corner readouts in mono frame it like an
instrument; the deck floats as one centered pill at the bottom of the stage; the setlist strip runs under
it. Phone: stage square on top, the deck sticky at the bottom of the screen, panel below with its tabs.

**Imagery.** None but the music: the beat ring with a stippled halo (Byotōne's dust, made from the
song's own loudness), jump chords, the ∞ path, mini rings in the library.

**Crafted details (first screen).**
1. The ∞ in the wordmark is a stroked lemniscate with an amber dot tracing it: the mark is the concept.
2. A time dial outside the ring: hairline ticks every 15 s, mono minute labels, like a turntable strobe
   scale, so the loop reads as an instrument you can read.
3. The setlist strip: songs as small linked rings, joined by a glyph for how each was entered.
4. Range thumbs are small rings with a dark hole (a loop), the fill is amber.
5. Corner readouts with an amber first token ("ĐANG PHÁT // …").

**Motion language.** Curves and light, never fly-ins. Entrances `cubic-bezier(0.22, 1, 0.36, 1)`,
travels `cubic-bezier(0.65, 0, 0.35, 1)`; 160 / 240 / 420 / 700 / 1200ms. The stage eases continuously
(critically damped, ~1s settle) toward its targets: rotation, glide, scale. The light breathes with the
beat at low amplitude (never a flash; well under 3 changes of luminance per second that read as flashes).
Indicators slide (mode switch, tabs). Buttons press to 0.97 in 80ms. Reduced motion: no rotation, glide,
breathing or tracing; state changes are 160ms fades.

**The mark.** "Mash" + lemniscate + "Loop" in Unbounded 600; the ∞ is an SVG path (stroke 2.2 at 20px
height) so it stays crisp at 16px as the favicon (amber ∞ on the warm black, rounded square).

**Radius and lines.** One radius 14px for panels and fields, 999px only for round controls (pill, play,
chips). Hairlines 1px at `--line` oklch(0.34 0.01 60 / 0.7). One shadow: the amber glow on the play button
and the playhead.
