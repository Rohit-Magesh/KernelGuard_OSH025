/* Bounded: forks NUM_CHILDREN (hard max 40) children that sleep 3s. NOT a fork bomb. */
#include <stdio.h>
#include <unistd.h>
#include <sys/wait.h>
#define NUM_CHILDREN 30
#if NUM_CHILDREN > 40
#error "NUM_CHILDREN must be <= 40"
#endif
int main(void) {
    int ok = 0, failed = 0;
    for (int i = 0; i < NUM_CHILDREN; i++) {
        pid_t p = fork();
        if (p == 0) { sleep(3); _exit(0); }
        if (p < 0) { failed++; printf("fork %d failed (process limit reached)\n", i + 1); fflush(stdout); }
        else ok++;
    }
    printf("created %d children, %d forks refused\n", ok, failed); fflush(stdout);
    while (wait(NULL) > 0) {}
    return failed ? 1 : 0;
}
