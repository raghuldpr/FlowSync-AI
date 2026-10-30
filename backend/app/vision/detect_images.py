"""Run the pothole detector on photos or a recorded clip and save annotated copies (offline; no backend, no camera link).

    python -m app.vision.detect_images photo1.jpg photo2.jpg --out data/pothole_out
    python -m app.vision.detect_images clip.mp4 --every 15 --out data/pothole_out

This is how the demo shows the pothole layer: the ESP32-CAM (standalone) saves pictures, you copy them here, and the
software finds candidates. Every result is a CANDIDATE for a person to check, labelled with the detector that ran.
"""
import argparse
import sys
from pathlib import Path

import cv2

from . import pothole


def annotate(img, dets, source):
    for d in dets:
        x, y, w, h = d["bbox"]
        cv2.rectangle(img, (x, y), (x + w, y + h), (0, 0, 255), 2)
        cv2.putText(img, f"pothole? {d['confidence']:.2f}", (x, max(14, y - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
    cv2.putText(img, f"{source} - candidate, needs a person to verify", (6, img.shape[0] - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
    return img


def frames(path: Path, every: int):
    if path.suffix.lower() in (".mp4", ".avi", ".mov", ".mkv", ".webm"):
        cap = cv2.VideoCapture(str(path))
        i = 0
        while True:
            ok, f = cap.read()
            if not ok:
                break
            if i % every == 0:
                yield f"{path.stem}_f{i:05d}", f
            i += 1
        cap.release()
    else:
        img = cv2.imread(str(path))
        if img is None:
            print(f"cannot read {path}", file=sys.stderr)
        else:
            yield path.stem, img


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--out", default="data/pothole_out")
    ap.add_argument("--every", type=int, default=15, help="for video: analyse every Nth frame")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for p in a.inputs:
        for name, img in frames(Path(p), max(1, a.every)):
            dets, source = pothole.detect_labelled(img)
            print(f"{name}: {len(dets)} candidate(s) [{source}]" + "".join(f" {d['confidence']:.2f}" for d in dets))
            if dets:
                cv2.imwrite(str(out / f"{name}_annotated.jpg"), annotate(img.copy(), dets, source))


if __name__ == "__main__":
    main()
