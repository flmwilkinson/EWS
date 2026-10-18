"""python -m server  -> Early Warning Studio on http://127.0.0.1:8765"""
import os

# OpenBLAS commits a buffer per CPU thread on import (over 1 GB on a 20-core machine). Four threads is plenty here,
# and kernel subprocesses inherit the setting.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "4")

import sys
import warnings

import uvicorn

from server.config import HOST, PORT

if __name__ == "__main__":
    warnings.filterwarnings("ignore")
    if sys.platform == "win32":  # subprocess-friendly event loop
        import asyncio
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    print(f"\n  Early Warning Studio is running at http://{HOST}:{PORT}\n")
    uvicorn.run("server.app:app", host=HOST, port=PORT, log_level="warning", reload=False)
