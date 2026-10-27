"""60 s playback benchmark of a shown PetWindow: achieved fps, CPU and memory.

Run from the project root: python -X utf8 tests/bench_player.py [seconds]
Targets: fps within 5% of the clip fps, CPU < 15% of one core, RSS growth < 20 MB.
The window really appears on screen (bottom-right) for the duration; it is never screenshotted.
Uses the real assets/anim + director when present (see tests/test_player.py::anim_env).
"""
import ctypes
import ctypes.wintypes
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_player as tp  # noqa: E402  (temp INNER_OS_PET_CONFIG, QApplication)

from inner_os_pet import player  # noqa: E402
from inner_os_pet.pet import PetWindow  # noqa: E402


def rss_bytes():
    try:
        import psutil
        return psutil.Process().memory_info().rss
    except ImportError:
        class PMC(ctypes.Structure):
            _fields_ = [("cb", ctypes.wintypes.DWORD), ("PageFaultCount", ctypes.wintypes.DWORD)] + [
                (n, ctypes.c_size_t) for n in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                                               "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                                               "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(pmc),
                                                 pmc.cb)
        return pmc.WorkingSetSize


paints = [0]


class CountingPet(PetWindow):
    def paintEvent(self, e):
        paints[0] += 1
        super().paintEvent(e)


def main(seconds=60.0):
    anim, base, desc = tp.anim_env()
    w = CountingPet(tp.test_cfg(), anim_dir=anim, director_factory=base)
    plays = []
    orig_play = w.player.play

    def counting_play(p):
        plays.append(p.clip)
        orig_play(p)
    w.player.play = counting_play
    w.show()
    tp.pump(3.0)  # warm-up: first clip, caches, allocator

    clip_fps = sorted({float(c.get("fps") or 0) for c in w.library.clips.values()})
    stats0 = dict(w.player.stats)
    paints0, cpu0, t0, rss0 = paints[0], time.process_time(), time.perf_counter(), rss_bytes()
    # A realistic mix: idle clips chosen by the director, a thinking bubble, a line bubble, a work turn.
    script = [(5, lambda: (w.agent_event("prompt"), w.show_thinking("Claude · bench"))),
              (10, lambda: w.show_line("這使用者發的啥啊，又要我通靈了是吧…", "Claude · bench", "已思考（用時 5 秒）")),
              (25, lambda: w.agent_event("tool")),
              (35, lambda: w.agent_event("done"))]
    script = [(t * seconds / 60.0, f) for t, f in script]
    trend, next_sample = [], seconds / 4
    while (elapsed := time.perf_counter() - t0) < seconds:
        while script and elapsed >= script[0][0]:
            script.pop(0)[1]()
        if elapsed >= next_sample:
            trend.append((round(elapsed), rss_bytes()))
            next_sample += seconds / 4
        tp.pump(0.25)
    wall = time.perf_counter() - t0
    cpu = time.process_time() - cpu0
    rss1 = rss_bytes()
    shown = w.player.stats["shown"] - stats0["shown"]
    dropped = w.player.stats["dropped"] - stats0["dropped"]
    underruns = w.player.stats["underruns"] - stats0["underruns"]
    n_paints = paints[0] - paints0

    t = time.perf_counter()
    w.shutdown()
    stop_ms = (time.perf_counter() - t) * 1000
    w.close()
    tp.pump(0.05)
    left = [th.name for th in threading.enumerate() if th.name.startswith(player.THREAD_PREFIX) and th.is_alive()]

    target = clip_fps[-1] if clip_fps else 24.0
    fps = shown / wall
    cpu_pct = cpu / wall * 100
    growth_mb = (rss1 - rss0) / 2**20
    print(f"[{desc}] {wall:.1f} s shown, dpr={w.devicePixelRatioF()}, frame {w._size}px, clips played: {len(plays)}")
    print(f"fps        {fps:.2f} (clip fps {target:g}; {fps / target * 100:.1f}%)  "
          f"dropped {dropped}  underruns {underruns}  paints {n_paints} ({n_paints / wall:.1f}/s)")
    print(f"CPU        {cpu_pct:.1f}% of one core (process_time {cpu:.2f} s / wall {wall:.1f} s)")
    print(f"RSS        start {rss0 / 2**20:.1f} MB  end {rss1 / 2**20:.1f} MB  growth {growth_mb:+.1f} MB  "
          f"(trend: {', '.join(f'{t}s {r / 2**20:.1f}' for t, r in trend)})")
    print(f"shutdown   {stop_ms:.1f} ms, decoder threads left: {left or 'none'}")
    ok = abs(fps / target - 1) <= 0.05 and cpu_pct < 15 and growth_mb < 20 and not left
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(float(sys.argv[1]) if len(sys.argv) > 1 else 60.0))
