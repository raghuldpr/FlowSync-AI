"""SIMULATED camera feed: plays a video into a running backend frame by frame, posting each frame the way a backend-connected
camera would (signed, base64 JPEG). The real ESP32-CAM in the demo is standalone and does NOT do this.

  python tools/send_test_video.py testdata/pothole/drive_by.mp4
  python tools/send_test_video.py testdata/pothole/drive_by.mp4 --fps 1 --host http://192.168.1.50:8000

--fps is how many frames per second are SENT (default 2: slow enough to follow on the dashboard). Press Ctrl+C to stop.
One pothole seen in many frames produces ONE unreviewed hazard; the operator still has to verify it.
Needs the backend running, `pip install httpx opencv-python`, and the roadcam-1 key from config/devices.yaml.
"""
import argparse
import base64
import os
import sys
import time

import cv2
import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from app.device_client import DeviceClient                          # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--host", default="http://127.0.0.1:8000")
    ap.add_argument("--edge", default="L13")
    ap.add_argument("--progress", type=float, default=0.5)
    ap.add_argument("--key", default="dev-roadcam-1-change-me")
    ap.add_argument("--fps", type=float, default=2.0, help="frames sent per second")
    ap.add_argument("--loop", action="store_true", help="repeat the video until Ctrl+C")
    a = ap.parse_args()
    dc = DeviceClient(httpx.Client(base_url=a.host, timeout=10), "roadcam-1", a.key)
    cap = cv2.VideoCapture(a.video)
    if not cap.isOpened():
        print("cannot open", a.video)
        return 1
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    step = max(1, round(src_fps / a.fps))                            # send every n-th source frame
    n = sent = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                if a.loop:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    n = 0
                    continue
                break
            n += 1
            if (n - 1) % step:
                continue
            ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            r = dc.request("POST", "/api/device/images", {"edge": a.edge, "progress": a.progress,
                                                           "image_b64": base64.b64encode(jpg.tobytes()).decode()})
            sent += 1
            if r.status_code == 200:
                j = r.json()
                tag = f"POTHOLE -> hazard #{j['reported_hazard']}" if j.get("reported_hazard") else ("candidate" if j["detections"] else "clear")
                print(f"t={n / src_fps:5.1f}s  frame {sent:3d}  detections={j['detections']}  {tag}")
            else:
                print(f"t={n / src_fps:5.1f}s  frame {sent:3d}  HTTP {r.status_code} {r.text[:120]}")
            time.sleep(1.0 / a.fps)
    except KeyboardInterrupt:
        pass
    print(f"sent {sent} frames")
    return 0


if __name__ == "__main__":
    sys.exit(main())
