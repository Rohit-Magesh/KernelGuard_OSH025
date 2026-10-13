/* C3 - trusted fixture: attempts AT MOST 40 short-lived children (bounded - not a fork bomb).
   Under pids.max=20 the kernel refuses part of the requests with EAGAIN; the program notes it and carries on. */
#include <stdio.h>
#include <unistd.h>
#include <sys/wait.h>
#define ATTEMPTS 40
int main(void) {
    int created = 0, refused = 0;
    for (int i = 0; i < ATTEMPTS; i++) {
        pid_t p = fork();
        if (p == 0) { sleep(2); _exit(0); }      /* short-lived child */
        if (p < 0) refused++; else created++;
    }
    printf("attempted=%d created=%d refused=%d\n", ATTEMPTS, created, refused);
    fflush(stdout);
    while (wait(NULL) > 0) {}
    puts("all children reaped; workload completed");
    return 0;
}