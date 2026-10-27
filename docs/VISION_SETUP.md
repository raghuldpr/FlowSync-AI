# Overhead camera setup (Phase 7)

The phone above the table is the system's eyes: it counts vehicles on each approach, notices when they stop
moving, and tracks the connected car (the EV) by an ArUco marker. All of it runs in `python -m app.vision.run`,
a separate process from the API server.

> Status: pipeline verified only against a **synthetic** camera (rendered images with known truth). Real lighting,
> shadows, phone optics and toy-car colours will be harder — expect to tune `diff_threshold` and the car footprint
> on the real table (use `--debug-dir`).

## 1. Draw the table in the layout frame
`config/topology.yaml` → `layout` gives every node's position in *layout units*. Choose a scale (e.g. 1 unit = 1 mm
→ an 80 × 52 cm board) and draw the roads exactly there. Each direction of a road is its own lane, offset `lane_offset`
(14 units) to the **left of travel** from the road axis — the same as the dashboard map — so the two lanes of a road are
28 units apart. Intersection areas are **circles of radius 30** (draw them round, or keep diagonal lanes clear of any
square corners). The counting region of a lane skips its first 30 units after it leaves an intersection
(`roi_exit_clearance`), because right after a junction neighbouring lanes are closer than a lane width.
The ROI half-width (10) must stay below `lane_offset`; `VisionConfig` refuses configurations where lanes' ROIs could overlap.

**Capacity check (important).** Lanes are short compared with toy cars. Run
`python -m app.vision.capacity`: with 28 × 16 units cars on that layout the camera ROIs hold only 2–6 vehicles
(S3 holds 2), not the simulation's 8. For the physical table / synthetic camera generate and use the physical file:
```
python -m app.vision.capacity --write config/topology.physical.yaml
set FLOWSYNC_TOPOLOGY_PATH=config/topology.physical.yaml     (PowerShell: $env:FLOWSYNC_TOPOLOGY_PATH=...)
```
(or make the board bigger / the cars smaller; `vision.car_length/car_width/car_gap` must match your real cars).

## 2. Print and place the markers
```
python -m app.vision.markers --out print_markers --mm-per-unit 1.0
```
Print at **100 % scale** (no "fit to page") and measure the black square against `print_markers/sizes.txt`.
* Corner markers **10, 11, 12, 13** — centres at layout (40,40), (760,40), (760,480), (40,480), **upright** (the marker's top toward the top of the table), flat, with their white border.
* EV marker **20** on the roof of the connected car: **the marker's top points where the car's nose points** (that is how the two lanes of a road are told apart).
* Keep other cars in colours that contrast with the road surface (very light yellow/white cars on a light road are invisible to background differencing).

## 3. Mount and lock the phone
Rigid overhead mount, whole board plus all four corner markers in view, as square-on as practical. In the phone's
camera/IP-camera app **lock exposure, focus and white balance**. Serve MJPEG/RTSP (e.g. an Android IP-camera app) and
check the URL in a browser first. Prefer 1080p or 720p; the pipeline processes ~5 frames/s.

## 4. Capture the empty-table reference (once per mounting/lighting change)
Clear the board of cars (corner markers stay), then:
```
python -m app.vision.run --source http://PHONE_IP:8080/video --capture-reference 10
```
This calibrates the homography from the four corner markers and stores `vision_ref.png` (git-ignored).
It refuses if the markers are not all visible.

## 5. Run
```
python -m app.vision.run --source http://PHONE_IP:8080/video --backend http://LAPTOP_IP:8000 --debug-dir debug
```
* Posts `POST /api/device/traffic` (source `overhead_camera`, or `recorded_video` for a file) at 2 Hz and `POST /api/device/ev-position` when the EV moves or every 1 s.
* The homography refreshes each frame the four corners are visible, so small camera shifts self-correct. If the corners are hidden for more than 3 s, traffic posts stop and the backend marks the data stale (never "zero").
* `debug/debug_*.png` is the rectified top-down view with ROIs, counts, "stationary for" seconds, the EV and calibration error drawn on it. Check it against the real table before trusting any number.
* The `camera-1` key in `config/devices.yaml` must be the real one.

## What the numbers mean / known limits
* **Count** = foreground blobs inside the approach ROI vs the empty reference; touching cars are split by area (`round(area / car footprint)`), so a dense queue can be off by one. It is an estimate — spot-check it.
* **stationary_s** = how long the ROI's foreground has not changed shape; the backend only treats it as a stall while that lane has been green.
* **EV**: marker centre → nearest lane (within `ev_snap_distance`, heading-aware) → `(edge, progress)`. Inside an intersection box the EV is reported at the end of the edge it was on.
* Hands/arms over the table count as vehicles. A car hiding the EV marker (or a marker tilted too far) loses the EV; after 4 s the backend marks its location stale and withdraws priority/warnings.
* Not done (Tier 2): YOLO as a secondary detector, per-lane direction, motion tracking across frames.

## Demo without a camera
`python -m simulator.run_sim --demo --vision` renders the simulated queues and EV with a synthetic camera and posts what the **real pipeline measures** (start the backend with the physical topology above so capacities match).
