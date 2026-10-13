/* Allocates and touches memory in 10 MB steps up to TARGET_MB. Edit TARGET_MB to test limits. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#define TARGET_MB 1024
int main(void) {
    static char *blocks[1024]; int n = 0;
    for (int mb = 10; mb <= TARGET_MB; mb += 10) {
        char *p = malloc(10 * 1024 * 1024);
        if (!p) { printf("malloc failed at %d MB\n", mb); return 2; }
        memset(p, 1, 10 * 1024 * 1024);
        blocks[n++] = p;
        printf("allocated %d MB\n", mb); fflush(stdout);
        usleep(50000);
    }
    printf("allocated %d MB successfully\n", TARGET_MB);
    return 0;
}
