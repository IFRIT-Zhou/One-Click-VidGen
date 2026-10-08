"""Run unmodified ComfyUI; shut down if the owning OCV process disappears."""
import os
from pathlib import Path
import runpy
import sys
import threading
import time


def main():
    import psutil
    parent = psutil.Process(int(sys.argv[1]))
    born = parent.create_time()
    root = Path(sys.argv[2]).resolve()

    def watch():
        while True:
            time.sleep(3)
            try:
                alive = parent.is_running() and parent.create_time() == born
            except psutil.Error:
                alive = False
            if not alive:
                os._exit(0)

    threading.Thread(target=watch, daemon=True).start()
    sys.argv = [str(root / "main.py"), *sys.argv[3:]]
    sys.path.insert(0, str(root))
    runpy.run_path(str(root / "main.py"), run_name="__main__")


if __name__ == "__main__":
    main()
