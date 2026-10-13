"""Allocates ~50 MB per step until the memory budget is exhausted (kernel OOM kill)."""
import time
blocks = []
for i in range(1, 100):
    blocks.append(bytearray(50 * 1024 * 1024))
    for j in range(0, len(blocks[-1]), 4096):
        blocks[-1][j] = 1                      # touch every page so it is really resident
    print(f"allocated {i * 50} MB", flush=True)
    time.sleep(0.05)