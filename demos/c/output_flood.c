/* Prints without bound; the sandbox stops it at the output cap (OUTPUT_LIMIT). */
#include <stdio.h>
int main(void) {
    for (unsigned long i = 0; ; i++) printf("line %lu: the quick brown fox jumps over the lazy dog\n", i);
}