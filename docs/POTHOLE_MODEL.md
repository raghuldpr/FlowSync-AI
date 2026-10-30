# Pothole model

The ESP32-CAM is **standalone** on the demo car: its own power, no link to the car brain and no link to the backend. It only takes pictures. Detection runs on the laptop, offline, on those pictures or a recorded clip:

```
python -m app.vision.detect_images photos/*.jpg --out data/pothole_out     # or a .mp4 with --every 15
```

The pothole layer judges see on their phones comes from the software simulation (an operator-declared or scripted hazard), not from a live camera feed. The `POST /api/device/images` path still exists and works the same way (future work: a camera that posts to the backend).

| | |
|---|---|
| Model | [vinothvikas1987/pothole-detection-yolov8](https://huggingface.co/vinothvikas1987/pothole-detection-yolov8), file `best.onnx` (about 45 MB) |
| Licence | Apache-2.0 (from the model card; re-check before redistributing) |
| Architecture | YOLOv8s, pretrained on COCO, fine-tuned on the Road Damage Dataset (real photos from Japan, India, Czech Republic) |
| Classes | 0 longitudinal crack, 1 transverse crack, 2 alligator crack, 3 pothole, 4 other. We only use class 3. |
| Reported quality | pothole mAP@0.5 0.78 on its own validation set; overall 0.63. Not measured on our camera or our tabletop. |
| Runtime | OpenCV DNN (`cv2.dnn`), already a dependency. Frames are letterboxed to 640x640. |

## Install
```
python -m app.vision.fetch_model        # run from the repository root with PYTHONPATH=backend
```
Downloads to `models/pothole_yolov8s_rdd.onnx` (git-ignored), checks the size and the pinned SHA-256 (`590a20e8…a753f`, 44,752,195 bytes), and deletes the file if the hash does not match. Set `FLOWSYNC_POTHOLE_MODEL` to use another path.

## Behaviour (also when images come in through the device API)
- Model installed: detections are labelled `camera_model`.
- Not installed, or it fails to load or run: the old dark-patch heuristic runs and is labelled `camera_heuristic`. Image intake never fails because of the model.
- Either way a detection is an **unreviewed** report: nothing changes routing or driver alerts until an operator verifies it.

## Limits to say out loud
- Trained on dashcam-style photos; a low-resolution ESP32-CAM on a toy car looking at a printed prop is a different domain, so expect misses and false alarms. Test with the real camera before the demo.
- A model detection is never shown to drivers as fact.

## Test material (SIMULATED camera feed)
The ESP32-CAM in the demo is standalone. To show the detection and the operator workflow without it, the scripts in `tools/` post
frames to a running backend as a **simulated camera feed** (they use the `roadcam-1` development key from `config/devices.yaml`):

```
python tools/make_pothole_test.py                                   # regenerates testdata/pothole/ (synthetic roads, a drive-by clip, a print prop)
python tools/send_test_image.py testdata/pothole/road_pothole_medium.jpg
python tools/send_test_video.py testdata/pothole/drive_by.mp4 --fps 1
```
One pothole seen in many frames becomes one unreviewed hazard; the operator must verify it, and a verified hazard can be handed to a
**simulated** road-authority outbox (nothing leaves the system). The operator page shows the camera feed, the detection box and the outbox.
`firmware/lcdtest` is a bring-up sketch for the car's 16x2 LCD on its own I2C bus (GPIO 16/17).
