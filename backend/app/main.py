import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable, Optional

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles

from . import auth
from .api import device, llm as llm_api, operator, public
from .audit import event
from .config import ROOT, Settings, load_yaml
from .core.controller import Controller
from .core.ev import EVManager
from .core.incidents import IncidentManager
from .core.proposals import ProposalManager
from .llm.client import LLMClient
from .core.store import Store
from .core.topology import Topology
from .db import Database, now_iso
from .security import DeviceAuthenticator


class Context:
    def __init__(self, settings: Settings, clock: Optional[Callable[[], float]] = None):
        self.settings = settings
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
        self.device_seen = {}
        self.login_failures = {}
        auth.seed_operator(self.db, settings.operator_password)


def create_app(settings: Optional[Settings] = None, clock: Optional[Callable[[], float]] = None) -> FastAPI:
    settings = settings or Settings()
    ctx = Context(settings, clock)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        event(ctx.db, "backend_started", "backend")
        task = None
        if settings.controller_autostart:
            async def loop():
                while True:
                    await asyncio.sleep(ctx.topology.control.interval_s)
                    try:
                        await run_in_threadpool(ctx.incidents.tick)
                        await run_in_threadpool(ctx.ev.tick)
                        await run_in_threadpool(ctx.controller.cycle)
                    except Exception as e:  # keep the loop alive; surface the fault
                        event(ctx.db, "controller_error", "controller", "error", {"error": repr(e)})
            task = asyncio.create_task(loop())
        yield
        if task:
            task.cancel()

    app = FastAPI(title="FlowSync AI", lifespan=lifespan)
    app.state.ctx = ctx
    app.include_router(public.router)
    app.include_router(device.router)
    app.include_router(operator.router)
    app.include_router(llm_api.router)
    dash = Path(ROOT) / "dashboard"
    if dash.exists():
        app.mount("/", StaticFiles(directory=str(dash), html=True), name="dashboard")
    return app


def app_factory() -> FastAPI:
    return create_app()
