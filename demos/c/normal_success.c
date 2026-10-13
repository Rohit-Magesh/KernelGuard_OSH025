#include <stdio.h>
int main(void) {
    long sum = 0;
    for (int i = 1; i <= 1000; i++) sum += i;
    printf("Hello from the sandbox! Sum 1..1000 = %ld\n", sum);
    return 0;
}
