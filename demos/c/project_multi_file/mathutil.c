#include "mathutil.h"
int gcd(int a, int b) { return b == 0 ? a : gcd(b, a % b); }
long factorial(int n) { return n <= 1 ? 1 : n * factorial(n - 1); }