"""Download the pretrained pothole model into models/ and verify it.

    python -m app.vision.fetch_model                 # download, check size and SHA-256, install
    python -m app.vision.fetch_model --sha256 <hex>  # override the pinned hash (first use, or after you reviewed a new version)

Source: https://huggingface.co/vinothvikas1987/pothole-detection-yolov8 (Apache-2.0), file best.onnx, about 45 MB,
YOLOv8s trained on the Road Damage Dataset. The file is downloaded to a temporary name, checked, then moved into place;
a file whose hash does not match is deleted and never used. See docs/POTHOLE_MODEL.md.
"""
import argparse
import hashlib
import os
import sys
import urllib.request

from .pothole_model import DEFAULT_PATH, model_path

REPO = "vinothvikas1987/pothole-detection-yolov8"
REVISION = "main"
URL = f"https://huggingface.co/{REPO}/resolve/{REVISION}/best.onnx"
PINNED_SHA256 = "590a20e8c4a7bcbdb32e8a3b7b3a3c1d57d1fa8a7dde5c83fcec8928a4aa753f"   # matches the SHA-256 shown on the model page; 44,752,195 bytes
MIN_BYTES, MAX_BYTES = 30_000_000, 80_000_000


def sha256_of(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sha256", default=PINNED_SHA256, help="expected SHA-256 of best.onnx")
    ap.add_argument("--dest", default=str(model_path()))
    a = ap.parse_args()
    dest = a.dest
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    print(f"Downloading {URL}")
    with urllib.request.urlopen(URL, timeout=60) as r, open(tmp, "wb") as f:
        total = 0
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            total += len(b)
            if total > MAX_BYTES:
                f.close(); os.remove(tmp)
                raise SystemExit("file is larger than expected; refusing it")
            f.write(b)
    digest = sha256_of(tmp)
    print(f"size {total} bytes, sha256 {digest}")
    if not (MIN_BYTES <= total <= MAX_BYTES):
        os.remove(tmp)
        raise SystemExit("unexpected file size; refusing it")
    if a.sha256 and digest != a.sha256.lower():
        os.remove(tmp)
        raise SystemExit("SHA-256 does not match the expected value; the file was deleted")
    if not a.sha256:
        print("WARNING: no pinned hash yet. Compare the value above with the model page, then pin it in fetch_model.py.", file=sys.stderr)
    os.replace(tmp, dest)
    print(f"Installed {dest}")


if __name__ == "__main__":
    main()
