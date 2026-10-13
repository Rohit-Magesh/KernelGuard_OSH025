/* Calls ptrace(), which the sandbox's seccomp profile kills with SIGSYS (exit status 159). */
#include <stdio.h>
#include <sys/ptrace.h>
int main(void) {
    puts("about to call ptrace()..."); fflush(stdout);
    long r = ptrace(PTRACE_TRACEME, 0, 0, 0);
    printf("ptrace returned %ld (not blocked!)\n", r);
    return 0;
}