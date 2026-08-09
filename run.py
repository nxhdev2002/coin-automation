import asyncio
import sys
import warnings

def patch_nodriver():
    try:
        import nodriver
        p = nodriver.cdp.network.__file__
        with open(p, 'rb') as f:
            content = f.read()
        if b'\xb1Inf' in content:
            content = content.replace(b'\xb1Inf', b'+/-Inf')
            with open(p, 'wb') as f:
                f.write(content)
            print("Patched nodriver network.py (non-UTF-8 char)")
    except Exception as e:
        print(f"nodriver patch skipped: {e}")

patch_nodriver()

if sys.platform == "win32":
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    loop = asyncio.ProactorEventLoop()
    asyncio.set_event_loop(loop)
    print(f"Event loop type: {type(loop).__name__}")

    import uvicorn
    config = uvicorn.Config("src.main:app", host="0.0.0.0", port=8000, loop="none")
    server = uvicorn.Server(config)
    loop.run_until_complete(server.serve())
else:
    import uvicorn
    uvicorn.run("src.main:app", host="0.0.0.0", port=8000)
