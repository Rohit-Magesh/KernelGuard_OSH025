/* Hostile-but-bounded: creates children until the PID limit refuses, then burns CPU until the runtime deadline.
   Expected on SAFE: PROCESS_LIMIT (fork denied) + TIME_LIMIT (terminated) + CPU_LIMIT (throttled) in one run. */
#include <stdio.h>
#include <unistd.h>
#include <sys/wait.h>
#define MAX_CHILDREN 40            /* hard bound - not a fork bomb */
int main(void) {
    int ok = 0, denied = 0;
    for (int i = 0; i < MAX_CHILDREN; i++) {
        pid_t p = fork();
        if (p == 0) { sleep(60); _exit(0); }
        if (p < 0) denied++; else ok++;
    }
    printf("children created: %d, denied: %d - now spinning\n", ok, denied);
    fflush(stdout);
    volatile unsigned long x = 0;
    for (;;) x++;
}