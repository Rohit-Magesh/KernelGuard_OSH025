# C4 - trusted fixture: sleeps for 30 s so supervision (not the program) decides how the job ends.
import time
print("sleeper started", flush=True)
for i in range(30):
    time.sleep(1)
print("sleeper finished", flush=True)