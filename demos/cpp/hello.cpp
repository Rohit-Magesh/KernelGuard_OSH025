#include <iostream>
#include <vector>
#include <numeric>
int main() {
    std::vector<int> v(100);
    std::iota(v.begin(), v.end(), 1);
    std::cout << "Hello from C++17 in the sandbox, sum 1..100 = " << std::accumulate(v.begin(), v.end(), 0) << std::endl;
    return 0;
}
