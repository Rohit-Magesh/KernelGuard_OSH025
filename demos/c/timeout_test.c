/* Harmless: just sleeps and prints for 120 seconds (longer than every profile's limit). */
#include <stdio.h>
#include <unistd.h>
int main(void) {
    for (int i = 1; i <= 120; i++) { printf("tick %d\n", i); fflush(stdout); sleep(1); }
    return 0;
}
