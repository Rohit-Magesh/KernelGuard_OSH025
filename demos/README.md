# Demo programs

Load them from the **Dashboard -> Load a demo program** list (or upload the files yourself). `catalog.json` holds the descriptions and the
recommended profile. All programs are bounded or contained by the sandbox limits.

| Demo | Profile | Expected result |
|---|---|---|
| c/normal_success.c | Standard | COMPLETED, `Sum 1..1000 = 500500` |
| c/project_multi_file/ | Standard | COMPLETED - 3 files compiled together |
| c/project_two_programs/ | Standard | asks you to pick `hello_tool.c` or `sum_tool.c`; only that program is built |
| c/cpu_stress.c | Safe | COMPLETED, CPU_LIMIT (throttled by the 0.5 CPU quota) |
| c/memory_test.c, python/memory_hog.py | Safe | TERMINATED - MEMORY LIMIT (kernel OOM kill, confirmed) |
| c/timeout_test.c | Safe | TERMINATED - TIME LIMIT at 10 s |
| c/process_test.c | Safe | RUNTIME ERROR + PROCESS_LIMIT (forks denied after ~17) |
| c/multi_violation.c | Safe | TERMINATED - TIME LIMIT with PROCESS_LIMIT and CPU_LIMIT recorded as well |
| c/seccomp_test.c | Standard | TERMINATED - SECURITY POLICY (ptrace killed by seccomp) |
| c/output_flood.c | Standard | TERMINATED - OUTPUT LIMIT |
| c/compile_error.c | Standard | COMPILATION FAILED |
| c/runtime_error.c | Standard | RUNTIME ERROR (SIGSEGV, exit 139) |
| cpp/hello.cpp | Standard | COMPLETED |
| cpp/fork_bomb.cpp | Safe | an UNBOUNDED fork bomb, contained by the PID cgroup: TIME LIMIT + PROCESS_LIMIT + CPU_LIMIT |
| python/hello.py | Standard | COMPLETED (shows uid 1000 and blocked file access) |
| java/Main.java, java/project/ | Standard | COMPLETED (Safe's 20 PIDs is tight for the JVM) |
| challenges/* | - | trusted fixtures used by the official challenge tests |