/* Compiles fine, then crashes with a segmentation fault (exit 139). */
#include <stdio.h>
int main(void) {
    int *p = 0;
    puts("about to dereference NULL"); fflush(stdout);
    *p = 42;
    return 0;
}