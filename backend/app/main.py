import asyncio
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable, Optional

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import auth
from .api import admin as admin_api, device, hazards as hazards_api, llm as llm_api, operator, public, roadusers as roadusers_api
from .audit import event
from . import retention
from .config import ROOT, Settings, load_yaml
from .core.controller import Controller
from .core.ev import EVManager
from .core.incidents import IncidentManager
from .core.hazards import HazardManager
from .core.roaduser import RoadUserManager
from .core.simbus import SimBus
from .metrics import Metrics
from .ratelimit import RateLimiter
from .scenarios import ScenarioRunner
from .core.proposals import ProposalManager
from .llm.client import LLMClient
from .core.store import Store
from .core.topology import Topology
from .db import Database, now_iso
from .deps import client_id
from .health import default_credentials
from .security import DeviceAuthenticator


MAX_BODY = 1_000_000          # bytes (the largest legitimate body is a ~530 KB base64 road-camera frame)
PUBLIC_GET_PER_MIN = 3000     # per address; venue Wi-Fi puts many phones behind one address, so this is deliberately generous


class Context:
    def __init__(self, settings: Settings, clock: Optional[Callable[[], float]] = None):
        self.settings = settings
        self.instance_id = secrets.token_hex(4)      # new on every process start: two different values = two instances
        if settings.db_path != ":memory:":
            Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)     # a hosted app starts from an empty writable folder
        self.db = Database(settings.db_path)
        self.topology = Topology.load(settings.topology_path)
        self.device_specs = load_yaml(settings.devices_path)["devices"]
        self.device_auth = DeviceAuthenticator(self.device_specs)
        self.store = Store(clock) if clock else Store()
        self.intersection_device = {i: d for d, s in self.device_specs.items()
                                    if s["role"] == "intersection_controller" for i in s.get("intersections", [])}
        self.controller = Controller(self.topology, self.store, self.db, self.intersection_device)
        self.incidents = IncidentManager(self.topology, self.store, self.db)
        self.ev = EVManager(self.topology, self.store, self.db)
        self.controller.extra_emergency = self.ev.priority_approaches
        self.proposals = ProposalManager(self.topology, self.store, self.db, self.controller)
        self.llm = LLMClient(settings)
        self.db.execute("UPDATE ev_sessions SET status='ended', ended_at=?, end_reason='backend_restart' "
                        "WHERE status='active'", (now_iso(),))
        self.rate = RateLimiter(self.store.clock)
        self.roadusers = RoadUserManager(self.topology, self.store, self.ev)
        self.hazards = HazardManager(self.topology, self.store, self.db)
        self.roadusers.event_sources.append(self.incidents.road_events)
        self.roadusers.event_sources.append(self.hazards.road_events)
        self.sim_bus = SimBus(self.store.clock)
        self.prov = {}                    # what each data path last reported (origin/source, time) -> live built-vs-simulated
        self.scenarios = ScenarioRunner(self)
        self.device_seen = {}
        self.login_failures = {}
        auth.seed_users(self.db, {"viewer": settings.viewer_password, "operator": settings.operator_password, "admin": settings.admin_password})
        self.metrics = Metrics()
        self.roadusers.metrics = self.metrics
        self.controller.metrics = self.metrics


def create_app(settings: Optional[Settings] = None, clock: Optional[Callable[[], float]] = None) -> FastAPI:
    settings = settings or Settings()
    ctx = Context(settings, clock)
    hosted = bool(settings.public_url or os.getenv("X_ZOHO_CATALYST_LISTEN_PORT"))
    if hosted:                                  # a public address or a Catalyst host means the open internet: never with development credentials
        weak, keys = default_credentials(settings, ctx.device_specs)
        if weak or keys:
            raise RuntimeError("refusing to start on a public host (FLOWSYNC_PUBLIC_URL set or running on Catalyst) while development credentials are in use "
                               f"(accounts: {', '.join(weak) or 'none'}; device keys: {len(keys)}). "
                               "Create private ones with: python -m app.setup_demo")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        event(ctx.db, "backend_started", "backend")
        task, ticks = None, [0]
        if settings.controller_autostart:
            async def loop():
                while True:
                    await asyncio.sleep(ctx.topology.control.interval_s)
                    try:
                        await run_in_threadpool(ctx.scenarios.tick)
                        await run_in_threadpool(ctx.incidents.tick)
                        await run_in_threadpool(ctx.ev.tick)
                        await run_in_threadpool(ctx.roadusers.tick)
                        await run_in_threadpool(ctx.controller.cycle)
                        ticks[0] += 1
                        if ticks[0] % 300 == 0:
                            await run_in_threadpool(retention.run, ctx)
                    except Exception as e:  # keep the loop alive; surface the fault
                        event(ctx.db, "controller_error", "controller", "error", {"error": repr(e)})
            task = asyncio.create_task(loop())
        yield
        if task:
            task.cancel()

    app = FastAPI(title="FlowSync AI", lifespan=lifespan)
    app.state.ctx = ctx

    @app.middleware("http")
    async def guard(request, call_next):
        """Body-size cap, a generous per-address limit on public reads, and per-route latency metrics."""
        t0 = time.perf_counter()
        try:
            too_big = request.method in ("POST", "PUT", "PATCH") and int(request.headers.get("content-length") or 0) > MAX_BODY
        except ValueError:
            too_big = True
        if too_big:
            return JSONResponse({"detail": "request body too large"}, status_code=413)
        if request.method == "GET" and request.url.path.startswith("/api/public/") and \
                not ctx.rate.allow("pub:" + client_id(request), PUBLIC_GET_PER_MIN, 60):
            return JSONResponse({"detail": "too many requests"}, status_code=429)
        resp = await call_next(request)
        route = request.scope.get("route")
        path = route.path if route else "unmatched"
        ctx.metrics.observe(f"http {request.method} {path}", (time.perf_counter() - t0) * 1000.0)
        ctx.metrics.count(f"http_{resp.status_code // 100}xx")
        return resp
    @app.get("/api/whoami")
    def whoami(request: Request):
        """What the server sees: the client address its rate limits use, and this process's id. Used to check proxy settings
        (the address must be yours, not the host's proxy) and that a hosted app runs as ONE instance (the id never changes)."""
        return {"client_address": client_id(request), "trusted_proxy_hops": settings.trust_proxy_hops, "instance": ctx.instance_id}

    app.include_router(public.router)
    app.include_router(device.router)
    app.include_router(operator.router)
    app.include_router(llm_api.router)
    app.include_router(roadusers_api.router)
    app.include_router(hazards_api.router)
    app.include_router(admin_api.router)
    dash = Path(ROOT) / "dashboard"
    screen = Path(ROOT) / "web" / "dist"      # the 3D big screen, built with base /screen/ (deploy/catalyst/build.ps1 or `npm run build`)
    if screen.exists():
        app.mount("/screen", StaticFiles(directory=str(screen), html=True), name="screen")      # before "/", which would swallow it
    if dash.exists():
        app.mount("/", StaticFiles(directory=str(dash), html=True), name="dashboard")
    return app


def app_factory() -> FastAPI:
    return create_app()
