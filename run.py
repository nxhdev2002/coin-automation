import asyncio
import sys
import warnings

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
