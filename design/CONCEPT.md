# Concept: Mash ∞ Loop

## One sentence
**The infinity sign is the instrument: every song is one loop of the ∞, and the crossing point where two
loops touch is the mix.**

This is literally how the app works. A song plays as a circle that the walk can circle forever (in-song
jumps); in chain mode the player plans an exit, and the next song appears as the second loop, touching the
first at the exit beat. The playhead runs through the crossing and the new song becomes the loop you are in.
The name, the mark and the stage are the same drawing.

## Tone
**Hypnotic and precise**: the feeling of a long set at 3 a.m., where nothing ends and every change is
exact. Not a party, not a dashboard.
- **Type:** Unbounded (wide, round counters that read as loops) for the wordmark and the song title, the
  only two display moments; Be Vietnam Pro for every sentence; JetBrains Mono for numbers that tick (BPM,
  Camelot, time, ETA), tabular.
- **Color:** a warm near-black stage (`oklch 0.16 0.01 60`), one emissive light, **amber phosphor**
  (`oklch 0.83 0.13 72`): the playhead, the crossing glow, the primary control, focus. The beat ring keeps
  its data colors (timbre); other songs keep their own hue only where they appear as a destination. Chrome
  is neutral and quiet so the music data is the color.
- **Imagery:** no photos. The only image is the music itself, drawn: beat rings, jump chords, the ∞ path.
- **Motion:** slow, continuous, curved. Things travel along arcs, never in straight 100px fly-ins; light
  breathes with the beat (low amplitude, never flashing); state changes ease over 600-1200ms.

## Where the concept appears (four places and more)
1. **The stage (hero):** a single loop while one song plays; the second loop slides in when an exit is
   planned, rotating so the exit beat faces the crossing; the two form the ∞ during the hand-over.
2. **The mark:** the wordmark "Mash ∞ Loop", whose ∞ is a stroked lemniscate with a small amber dot
   tracing it; the favicon is the same ∞. Hover the mark: the dot runs one fast lap.
3. **Structure:** the session history becomes a **setlist strip** of small linked loops (each song a ring,
   joined by a crossing glyph that shows how it was entered: cut, blend, fade), the ∞ repeated as a chain.
4. **Library:** every track row carries a **mini beat ring** drawn from its own analysis (its timbre colors),
   so the library reads as a shelf of loops; while a track is being analysed its ring draws itself.
5. **Small details:** the loading state is the ∞ being traced; the range sliders' thumbs are small rings;
   text selection and focus glow in amber; the empty stage is two faint loops waiting.

## Swap test
Put a podcast app or a generic music streamer in: the ∞ stage, the crossing exit, the setlist of linked
loops and the per-track beat rings stop meaning anything. They only make sense for a player that loops
songs forever and mixes one into the next at an analysed crossing point.

## The three wow moments
### 1. Hero: the loop fills (first 2 seconds of playback)
- **What:** before playback the stage shows the ∞ as two faint outlined loops with the prompt. Pressing
  play: the right loop dissolves, the left loop moves to the center and its beat segments sweep in
  clockwise from the top (900ms, in timbre colors), the chords draw after it, the light comes up under the
  ring and the playhead starts. On idle load, the wordmark's ∞ draws itself (600ms) and its dot starts.
- **Why it belongs:** the song becomes a loop in front of you; the ∞ is the waiting instrument.
- **Mobile:** identical, the stage is square on phones.
- **Reduced motion:** the ring appears complete in one 160ms fade; the dot stays still on the ∞.
- **Build:** canvas 2D (`viz.js`), eased progress `0 → 1` drives how much of the ring and chords is drawn.

### 2. Story: the crossing (every song change)
- **What:** when the player plans an exit, the current loop rotates (1.2s, in-out) until the exit beat
  faces right, and the destination's ring grows in on the right, touching it at that point: the ∞ forms.
  The route to the exit (the jumps the player will take) is drawn as an amber thread. At the hand-over the
  playhead passes through the crossing; for a blend both loops carry a playhead and the crossing glows with
  the blend progress. Then the new song's loop glides to the center and the old one shrinks away.
- **Why it belongs:** it is the name of the app performed by its own data, at the real moment of the mix.
- **Mobile:** the ∞ stands upright (an 8): the next loop appears below.
- **Reduced motion:** no rotation or glide; the second loop fades in at its place, the swap is a 200ms
  crossfade.
- **Build:** per-ring transform state (center, radius, rotation, opacity) eased toward targets each
  frame; static ring layers rendered once per song and drawn rotated.

### 3. Touch: pick the crossing yourself
- **What:** hovering the ring lights the beat under the pointer and its jump chords, with a caption
  (time, branches). Hovering an exit dot shows where it leads ("Blend khớp nhịp → September · khớp 87%");
  clicking it makes that the planned exit: the ∞ forms toward that song right away.
- **Why it belongs:** you choose where the two loops touch.
- **Mobile:** tap a dot to see its caption, tap the caption to choose it.
- **Reduced motion:** same, without the travel animation.
- **Build:** hit-testing on the canvas, `Player.planTransition(tr)` (new, reuses the route planner).

## Map (one screen app)
- **Top bar:** mark; mode switch (Một bài / Chuỗi) with a sliding indicator; library/settings toggle on
  narrow screens.
- **Stage:** the ∞ canvas, centered caption under it (title in Unbounded, meta in mono), the next-change
  line.
- **Deck (bottom):** play/pause (round, amber), "Nhảy", "Bài khác", volume; listening time, jumps, changes
  in mono; setlist strip.
- **Side panel:** tabs Thư viện / Tinh chỉnh with a sliding indicator. Library: add/search field, results,
  worker line, track shelf with mini rings. Settings: grouped sliders with live values, transition kinds as
  three toggles, relink, shortcuts.
- **States:** empty library, idle stage, loading a song, analysing tracks, errors, stale server.
