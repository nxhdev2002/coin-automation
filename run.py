import asyncio
import os
import sys
import warnings
import traceback

def patch_nodriver():
    import site
    for sp in site.getsitepackages():
        p = os.path.join(sp, "nodriver", "cdp", "network.py")
        if not os.path.exists(p):
            continue
        with open(p, "rb") as f:
            content = f.read()
        if b'\xb1Inf' in content:
            content = content.replace(b'\xb1Inf', b'+/-Inf')
            with open(p, "wb") as f:
                f.write(content)
            print(f"Patched nodriver network.py: {p}")
        return
    print("nodriver network.py not found, skipping patch")

patch_nodriver()

try:
    if sys.platform == "win32":
        warnings.filterwarnings("ignore", category=DeprecationWarning)
        loop = asyncio.ProactorEventLoop()
        asyncio.set_event_loop(loop)

        import uvicorn
        config = uvicorn.Config("src.main:app", host="0.0.0.0", port=8000, loop="none")
        server = uvicorn.Server(config)
        loop.run_until_complete(server.serve())
    else:
        import uvicorn
        uvicorn.run("src.main:app", host="0.0.0.0", port=8000)
except Exception:
    log_path = os.path.join(os.path.dirname(__file__), "logs", "startup_error.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "w") as f:
        f.write(traceback.format_exc())
    raise
