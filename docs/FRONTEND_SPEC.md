# FlowSync AI — Frontend Spec (the "wow" build)

Owner: frontend teammate · Backend contact: backend owner · Status: ready to build

This document is everything you need to rebuild the FlowSync frontend into something judges remember. It covers the
screens, their layout, how to structure the code, the exact animations and 3D pieces, the data each screen reads,
and the rules you must not break. Read **§1 (the rules)** before anything else. The rest can be read as you build.

---

## 0. The idea in one paragraph

A judge scans a QR code on the tabletop and **becomes a driver** in our city. Their phone turns into a dark in-car
display. Their car appears, in their colour, on a **3D twin of the tabletop** on the projector. When the ambulance is
dispatched, a **wave** spreads from it on the big screen, and **only the phones on its path** go into a full-screen
red/blue emergency takeover. The others stay calm. When the route changes, one phone calms down and another lights up.
When the ambulance passes, everyone gets a green tick. At the end each judge gets a **trip receipt** that says their data
was deleted. Everything on the phone, the projector and the physical table happens **at the same moment**. That sync is the wow.

---

## 1. Rules you must not break (judges check these)

These come straight from the problem statement. Breaking one costs more points than any animation earns.

| # | Rule | What it means for you |
|---|---|---|
| R1 | Public users must not see restricted tracking data | The **driver phone never shows the ambulance's position, route, ID or a live dot.** It shows only: the server's warning text, a coarse ETA band (`approaching` / `imminent`) and, if the backend provides it, a coarse direction. **Never** draw other road users on a phone either. |
| R2 | Warnings ask drivers to make way only when safe and lawful | **Show the server's `text` exactly as given.** Never write your own warning sentences in JS, never shorten them, never add "Move now!", "Hurry", "Pull over immediately". Headings like "Emergency vehicle" are fine. |
| R3 | No unsafe instructions | No countdowns that pressure ("3… 2… 1… GO"), no "speed up to catch green", no instructions to cross a red. |
| R4 | Stale is not safe | If a signal is `"unknown"` or data is stale, draw it **grey with a "?"**. Never default a lamp to green, and never default a queue to empty. |
| R5 | Restricted screens need login | Presenter stage and operator console use the bearer token from `/api/auth/login`. The ambulance view (§3.6) is restricted too. |
| R6 | Honesty | The "Built vs simulated" panel stays visible on the presenter screen. Never hide it to make room for eye candy. |
| R7 | Driver display, not a phone app | Big, glanceable, no typing after "Start driving". Judges *will* ask "isn't using a phone while driving unsafe?". The answer is that this is the in-car display. |

---

## 2. Tech choices (decided, don't relitigate)

- **No build step.** Plain HTML + ES modules (`<script type="module">`), served by FastAPI from `dashboard/`. No React,
  no npm, no bundler. The laptop has no Node.js.
- **3D:** [three.js](https://threejs.org) `0.160.0` (ES module build + `examples/jsm` addons: `OrbitControls`,
  `EffectComposer`, `RenderPass`, `UnrealBloomPass`).
- **Tweening/timelines:** GSAP `3.12.5` (core only, free).
- **Fonts:** Inter (UI) + JetBrains Mono (numbers, plates).
- **Vendor everything locally** into `dashboard/vendor/` (three, its addons, gsap, font files). **Venue Wi-Fi may have no
  internet.** No CDN links in the final build. Load with an import map:
  ```html
  <script type="importmap">
  { "imports": { "three": "./vendor/three/three.module.js",
                 "three/addons/": "./vendor/three/addons/",
                 "gsap": "./vendor/gsap/gsap.mjs" } }
  </script>
  ```
- **Phones get 2D (SVG/Canvas) + CSS perspective**, not WebGL. That saves battery and avoids old-phone crashes.
  The **projector gets real 3D**.
- Must work in Chrome Android, Safari iOS and desktop Chrome. Test on real phones.

---

## 3. Screens

| ID | Screen | URL | Device | Auth | Priority |
|---|---|---|---|---|---|
| S1 | Join (QR landing) | `/driver.html?from=W1` | Judge phone | none | **P0** |
| S2 | Cockpit (driving) | `/driver.html` (same page, state) | Judge phone | road-user token | **P0** |
| S3 | Emergency takeover (overlay) | over S2 | Judge phone | road-user token | **P0** |
| S4 | Trip receipt | over S2 at arrival | Judge phone | road-user token | P1 |
| S5 | City Stage (3D twin) | `/presenter.html` | Projector 1920×1080 | viewer/operator | **P0** |
| S6 | Ambulance view | `/ambulance.html` | Tablet/phone | operator | P2 (needs backend) |
| S7 | Operator console (restyle) | `/operator.html` | Laptop | operator | P2 |
| S8 | Approach sign | `/public.html?approach=L12` | Spare phone/tablet on the table | none | P2 |

**P0 = must exist for the demo. P1 = strong. P2 = only if P0/P1 are rock solid.** Keep every existing feature of
`operator.html`. Restyle it, don't remove anything.

### S1 — Join (QR landing)

Purpose: get a judge from a QR scan to driving in **one tap**.

- QR codes on the table encode `http://LAPTOP_IP:8000/driver.html?from=W1` (one per entrance: `W1`, `N1`, `N2`, `E2`, `S3`).
  Read `from` from the URL and preselect it. Pick a destination automatically (a different external node, preferably
  one whose route crosses the ambulance's usual corridor W1→S3). Show it with a small "change" link.
- Show the judge's **plate** and **colour** before they tap. Both are derived on the client from the pseudonym `id` the
  server returns (see §6.3), so the projector shows the same plate and colour without any backend change.
- One huge button: **Start driving**. That tap also unlocks audio and asks for vibration (see §8).
- Small print: "Pseudonymous. No GPS. Deleted when you arrive."

```
┌──────────────────────────────┐
│  FlowSync          ● live    │  status bar 56px
│                              │
│      [ animated city loop ]  │  hero: slow pseudo-3D map, 40vh
│                              │
│  You enter at   West Gate    │
│  Heading to     South Gate ✎ │
│                              │
│   ┌──────────────────────┐   │
│   │  TN·07·K3F   ■ teal  │   │  plate card, mono font
│   └──────────────────────┘   │
│                              │
│ ┌──────────────────────────┐ │
│ │      START DRIVING       │ │  64px tall, full width
│ └──────────────────────────┘ │
│ Pseudonymous · no GPS ·      │
│ deleted on arrival           │
└──────────────────────────────┘
```

### S2 — Cockpit

Purpose: a calm, beautiful in-car display that is obviously live.

Zones (portrait, 390×844 baseline, respect `env(safe-area-inset-*)`):

1. **Status bar (56px):** plate chip in your colour · `driving` / `arrived` chip · connection dot
   (green = long-poll OK, amber = retrying, grey = offline banner).
2. **Map (≈45vh):** the tabletop map from `/api/public/topology`, tilted with CSS
   `transform: perspective(900px) rotateX(38deg)`. Your car is a glowing arrow that moves smoothly along `edge` + `progress`.
   Your route is drawn as a thick line ahead of you. Signal lamps come from `/api/public/signals` (poll every 1 s):
   small glowing dots, grey "?" when `unknown`. Congestion bands come from `/api/public/congestion` and colour the road
   (green/amber/red). Only **you** are on this map. No other cars, no ambulance.
3. **Trip strip (64px):** `West Gate → South Gate` · route text (`route_text`) · elapsed time (client clock).
4. **Alert stack (rest):** cards from `alerts[]`, newest live ones on top. Card types: `ev`, `route`, `incident`, `hazard`
   (see §6.2). A `suggestion` shows as a card with a **Take this route** button and "saves ~N s" (`saves_s`).
   Empty state: "All clear. Nothing affects your route right now."
5. **Bottom bar (72px):** `Report hazard` (P2, needs backend, see §9) · `Leave`.

```
┌──────────────────────────────┐
│ TN·07·K3F   driving     ●    │
│ ╱‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾╲  │
│╱   tilted map, your car ▲   ╲│  45vh
│     glowing route ahead      │
│──────────────────────────────│
│ West Gate → South Gate  02:14│
│ via Intersection 2           │
│──────────────────────────────│
│ ┌ INCIDENT AHEAD ──────────┐ │
│ │ Queue building on ...    │ │
│ └──────────────────────────┘ │
│ ┌ SUGGESTED ROUTE  saves 40s┐│
│ │ ...      [Take this route]││
│ └──────────────────────────┘ │
│──────────────────────────────│
│  Report hazard        Leave  │
└──────────────────────────────┘
```

### S3 — Emergency takeover (the money shot)

Trigger: an alert with `kind === "ev"` enters a live state (`active`, `updated`, `redirected`).

- Full-screen overlay over S2. The **screen edges pulse red ↔ blue** (inset box-shadow, 0.6 s alternate).
  The background dims to near black.
- Centre: big icon (ambulance/siren), heading **EMERGENCY VEHICLE**, then the **server `text` verbatim** (R2),
  then the ETA band chip: `approaching` (amber) / `very soon` for `imminent` (red, faster pulse).
- Direction arrow: only if the backend adds a coarse `direction` field (§9). Without it, skip the arrow. **Don't parse the text.**
- Sound: two-tone chime (not a real siren, that would be alarming), played once on entry and once on `imminent`.
  Vibration pattern `[200,100,200,100,400]` (Android only).
- Dismiss: there is no dismiss button. It follows the alert state:
  - `redirected` → a quick flash, the text updates (the server sends new text).
  - `cancelled_route_change` → "No longer approaching you". Edges fade to calm blue, overlay slides down after 2.5 s.
  - `cancelled_passed` → "Emergency vehicle has passed". A **green tick draws itself**, overlay fades after 2.5 s.
  - `cancelled_stale` / `expired` → "Warning ended". Neutral grey fade.
- `prefers-reduced-motion`: no pulsing. Use a solid red border and a static icon.

```
┌══════════════════════════════┐  ← edges pulse red/blue
║                              ║
║            (🚑)               ║  icon 96px, gentle scale pulse
║                              ║
║     EMERGENCY VEHICLE        ║
║                              ║
║  <server text, verbatim,     ║  28px, max 3 lines
║   e.g. "...make way only     ║
║   when it is safe and        ║
║   lawful to do so.">         ║
║                              ║
║        [ approaching ]       ║  ETA band chip
║                              ║
╚══════════════════════════════╝
```

### S4 — Trip receipt

Trigger: `status` becomes `arrived` (the session is deleted soon after, so capture the data the moment you see `arrived`).

Everything here can be computed **on the client**. No backend change needed:
- Trip time = now − join time (client clock).
- "Made way for N emergency vehicle(s)" = count of distinct `ev:*` alert ids you saw.
- "Rerouted N times" = number of times you tapped *Take this route*.
- The privacy line: **"Your session and position have been deleted."** Animate this line last (see §5.12).
- Button: **Drive again** (back to S1 with the same `from`).

### S5 — City Stage (projector, 3D)

Purpose: the room's shared view. This is where judges see **themselves**, and see that **only some of them** were warned.

Layout, 1920×1080, CSS grid `grid-template-columns: 1fr 440px; grid-template-rows: 72px 1fr 120px`:

```
┌───────────────────────────────────────────────────────────┬──────────────────┐
│ FlowSync AI  ·  scenario title  ·  ● running  · physical  │  t = 01:12       │ 72px header
├───────────────────────────────────────────────────────────┼──────────────────┤
│                                                           │ NOW              │
│                                                           │ "An ambulance is │
│                3D tabletop twin (three.js)                │  dispatched…"    │
│                                                           │──────────────────│
│     roads · lamps · cars with plates · ambulance          │ SELF-CHECKS      │
│     warning wave · green corridor ribbon                  │ ✓ congestion L13 │
│                                                           │ ✓ route avoids…  │
│                                                           │ ○ warned only…   │
│                                                           │──────────────────│
│  [legend: you-are-here colours]          [ 2D | 3D ]      │ BUILT vs SIM     │
│                                                           │ Controller ■Built│
├───────────────────────────────────────────────────────────┴──────────────────┤
│ road users 6 · warned 2 · not warned 4 · reroutes 1 · priority decisions 3   │ 120px tile bar
└──────────────────────────────────────────────────────────────────────────────┘
```

- Right rail = the existing presenter content (narration, cue, checks, provenance), restyled. **Keep all of it** (R6).
- Bottom tiles = live counters, then the scenario `summary` tiles when the run finishes (numbers count up).
- **Warned / not warned** counts are computed from `/api/operator/road-users`: a user is "warned" if any key in its
  `alerts` starts with `ev:` and its state is live.
- 2D fallback toggle: if WebGL fails or FPS < 30 for 3 s, switch to an SVG top-down view automatically.

### S6 — Ambulance view (P2)

A judge "drives" the ambulance: route line, the next intersections turning green ("holding green for you"), ETA.
This is restricted data (R1, R5), so it is **operator-login only**. The data exists in `/api/operator/ev` (route, edge,
progress, warnings) and `/api/operator/signals`. Coordinate with backend before building.

### S7 — Operator console (P2 restyle)

Apply the new tokens (§4) and components to `operator.html` without changing behaviour. One addition worth doing:
an **assistant trace** in the Assistant panel that shows each tool call as a step (tool name → short result → final answer),
revealed one by one with a 120 ms stagger. That makes "LLM as tool orchestrator, not a chatbot" visible.

### S8 — Approach sign (P2)

A phone or tablet propped on the table at one approach acts as a **roadside sign**. Black screen, huge amber text when
`/api/public/warnings?approach=…` returns a warning, blank otherwise. Same R2 wording rule.

---

## 4. Design system

### 4.1 Look

"Night city control room": dark, deep blue-black background, roads as soft slate, light coming *from* things
(lamps, cars, the ambulance), never flat neon everywhere. Glow is reserved for **meaning**: signals, your car,
the emergency vehicle, alerts.

### 4.2 Tokens (put in `dashboard/shared/tokens.css`)

```css
:root {
  /* surfaces */
  --bg: #070a12; --bg-2: #0d1220; --panel: rgba(20,27,45,.72); --line: rgba(140,160,200,.14);
  --ink: #e9eef8; --muted: #8a97b0; --road: #232c40; --road-edge: #36415c;
  /* meaning */
  --lamp-r: #ff4d5e; --lamp-y: #ffc23d; --lamp-g: #2ee68a; --unknown: #6b7489;
  --ev-red: #ff2d55; --ev-blue: #2d7bff; --ok: #2ee68a; --warn: #ffb02e; --bad: #ff4d5e;
  --flow-free: #2ee68a; --flow-busy: #ffb02e; --flow-jam: #ff4d5e;
  --accent: #7c9cff;
  /* type */
  --font-ui: "Inter", system-ui, sans-serif; --font-num: "JetBrains Mono", ui-monospace, monospace;
  /* shape */
  --r-sm: 10px; --r-md: 16px; --r-lg: 24px; --blur: blur(18px) saturate(140%);
  /* motion */
  --t-fast: 150ms; --t-med: 320ms; --t-slow: 650ms;
  --ease-out: cubic-bezier(.16,1,.3,1); --ease-in-out: cubic-bezier(.65,0,.35,1);
}
```

Panels are glassy: `background: var(--panel); backdrop-filter: var(--blur); border: 1px solid var(--line); border-radius: var(--r-md)`.

**Road-user colours:** 10 fixed hues that read well on dark and are **never red or blue**, so nobody mistakes
a judge's car for the ambulance:
`#2ee6c8 #9b7bff #ffd166 #ff8fab #7ee081 #4cc9f0 #f4a261 #c77dff #a3e635 #f9c74f`.

### 4.3 Type scale

| Use | Size / weight |
|---|---|
| Takeover heading | 32 / 800, letter-spacing .08em |
| Takeover body | 26 / 600 |
| Card title | 15 / 700 uppercase, .06em |
| Body | 17 / 500 (phone), 18 / 500 (projector) |
| Numbers / plates / timers | JetBrains Mono, tabular |
| Projector headings | 28 / 700. The projector is read from 5 m away, so nothing under 18px |

### 4.4 Alignment and spacing rules

- **8px grid.** All padding/gaps are 8, 16, 24, 32, 48.
- **Phone:** 16px side gutter, single column, no horizontal scroll at 360px width. Primary actions sit in the bottom
  third (thumb zone), at least 56px tall. Text is left-aligned, **except** the takeover (S3), which is centred.
- **Projector:** 32px outer margin, a 24px gap between grid areas, right rail fixed at 440px. Never put text
  over the busy middle of the 3D scene; labels float above objects with a dark pill behind them.
- Numbers right-aligned in tables, tabular figures.
- One primary button per screen.
- Contrast: body text ≥ 4.5:1 on its panel.

---

## 5. Animations (the exact list)

Each item gives the **trigger** (what in the data starts it), **what it looks like**, and **how to build it**.
Durations use the motion tokens. Every animation must have a `prefers-reduced-motion` version (usually: no movement,
instant state change, keep colour).

| # | Name | Screen | Trigger |
|---|---|---|---|
| 5.1 | Smooth car motion | S2, S5 | every data update |
| 5.2 | Join drop-in + ripple | S1→S2, S5 | new road user / join success |
| 5.3 | Living roads (flow) | S2, S5 | congestion band |
| 5.4 | Signal glow + change | S2, S5 | lamp colour change |
| 5.5 | Incident beacon | S2, S5 | new incident / hazard |
| 5.6 | Emergency takeover | S3 | `ev` alert goes live |
| 5.7 | Warning wave | S5 | ambulance declared or route changes |
| 5.8 | Green corridor ribbon | S5 | ambulance active with a route |
| 5.9 | Route redraw | S2, S5 | route changes |
| 5.10 | Card enter/exit/morph | S2 | alert list diff |
| 5.11 | Tick draw (passed) | S3 | `cancelled_passed` |
| 5.12 | Receipt + "data deleted" dissolve | S4 | `arrived` |
| 5.13 | Camera director | S5 | scenario events |
| 5.14 | Count-up tiles | S5, S4 | numbers change |

**5.1 Smooth car motion.** Data arrives every 0.2–1 s, so cars would teleport. Keep `{edge, progress}` as the *target*,
and each animation frame move the *displayed* progress toward it: `shown += (target - shown) * (1 - Math.exp(-dt * 8))`.
If the edge changes, finish the old edge quickly (≤200 ms), then continue on the new one. Rotate the car to the edge
direction. This one detail makes everything feel real-time.

**5.2 Join drop-in.** Phone: after *Start driving*, the plate card shrinks and flies into the map as your car
(GSAP `Flip` is not free-tier, so do it manually: tween the card's `transform` to the car's screen position, then swap).
A ring ripples out from the car (scale 0→3, opacity 1→0, 650 ms). Stage: the new car falls from y=+40 to the road with a
small bounce (`back.out(2)`), a coloured light pillar flashes for 400 ms, and the plate label fades in above it.

**5.3 Living roads.** 2D: each road is an SVG path with an animated `stroke-dasharray` overlay. Dash speed is
fast when free, slow when busy, almost stopped when jammed. The colour lerps between `--flow-*`. 3D: an `InstancedMesh`
of ~30 small glowing particles per road, moving along the edge with speed by band. Particles bunch up when jammed:
reduce spacing toward the stop line.

**5.4 Signal glow.** Lamps are emissive spheres (3D) or circles with a `drop-shadow` filter (2D). On change:
the old colour dims over 150 ms, the new one brightens with a slight overshoot (scale 1→1.25→1). `unknown` = grey
with a slow 2 s breathing opacity and a "?" glyph.

**5.5 Incident beacon.** A map pin drops in (y −30→0, `bounce.out`), then a ring pulses every 1.6 s. Amber for
congestion/hazard, red for a closed road. A closed road also becomes a dashed line with a hatched texture.
On resolve, the pin shrinks to 0 and the road animates back to its flow colour.

**5.6 Emergency takeover.** See S3. Build: an overlay div with
`@keyframes edge { 0%{box-shadow: inset 0 0 0 10px var(--ev-red), inset 0 0 120px var(--ev-red)} 100%{box-shadow: inset 0 0 0 10px var(--ev-blue), inset 0 0 120px var(--ev-blue)} }`,
0.6 s alternate infinite (0.35 s when `imminent`). Entry: the overlay scales 1.04→1 and fades in over 250 ms. The icon gets a
1.2 s "heartbeat" scale. Text never animates letter-by-letter, because it must be readable at once.

**5.7 Warning wave (stage, most important).** When an ambulance starts or its `route_version` changes: from the
ambulance's position, spawn a translucent ring (a `RingGeometry` on the ground plane, additive blending) expanding to
cover the map over 1.8 s. As the ring's radius passes each road-user car:
- if that user is **warned** (has a live `ev:` alert): the car gets a pulsing red/blue halo, and a label pops "WARNED".
- if not: a brief grey ✓ "not affected" label that fades after 1.5 s.
The ring is a visual flourish; the warned/not-warned colours come **only** from the server data, not from the ring's
distance. Animate "the wave reached you" at the moment the ring passes, but colour by the data.

**5.8 Green corridor ribbon.** Along the ambulance's remaining route, draw a glowing ribbon (a flat `TubeGeometry` or
extruded strip slightly above the road) with a scrolling gradient texture (`texture.offset.x -= dt * speed`) running
in the travel direction. Intersections on the route that are holding green for the ambulance get a soft vertical green
light column. When the ambulance passes an intersection, its column collapses downward.

**5.9 Route redraw.** The old route line "dissolves" (dash offset retracts toward the vehicle, 400 ms), then the new line
draws itself from the vehicle forward (`stroke-dashoffset` from length→0, 650 ms, `--ease-out`). Phone: same for
your route. Stage: same for the ambulance ribbon.

**5.10 Cards.** Render from a keyed list (key = `alert.id`). New card: slide up 12px + fade, 320 ms. Removed card:
height collapse + fade, 250 ms. State change on an existing card (e.g. `active → redirected`): a quick flip on the X axis
(rotateX 0→90→0, 300 ms) with the content swapped at 90°. "Done" states are greyed and drop to the bottom.

**5.11 Tick draw.** An SVG check path with `stroke-dasharray = length`, animating `stroke-dashoffset` to 0 over 450 ms,
inside a circle that fills green behind it. A soft green radial glow pulses once.

**5.12 Receipt.** The card flips in (rotateY 90→0, 500 ms). Numbers count up (5.14). Last, the line
"Your session and position have been deleted": the plate above it breaks into ~40 small squares that drift up and fade
(absolutely-positioned divs or a small canvas), then the line types in. Keep it ≤1.5 s total.

**5.13 Camera director (stage).** Idle: a slow orbit (one revolution per ~90 s) at a 45° tilt. On ambulance dispatch:
GSAP-tween the camera over 1.2 s to follow behind and above the ambulance (third-person chase). On arrival: pull back to
the overview. Any keypress or mouse drag hands control to `OrbitControls`. Press `R` to resume the director.
Never move the camera faster than 1.2 s per move, or the projector makes people dizzy.

**5.14 Count-up.** Tween a number from its old to its new value over 600 ms, monospace font so width doesn't jump.

---

## 6. 3D city stage — build guide

### 6.1 Scene

- Renderer: `antialias: true`, `setPixelRatio(Math.min(devicePixelRatio, 2))`, tone mapping `ACESFilmic`.
- Composer: `RenderPass` → `UnrealBloomPass(strength .8, radius .4, threshold .85)`. Only emissive things glow.
- Lights: one dim hemisphere light (sky `#2a3a66`, ground `#05070d`, intensity .6) + one directional "moon" with soft shadows.
- Fog: `scene.fog = new THREE.FogExp2(0x070a12, 0.0016)` for depth.
- Ground: a large dark plane with a subtle grid texture.

### 6.2 Geometry from the topology

`GET /api/public/topology` gives `nodes {id: {x, y, label, kind}}` and `edges [{id, src, dst, approach}]` in **SVG pixel
coordinates**. Map them to 3D: `X = (x - cx) * s`, `Z = (y - cy) * s`, where `(cx, cy)` is the centre of all nodes
and `s` scales the map to ~200 units wide. Use the **same lane offset maths as `dashboard/driver.js` → `drawMap`**
(offset each directed edge sideways by `lane_offset`, pull ends back from intersections) so the 2D and 3D views agree.
Put this maths in **one shared module** (`shared/geometry.js`) used by the phone map, the 2D fallback and the 3D stage.

- Roads: a flat box per directed edge (width ≈ 10 units, height 0.4), colour `--road`, with a dashed centre
  line texture. Flow particles (5.3) ride on top.
- Intersections: a slightly raised disc (`CylinderGeometry`), radius ≈ 18.
- Signal heads: per intersection, a thin pole with three small emissive spheres per axis (A and B). Map the
  lamp colours from `/api/operator/signals` (or `/api/public/signals`). `unknown` = grey.
- External nodes (gates): small glowing gate arches with the label as a sprite.
- **City dressing (cheap wow):** procedurally place 60–120 boxes as buildings in the empty space, skipping
  anything within ~25 units of a road (use a seeded random so the city looks the same every run). Give some building
  tops emissive window textures. Instanced, so this costs almost nothing.
- Vehicles: low-poly. Either simple boxes with a cabin, or the free **Kenney "Car Kit"** (CC0 licence) glTF models.
  Road users use their colour (§4.2). The ambulance is white with **two point lights (red, blue)** alternating every 0.3 s,
  plus a strip of emissive light on top.
- Labels: plates as `CSS2DRenderer` labels (crisp text) or canvas sprites, always facing the camera, dark pill background.

### 6.3 Plate and colour from the pseudonym

The server gives each road user a pseudonym `id` (in the phone's view **and** in `/api/operator/road-users`).
Derive both on the client with the same function on both screens:

```js
// shared/identity.js
export function identity(id) {
  let h = 2166136261;                                // FNV-1a
  for (const c of id) { h ^= c.charCodeAt(0); h = Math.imul(h, 16777619); }
  h >>>= 0;
  const PALETTE = ["#2ee6c8","#9b7bff","#ffd166","#ff8fab","#7ee081","#4cc9f0","#f4a261","#c77dff","#a3e635","#f9c74f"];
  const A = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  const tail = [0,1,2].map(i => A[(h >>> (i * 5)) % A.length]).join("");
  return { color: PALETTE[h % PALETTE.length], plate: `FS·${String(h % 90 + 10)}·${tail}` };
}
```

The judge sees "FS·42·K3F, teal" on their phone and then finds the teal "FS·42·K3F" car on the projector. **No backend change.**

### 6.4 Performance budget

- 60 fps on the presenter laptop at 1080p. Use `InstancedMesh` for buildings and particles. ≤ 150 draw calls.
- Pause rendering when the tab is hidden.
- Measure FPS over a 1 s window. If below 30 for 3 s, drop bloom first, then particles, then fall back to 2D.

### 6.5 Phone "pseudo-3D"

No WebGL on phones. Draw the map in SVG, then tilt the container:
`transform: perspective(900px) rotateX(38deg) scale(1.15); transform-origin: 50% 70%`. Add a radial vignette and a soft
glow under your car. It looks 3D, costs nothing and never crashes.

---

## 7. Code structure

```
dashboard/
  shared/
    tokens.css         # §4.2 design tokens + base reset + glass panel + chips + buttons
    components.css     # cards, chips, tiles, overlay, receipt
    api.js             # fetch wrapper (bearer token, JSON, errors), long-poll helper, polling helper with backoff
    store.js           # tiny state store: get(), set(patch), subscribe(fn); keeps previous state for diffs
    diff.js            # pure functions: alert diff (added / removed / stateChanged), route changed, new users
    geometry.js        # topology → lane-offset segments, pointAt(edge, progress), 3D mapping
    identity.js        # §6.3 plate + colour
    motion.js          # reducedMotion flag, tween helpers, count-up, smoothing (5.1)
    sfx.js             # audio unlock on first tap, chime(), alarm-safe volume
    haptics.js         # vibrate(pattern) with feature detection (no-op on iOS)
  driver/
    main.js            # boot, state machine: join → driving → (takeover) → receipt
    join.js  cockpit.js  takeover.js  receipt.js  map2d.js
  stage/
    main.js            # boot, login, data polling → store
    scene.js           # renderer, composer, lights, fog, resize, FPS guard
    city.js            # roads, intersections, signals, buildings from topology
    vehicles.js        # road-user cars + ambulance, smoothing, labels
    effects.js         # wave (5.7), corridor (5.8), beacons (5.5), flow particles (5.3)
    director.js        # camera moves (5.13)
    fallback2d.js      # SVG top-down if WebGL is unavailable/slow
    rail.js            # narration, checks, provenance, tiles (port of presenter.js)
  vendor/              # three, addons, gsap, fonts. Local copies, no CDN
  driver.html  presenter.html  operator.html  public.html  (+ ambulance.html later)
```

**Patterns to follow:**
1. **Data and animation are separate.** Polling writes plain data to `store`. Renderers subscribe, compute a **diff
   against the previous state**, and fire animations from the diff (`diff.js`). Never start an animation inside a fetch callback.
2. **Render is idempotent.** Calling `render(state)` twice with the same state changes nothing. This makes reconnects
   and reloads safe.
3. **One rAF loop per page** (`requestAnimationFrame`). All smoothing and 3D updates happen there.
4. **Keep URLs.** `driver.html`, `presenter.html`, `operator.html` and `public.html` must keep working at the same paths.
   The demo script and QR codes point at them.
5. **Escape all server strings** before putting them in `innerHTML` (or use `textContent`). The existing `esc()` helper
   in `driver.js` is the pattern.
6. **Storage:** wrap `sessionStorage`/`localStorage` in `try/catch`. Private mode can throw. The driver token already
   lives in `sessionStorage` key `fs_driver`, and the presenter token in `localStorage` key `fs_presenter_token`. Keep both.

---

## 8. Data contract (what each screen reads)

All paths are on the backend at `http://LAPTOP_IP:8000`. Open them in a browser while the simulator runs to see real JSON.

### Public (no auth), used by the phones

| Call | Use | Rate |
|---|---|---|
| `GET /api/public/topology` | map geometry: `nodes`, `edges`, `lane_offset`, capacities | once |
| `GET /api/public/signals` | `{I1: lamps \| "unknown", …}` | 1 s |
| `GET /api/public/congestion` | coarse bands per road | 2 s |
| `POST /api/public/road-users` `{origin, destination}` | join → `{token, …view}`. 429 = too many joins from this address, 503 = demo full | on tap |
| `GET /api/public/road-users/{token}/poll?after={version}&timeout=12` | **long-poll**: returns as soon as your view changes | loop |
| `POST /api/public/road-users/{token}/route/accept` | take the suggested route | on tap |
| `DELETE /api/public/road-users/{token}` | leave | on tap |
| `GET /api/public/warnings?approach=L12` | roadside sign (S8): `{warning: {text, eta_band} \| null}` | 500 ms |

The road-user **view** (what join and poll return):
```json
{ "id": "…pseudonym…", "status": "driving|arrived", "edge": "L13", "progress": 0.42,
  "from": "West Gate", "to": "South Gate", "route_text": "…",
  "alerts": [ { "id": "ev:3", "kind": "ev|route|incident|hazard",
                "state": "active|updated|redirected|cancelled_passed|cancelled_route_change|cancelled_stale|expired",
                "text": "…fixed template, show verbatim…", "band": "approaching|imminent|null" } ],
  "suggestion": { "text": "…", "saves_s": 40 } | null,
  "version": 17 }
```
Live states are `active`, `updated`, `redirected`. A 404 from poll means the session ended: show S4 if you saw
`arrived`, otherwise a "session ended" screen.

### Restricted (bearer token), used by the presenter / operator

Login: `POST /api/auth/login {username, password}` → `{token}`. Send `Authorization: Bearer <token>`. A 401 means show the login.

| Call | Use | Rate |
|---|---|---|
| `GET /api/operator/overview` | everything in one call: traffic, signals, ev, incidents, road_users, hazards, scenario, provenance, events… | 1 s |
| `GET /api/operator/road-users` | `[{id, simulated, status, edge, progress, alerts: {alertId: state}}]`. A user is warned if a key starts with `ev:` and its state is live | in overview |
| `GET /api/operator/ev` | `[{id, vehicle_id, status, edge, progress, stale, route, route_version, warnings}]` | in overview |
| `GET /api/operator/scenario` | title, state, t, duration, current step (narration, cue), steps with checks, summary | 1 s |
| `GET /api/operator/provenance` | built-vs-simulated rows `[{component, status}]` | 2 s |

Prefer **one `overview` call per second** on the stage over many small calls.

---

## 9. Needs backend work (talk to the backend owner first, don't block on these)

| Item | Why | Size |
|---|---|---|
| Coarse `direction` (e.g. `"W"`) on public `ev` alerts | direction arrow in S3. Allowed public info, unlike position | small |
| Signal "time to next change" in a coarse public form | a signal countdown on the cockpit (must not pressure drivers, R3) | medium, optional |
| Public hazard report from the phone (`POST /api/public/hazards` exists; confirm its body, add photo upload if wanted) | "Report hazard" button | small–medium |
| Ambulance-driver endpoint/view | S6 | medium |

Everything in §3 marked P0 and P1 works **without** any of these.

---

## 10. Build order (with checkpoints)

1. **Foundation (½ day):** `vendor/`, `tokens.css`, `components.css`, `api.js`, `store.js`, `geometry.js`, `identity.js`.
   ✔ The old driver page restyled with tokens still works end-to-end.
2. **Phone P0:** S1 join with `?from=`, S2 cockpit with smoothing (5.1), cards (5.10), S3 takeover (5.6, 5.11).
   ✔ Run the demo scenario. Two phones on different roads: only the one on the corridor takes over.
3. **Stage P0:** scene + city + vehicles + rail. Then wave (5.7) and corridor (5.8).
   ✔ Six phones joined. Each judge finds their own plate on the projector. The warned/not-warned counts match the phones.
4. **P1:** receipt (S4), route redraw (5.9), beacons (5.5), living roads (5.3), camera director (5.13), count-ups.
5. **P2:** operator restyle + assistant trace, approach sign, ambulance view.
6. **Freeze 2 h before judging.** Only bug fixes after that.

---

## 11. Test checklist before the demo

- [ ] **6+ phones at once** (mix Android and iPhone) on the venue router. The long-poll keeps up and nothing freezes.
- [ ] Laptop **offline** (no internet): every page still loads (vendor files, fonts).
- [ ] Phones reach `http://LAPTOP_IP:8000` (put the real IP in the QR codes. Regenerate them if the IP changes).
- [ ] Audio plays on iPhone after the *Start driving* tap. Vibration on Android (iPhone: no vibration, expected).
- [ ] Screen stays readable in a bright room (projector contrast; increase `--panel` opacity if washed out).
- [ ] `prefers-reduced-motion` on: no pulsing or flying, everything still understandable.
- [ ] Kill the backend for 10 s: phones show the amber "reconnecting" dot, recover on their own. The stage shows stale.
- [ ] Run the four failure drills from `docs/DEMO_SCRIPT.md`: lamps go grey "?" when unknown, never green (R4).
- [ ] Grep your JS: **no hard-coded warning sentences** (R2). Every warning string on screen comes from the server.
- [ ] Phone never shows the ambulance's position or other cars (R1). Check the network tab too: phones only call `/api/public/*`.
- [ ] Stage at 1080p holds ≥ 50 fps for a full scenario. The 2D fallback works with WebGL disabled.
- [ ] Portrait phone at 360px width: no horizontal scroll, buttons ≥ 56px.
- [ ] Rotating the phone or locking/unlocking it mid-trip doesn't lose the session (token in `sessionStorage`).

---

## 12. What "done" looks like

A judge scans, taps once, sees their teal car on the projector, watches the ambulance wave pass over the city,
feels their phone go red and blue (or sees it stay calm because they're not on the route), watches the route flip
and someone else's phone light up, gets a green tick, then a receipt saying their data is gone. All in under three
minutes, with the lamps on the physical table changing at the same moment.
