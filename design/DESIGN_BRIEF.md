# Design brief: Mash ∞ Loop

Mode: **redesign** of the existing local web app (`web/`), plus a rebrand from "Loopbox" to
**Mash ∞ Loop** (the infinity sign sits between the two words).

## The product
A personal, local-only player: songs are downloaded from YouTube, analysed (beats, bars, harmony, stems,
singer), and played forever. It jumps between similar moments inside one song and, in chain mode, hands
over from song to song through analysed transitions (cut, beat-matched blend, crossfade).

## Content and function inventory (everything that exists must survive)

Header / transport
- Brand mark and name.
- Play / pause, "Nhảy" (jump now), "Bài khác" (next song now). Keyboard: Space, J, N.
- Mode: "Một bài" (loop one song) / "Chuỗi" (chain the library).

Stage
- Circle of beats, coloured by timbre; chords for in-song jumps (bright = usable at the current
  strictness, faint = filtered); ticks at section starts; dots outside the ring for exits to other songs
  (colour per destination); pulsing ring on the planned exit; playhead; white flash along a jump.
- Centre text: title, BPM · Camelot · key, listened time, jump and transition counts, blend progress.
- Idle state: "Chọn một bài trong thư viện để bắt đầu".

Now bar
- Next transition: kind (nối thẳng / blend khớp nhịp / crossfade), destination, ETA, match %, loading;
  or "Có thể chuyển bài sau …" / "Đang chờ điểm nối tốt gần nhất…" / "Lặp mãi bài này".
- History of the last 8 songs with how each was entered (start, cut, blend, fade).

Library panel
- Add: one field for a song name, a YouTube link or a playlist link; "Tìm" searches (8 results with
  thumbnail, title, channel, duration, "Thêm" / "Đã có"), a URL is added directly.
- Worker line: current job and stage, queue length, relink progress, stale-server warning.
- Track rows: colour swatch, title, BPM · Camelot · branch count · duration, or stage + progress bar,
  or the error; actions play, include/exclude from chain, retry (on error), delete (with confirm).
- Playing row highlighted; excluded rows dimmed; empty library message.

Settings panel
- Jump frequency (~N giây một lần), strictness (giữ N% điểm tốt nhất), minimum stay, latest change,
  chain variety, transition kinds (cut / blend / fade), volume, "Tính lại điểm nối giữa các bài",
  keyboard hint.

## Audience and use
One person on this machine, a desktop browser (Chrome/Edge), often left running for hours as background
music, glanced at rather than read. Vietnamese UI.

## Assumptions (no questions asked: the brief and the existing app answer them)
- Dark theme only: the app runs for hours, often in a dim room; the visualization needs a dark stage.
- Desktop first (1280-1920), but every width from 375 must be composed; on phones the panels stack.
- Rebrand covers the name, mark, favicon, page title and README; no domain, no social preview needed for
  a local app (an `og:image` is still provided for completeness).
- The engine (`engine.js`, the analysis) is untouched; small additions to `player.js` are allowed where a
  touch moment needs them (choosing an exit by hand).
- Fonts load from Google Fonts (the app needs the internet for YouTube anyway), with system fallbacks.
