"""SIMULATED camera feed: posts a test image to a running backend the way a backend-connected camera would (signed, base64 JPEG)
and prints what came back. The real ESP32-CAM in the demo is standalone and does NOT do this.

  python tools/send_test_image.py testdata/pothole/road_pothole_medium.jpg
  python tools/send_test_image.py testdata/pothole/road_clear.jpg --host http://192.168.1.50:8000

Needs the backend running and `pip install httpx`. Uses the roadcam-1 key from config/devices.yaml.
The frame is located by --edge (default L13) because no tracked car is on a bench test.
"""
import argparse
import base64
import os
import sys

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from app.device_client import DeviceClient                          # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--host", default="http://127.0.0.1:8000")
    ap.add_argument("--edge", default="L13")
    ap.add_argument("--progress", type=float, default=0.5)
    ap.add_argument("--key", default="dev-roadcam-1-change-me")
    a = ap.parse_args()
    data = open(a.image, "rb").read()
    dc = DeviceClient(httpx.Client(base_url=a.host, timeout=10), "roadcam-1", a.key)
    r = dc.request("POST", "/api/device/images", {"edge": a.edge, "progress": a.progress, "image_b64": base64.b64encode(data).decode()})
    print(r.status_code, r.text)
    return 0 if r.status_code == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
