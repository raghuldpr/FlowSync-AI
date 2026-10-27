"""One call that answers 'is the system fit to demo right now?' - used by the dashboard, the preflight tool and the presenter."""
import os

DEFAULT_OPERATOR_PASSWORDS = ("operator-dev", "viewer-dev", "admin-dev")


def _c(name, status, detail):
    return {"name": name, "status": status, "detail": detail}        # status: ok | warn | fail


def report(ctx) -> dict:
    t = ctx.store.clock()
    checks = []
    try:
        ctx.db.one("SELECT 1")
        checks.append(_c("database", "ok", "reachable"))
    except Exception as e:
        checks.append(_c("database", "fail", type(e).__name__))

    if ctx.controller.last_cycle_t is None:
        checks.append(_c("control loop", "warn", "no cycle has run yet"))
    else:
        age = t - ctx.controller.last_cycle_t
        checks.append(_c("control loop", "ok" if age < 5 else "fail", f"last cycle {age:.1f}s ago"))

    for dev, spec in ctx.device_specs.items():
        seen = ctx.device_seen.get(dev)
        if spec["role"] in ("intersection_controller", "camera"):
            if seen is None:
                checks.append(_c(f"device {dev}", "warn", "never seen"))
            else:
                age = t - seen
                checks.append(_c(f"device {dev}", "ok" if age < 5 else "fail", f"last seen {age:.1f}s ago"))

    st = ctx.llm.status()
    checks.append(_c("llm", "ok" if st["enabled"] else "warn",
                     f"{st['model']} via {st['provider_host']}" if st["enabled"] else "not configured: deterministic fallback in use (labelled)"))

    try:
        os.makedirs(ctx.settings.image_dir, exist_ok=True)
        probe = os.path.join(ctx.settings.image_dir, ".probe")
        open(probe, "w").close(); os.remove(probe)
        checks.append(_c("image storage", "ok", "writable"))
    except OSError:
        checks.append(_c("image storage", "warn", "not writable; road-camera uploads would fail"))

    weak = [u for u, p in (("operator", ctx.settings.operator_password), ("viewer", ctx.settings.viewer_password),
                           ("admin", ctx.settings.admin_password)) if p in DEFAULT_OPERATOR_PASSWORDS]
    keys = [d for d, s in ctx.device_specs.items() if "change-me" in s["key"]]
    if weak or keys:
        checks.append(_c("credentials", "warn", f"development defaults in use (accounts: {', '.join(weak) or 'none'}; device keys: {len(keys)}). Change before an open demo"))
    else:
        checks.append(_c("credentials", "ok", "no development defaults"))

    ok = not any(c["status"] == "fail" for c in checks)
    return {"ok": ok, "warnings": sum(c["status"] == "warn" for c in checks), "checks": checks}
