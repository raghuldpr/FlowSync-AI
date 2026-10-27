"""Synthetic overhead camera for the virtual tabletop: renders the simulator's queues (and the EV) with the
synthetic camera, runs the REAL vision pipeline on that image, and posts what the pipeline measured.

Use with VirtualTabletop(external_camera=True) so the counts the backend sees come from perception, not from the
simulator's internals. It is a stand-in for the phone: real lighting and optics will be harder.
"""
import simulator  # noqa: F401
from app.vision.pipeline import VisionPipeline
from app.vision.poster import Poster
from app.vision.synthetic import SyntheticTable


class SyntheticCamera:
    def __init__(self, sim, client, devices: dict, **render_kw):
        self.sim = sim
        self.table = SyntheticTable(sim.topo)
        self.pipe = VisionPipeline(sim.topo)
        self.render_kw = render_kw
        empty, _ = self.table.render({}, **render_kw)
        assert self.pipe.capture_reference([empty] * 3), "synthetic reference calibration failed"
        self.poster = Poster(sim.cam, "simulated", self.pipe.cfg.post_hz, log=lambda *_: None)
        self.ev = None                 # {"edge":..., "progress":...} ground truth for the rendered EV marker
        self.last_truth = {}
        self.last_obs = None

    def set_ev(self, edge, progress):
        self.ev = {"edge": edge, "progress": progress}

    def step(self):
        if not self.sim.camera_on:                 # failure drill: the feed is dead, nothing is posted
            return None
        now = self.sim.now_ms / 1000
        img, self.last_truth = self.table.render(dict(self.sim.queues), self.ev, **self.render_kw)
        self.last_obs = self.pipe.process(img, now)
        self.poster.handle(self.last_obs, now)
        return self.last_obs
