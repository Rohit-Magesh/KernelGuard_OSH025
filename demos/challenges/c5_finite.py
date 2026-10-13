# C5 - trusted fixture: a finite job that holds its slot for ~5 s.
import time
print("c5 job running", flush=True)
time.sleep(5)
print("c5 job done", flush=True)