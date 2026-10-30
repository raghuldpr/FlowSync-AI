# Pothole model

The ESP32-CAM only takes pictures and posts them (`POST /api/device/images`). Detection runs on the laptop.

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
Downloads to `models/pothole_yolov8s_rdd.onnx` (git-ignored), checks the size and the SHA-256, and deletes the file if the hash does not match. Set `FLOWSYNC_POTHOLE_MODEL` to use another path.

## Behaviour
- Model installed: detections are labelled `camera_model`.
- Not installed, or it fails to load or run: the old dark-patch heuristic runs and is labelled `camera_heuristic`. Image intake never fails because of the model.
- Either way a detection is an **unreviewed** report: nothing changes routing or driver alerts until an operator verifies it.

## Limits to say out loud
- Trained on dashcam-style photos; a low-resolution ESP32-CAM on a toy car looking at a printed prop is a different domain, so expect misses and false alarms. Test with the real camera before the demo.
- A model detection is never shown to drivers as fact.
