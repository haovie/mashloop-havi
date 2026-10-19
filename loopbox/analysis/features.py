"""Beat-synchronous features. Every beat is described by what a listener would
notice changing at a splice: harmony (chroma), tone colour (MFCC), level, the
rhythm pattern inside the beat, and the arrangement (stem levels from Demucs)."""
import librosa
import numpy as np
import soxr

from ..config import ANALYSIS_SR, SAMPLE_RATE
from .beats import HR_HOP

HOP = 256
N_RHYTHM = 12
N_MFCC = 13
STEMS = ("drums", "bass", "other", "vocals")


def mono22(y: np.ndarray) -> np.ndarray:
    return soxr.resample(y.mean(1), SAMPLE_RATE, ANALYSIS_SR).astype(np.float32)


def onset_hr(y22: np.ndarray) -> np.ndarray:
    env = librosa.onset.onset_strength(y=y22, sr=ANALYSIS_SR, hop_length=HR_HOP, n_fft=512,
                                       n_mels=64, fmax=8000)
    return env.astype(np.float32)


def _beat_bounds(beats: np.ndarray, duration: float) -> np.ndarray:
    ibi = np.median(np.diff(beats)) if len(beats) > 1 else 0.5
    end = min(duration, beats[-1] + ibi)
    return np.append(beats, max(end, beats[-1] + 0.05))


def _interval_mean(frames: np.ndarray, frame_rate: float, starts: np.ndarray, ends: np.ndarray) -> np.ndarray:
    """Mean of frame features (T, D) over each [start, end) time interval."""
    T = frames.shape[0]
    cs = np.vstack([np.zeros((1, frames.shape[1])), np.cumsum(frames, 0)])
    lo = np.clip(np.floor(starts * frame_rate).astype(int), 0, T - 1)
    hi = np.clip(np.ceil(ends * frame_rate).astype(int), 1, T)
    hi = np.maximum(hi, lo + 1)
    return (cs[hi] - cs[lo]) / (hi - lo)[:, None]


def compute(y: np.ndarray, y22: np.ndarray, beats: np.ndarray, stems: np.ndarray) -> dict:
    duration = len(y) / SAMPLE_RATE
    bounds = _beat_bounds(beats, duration)
    starts, ends = bounds[:-1], bounds[1:]
    mids = 0.5 * (starts + ends)
    fr = ANALYSIS_SR / HOP

    # Harmony is read from the pitched stems only: drum hits smear chroma and pull
    # key estimates toward whatever the snare happens to ring at.
    harmonic = stems[1] + stems[2] + 0.5 * stems[3]
    h22 = soxr.resample(harmonic.astype(np.float32), SAMPLE_RATE, ANALYSIS_SR).astype(np.float32)
    chroma = librosa.feature.chroma_cqt(y=h22, sr=ANALYSIS_SR, hop_length=HOP).T  # (T, 12)
    # How far the recording sits off A440, in cents: chroma hides it (it is
    # normalised per song), but two songs 30 cents apart beat against each
    # other when they are mixed.
    tuning = 100.0 * float(librosa.estimate_tuning(y=h22, sr=ANALYSIS_SR))
    mel = librosa.feature.melspectrogram(y=y22, sr=ANALYSIS_SR, hop_length=HOP, n_fft=2048, n_mels=96)
    logmel = librosa.power_to_db(mel, ref=1.0, top_db=None)
    mfcc = librosa.feature.mfcc(S=logmel, n_mfcc=N_MFCC + 1)[1:].T  # drop c0 (level)
    power = librosa.feature.rms(y=y22, hop_length=HOP)[0] ** 2
    onset = librosa.onset.onset_strength(S=logmel, sr=ANALYSIS_SR, hop_length=HOP)

    def halves(frames):
        a = _interval_mean(frames, fr, starts, mids)
        b = _interval_mean(frames, fr, mids, ends)
        return np.stack([a, b], 1)

    ch = halves(chroma)
    ch /= np.linalg.norm(ch, axis=2, keepdims=True) + 1e-9
    mf = halves(mfcc)
    loud = 10 * np.log10(_interval_mean(power[:, None], fr, starts, ends)[:, 0] + 1e-10)

    # Onset envelope folded into N_RHYTHM sub-beat bins: the drum pattern of the beat.
    sub = np.linspace(0, 1, N_RHYTHM + 1)
    rs = starts[:, None] + sub[None, :-1] * (ends - starts)[:, None]
    re = starts[:, None] + sub[None, 1:] * (ends - starts)[:, None]
    rh = _interval_mean(onset[:, None], fr, rs.ravel(), re.ravel())[:, 0].reshape(-1, N_RHYTHM)
    rh /= np.linalg.norm(rh, axis=1, keepdims=True) + 1e-9

    # Key evidence: melody and chords (other + vocals) plus the bass line, which
    # sits on the tonic far more often than anything else. Log compression
    # keeps one loud sustained note from deciding the key on its own.
    def log_profile(sig):
        s22 = soxr.resample(sig.astype(np.float32), SAMPLE_RATE, ANALYSIS_SR)
        p = np.log1p(10 * librosa.feature.chroma_cqt(y=s22, sr=ANALYSIS_SR, hop_length=512)).mean(1)
        return p / (p.sum() + 1e-9)

    key_profile = log_profile(stems[2] + stems[3]) + 0.7 * log_profile(stems[1])

    # The singer: pitch register (separates a male and a female voice an octave
    # or so apart) and the vocal stem's own spectral envelope (who is singing,
    # and which syllables). yin on the vocal stem, gated by its level, agrees
    # with pyin within 2 semitones on 84-96 % of beats at a 13th of the cost.
    v22 = soxr.resample(stems[3].astype(np.float32), SAMPLE_RATE, ANALYSIS_SR).astype(np.float32)
    vhop = 512
    f0 = librosa.yin(v22, fmin=65, fmax=1000, sr=ANALYSIS_SR, frame_length=2048, hop_length=vhop)
    vrms = librosa.feature.rms(y=v22, frame_length=2048, hop_length=vhop)[0]
    vdb = 20 * np.log10(vrms + 1e-9)
    sung = vdb > np.percentile(vdb, 95) - 25
    vmidi = librosa.hz_to_midi(f0)
    vtimes = np.arange(len(f0)) * vhop / ANALYSIS_SR
    vpitch = np.full(len(starts), np.nan, dtype=np.float32)
    lo = np.searchsorted(vtimes, starts)
    hi = np.searchsorted(vtimes, ends)
    for b in range(len(starts)):
        m = sung[lo[b]:hi[b]]
        if m.sum() >= 2:
            vpitch[b] = np.median(vmidi[lo[b]:hi[b]][m])
    vmel = librosa.feature.melspectrogram(y=v22, sr=ANALYSIS_SR, hop_length=HOP, n_fft=2048, n_mels=64)
    vmfcc = librosa.feature.mfcc(S=librosa.power_to_db(vmel, ref=1.0, top_db=None), n_mfcc=N_MFCC + 1)[1:].T
    vmf = halves(vmfcc)

    stem_hop = 512
    usable = (stems.shape[1] // stem_hop) * stem_hop
    sp = (stems[:, :usable].astype(np.float64) ** 2).reshape(len(stems), -1, stem_hop).mean(2).T
    stem_db = 10 * np.log10(_interval_mean(sp, SAMPLE_RATE / stem_hop, starts, ends) + 1e-10)
    stem_db = np.maximum(stem_db, -80.0)
    # Vocal presence: how close the vocal stem sits to the full mix level.
    vocal = np.clip((stem_db[:, 3] - loud + 15.0) / 10.0, 0, 1)
    vocal[stem_db[:, 3] < -55] = 0.0

    return {
        "chroma": ch.astype(np.float32),
        "mfcc": mf.astype(np.float32),
        "loud": loud.astype(np.float32),
        "rhythm": rh.astype(np.float32),
        "stems": stem_db.astype(np.float32),
        "vocal": vocal.astype(np.float32),
        "dur": (ends - starts).astype(np.float32),
        "key_profile": key_profile.astype(np.float32),
        "vpitch": vpitch,
        "vmfcc": vmf.astype(np.float32),
        "tuning": np.float32(tuning),
    }


def timbre_colors(mfcc: np.ndarray) -> list[str]:
    """Map the first three principal components of timbre to RGB, like the
    original Infinite Jukebox: beats that sound alike get similar colours."""
    m = mfcc.mean(1)
    m = (m - m.mean(0)) / (m.std(0) + 1e-9)
    _, _, vt = np.linalg.svd(m, full_matrices=False)
    pcs = m @ vt[:3].T
    lo, hi = np.percentile(pcs, 3, 0), np.percentile(pcs, 97, 0)
    rgb = np.clip((pcs - lo) / (hi - lo + 1e-9), 0, 1)
    rgb = 0.25 + 0.75 * rgb
    return ["#%02x%02x%02x" % tuple(int(c * 255) for c in px) for px in rgb]
