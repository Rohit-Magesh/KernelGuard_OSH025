/* A real, UNBOUNDED fork bomb. It is only safe because the container's pids cgroup caps the task count:
   expect PROCESS_LIMIT (fork denied, hundreds of thousands of times) + CPU_LIMIT, ended by the TIME_LIMIT watchdog. */
#include <unistd.h>
int main() {
    while (true) {
        fork();
    }
    return 0;
}
