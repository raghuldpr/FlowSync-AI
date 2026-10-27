"""Camera process: read frames, perceive, post to the backend. Runs separately from the API server so heavy
vision work never blocks FastAPI.

  python -m app.vision.run --source http://PHONE_IP:8080/video --capture-reference 10     # once, empty table
  python -m app.vision.run --source http://PHONE_IP:8080/video --backend http://LAPTOP:8000
  python -m app.vision.run --source recording.mp4 --debug-dir debug/                       # recorded video

Reference capture needs all four corner markers visible. Keep exposure/focus locked on the phone.
"""
import argparse
import sys
import time
from pathlib import Path

import cv2
import httpx

from ..config import ROOT, Settings, load_yaml
from ..core.topology import Topology
from ..device_client import DeviceClient
from .pipeline import VisionPipeline
from .poster import Poster
from .sources import CvSource

PROCESS_HZ = 5.0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", required=True, help="stream URL, video file, or camera index")
    p.add_argument("--backend", default="http://127.0.0.1:8000")
    p.add_argument("--device", default="camera-1")
    p.add_argument("--ref", default=str(ROOT / "vision_ref.png"), help="empty-table reference image")
    p.add_argument("--capture-reference", type=int, metavar="N", help="capture N frames of the EMPTY table and exit")
    p.add_argument("--debug-dir", help="write annotated top-down images here")
    p.add_argument("--debug-every", type=float, default=5.0)
    a = p.parse_args(argv)

    s = Settings()
    topo = Topology.load(s.topology_path)
    pipe = VisionPipeline(topo)
    src = CvSource(a.source)
    label = "recorded_video" if src.is_file else "overhead_camera"

    if a.capture_reference:
        frames, t0 = [], time.monotonic()
        while len(frames) < a.capture_reference and time.monotonic() - t0 < 60:
            f = src.read()
            if f is not None:
                frames.append(f)
                time.sleep(0.1)
        if not frames or not pipe.capture_reference(frames):
            print("Could not calibrate: all four corner markers must be visible in most frames.", file=sys.stderr)
            return 1
        cv2.imwrite(a.ref, pipe.ref)
        print(f"reference saved to {a.ref} (calibration error {pipe.calib_error:.2f} layout units)")
        return 0

    ref = cv2.imread(a.ref, cv2.IMREAD_GRAYSCALE)
    if ref is None:
        print(f"No reference image at {a.ref}. Run once with --capture-reference 10 on the empty table.", file=sys.stderr)
        return 1
    pipe.set_reference([ref])

    spec = load_yaml(s.devices_path)["devices"][a.device]
    poster = Poster(DeviceClient(httpx.Client(base_url=a.backend, timeout=2.0), a.device, spec["key"]), label, pipe.cfg.post_hz)
    dbg = Path(a.debug_dir) if a.debug_dir else None
    if dbg:
        dbg.mkdir(parents=True, exist_ok=True)
    last_proc = last_dbg = last_log = 0.0
    print(f"camera running: source={a.source} label={label} backend={a.backend}")
    while True:
        frame = src.read()
        now = time.monotonic()
        if frame is None:
            if src.is_file:
                break
            time.sleep(0.05)
            continue
        if now - last_proc < 1.0 / PROCESS_HZ:
            continue
        last_proc = now
        obs = pipe.process(frame, now)
        poster.handle(obs, now)
        if dbg and now - last_dbg >= a.debug_every:
            img = pipe.annotate(obs)
            if img is not None:
                cv2.imwrite(str(dbg / f"debug_{int(now)}.png"), img)
            last_dbg = now
        if now - last_log >= 5.0:
            last_log = now
            tot = sum(n for n, _ in obs.counts.values()) if obs.counts else None
            print(f"[cam] calibrated={obs.calibrated} fresh={obs.fresh_calibration} err={obs.calib_error} "
                  f"vehicles={tot} ev={[e.edge for e in obs.ev]} posted traffic={poster.sent_traffic} ev={poster.sent_ev} errors={poster.errors}")
    src.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
