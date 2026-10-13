/* Harmless bounded CPU burner: ~8 seconds of floating-point work, then exits. */
#include <stdio.h>
#include <math.h>
#include <time.h>
int main(void) {
    time_t end = time(NULL) + 8;
    volatile double x = 0; long rounds = 0;
    while (time(NULL) < end) {
        for (int i = 1; i < 2000000; i++) x += sqrt((double)i) * sin((double)i);
        rounds++;
        if (rounds % 20 == 0) { printf("round %ld\n", rounds); fflush(stdout); }
    }
    printf("done, checksum %f\n", x);
    return 0;
}
