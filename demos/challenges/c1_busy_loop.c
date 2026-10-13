/* C1 - trusted fixture: controlled busy loop. It never exits by itself; the watchdog must end it. */
#include <stdio.h>
int main(void) {
    puts("busy loop started"); fflush(stdout);
    volatile unsigned long x = 0;
    for (;;) x++;
}