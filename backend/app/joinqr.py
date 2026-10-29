"""Print the demo URLs for this machine and write a printable QR sheet.

The laptop's address changes whenever the phone hotspot restarts, so run this each time before the demo:

    python -m app.joinqr                      # auto-detect the LAN address
    python -m app.joinqr --host 192.168.43.5  # or give it explicitly

It writes data/qr/join.html (open it and print it; it needs no internet) with one QR code per page
and per entrance gate. The gate is passed as ?gate=<id> for the phone page to use.
Needs the optional `qrcode` package (requirements-demo.txt).
"""
import argparse
import html
from pathlib import Path

from app.config import ROOT, Settings, load_yaml
from app.preflight import lan_addresses


def _svg(url: str) -> str:
    import io

    import qrcode
    import qrcode.image.svg

    img = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode("utf-8")


def gates(topology_path: str) -> list:
    layout = (load_yaml(topology_path) or {}).get("layout", {})
    return [k for k in layout if k.startswith("ext_")]


def build(host: str, port: int, topology_path: str) -> list:
    base = f"http://{host}:{port}"
    items = [("Join as a driver", f"{base}/driver.html"), ("Big screen (presenter)", f"{base}/presenter.html"),
             ("Operator", f"{base}/operator.html")]
    items += [(f"Driver, entering at {g}", f"{base}/driver.html?gate={g}") for g in gates(topology_path)]
    return items


def write_sheet(items: list, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    cards = "".join(
        f"<div class=c><h2>{html.escape(t)}</h2>{_svg(u)}<p>{html.escape(u)}</p></div>" for t, u in items)
    out.write_text(
        "<!doctype html><meta charset=utf-8><title>FlowSync join codes</title>"
        "<style>body{font-family:sans-serif}.c{display:inline-block;width:260px;margin:12px;text-align:center;"
        "page-break-inside:avoid}svg{width:240px;height:240px}p{font-size:11px;word-break:break-all}</style>" + cards,
        encoding="utf-8")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", help="laptop address on the hotspot (default: first detected LAN address)")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    host = a.host or next(iter(lan_addresses()), None)
    if not host:
        raise SystemExit("No LAN address found. Connect to the hotspot first, or pass --host.")
    items = build(host, a.port, Settings().topology_path)
    for t, u in items:
        print(f"{t:32s} {u}")
    try:
        out = write_sheet(items, ROOT / "data" / "qr" / "join.html")
    except ImportError:
        raise SystemExit("\nInstall the QR package to write the sheet:  pip install -r requirements-demo.txt")
    print(f"\nPrintable QR sheet: {out}")
    print("Firmware config.h BACKEND_HOST should be:", host)


if __name__ == "__main__":
    main()
