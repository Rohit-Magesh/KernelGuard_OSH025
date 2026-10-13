/* kg-probe: KernelGuard in-container telemetry helper.
 *
 * Static, single-threaded and fork-free: it only open()/read()s kernel files, so it keeps working even when the
 * container's pids cgroup is saturated by the workload (a `docker exec cat ...` could not start in that case).
 * It runs as an unprivileged *different* uid (1001) so the untrusted workload (uid 1000) cannot signal it.
 *
 * Output: one JSON object per line on stdout.
 *   {"type":"hello",...}  once  - what the kernel actually applied to this container (verification, not assumption)
 *   {"type":"s",...}      every interval - cgroup v2 counters
 */
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define CG "/sys/fs/cgroup/"

static int slurp(const char *path, char *buf, size_t n) {
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return -1;
    ssize_t r = read(fd, buf, n - 1);
    close(fd);
    if (r < 0) return -1;
    buf[r] = 0;
    return (int)r;
}

/* single-number file; "max" -> -2, unreadable -> -1 */
static long long num(const char *name) {
    char p[160], b[128];
    snprintf(p, sizeof p, CG "%s", name);
    if (slurp(p, b, sizeof b) < 0) return -1;
    if (strncmp(b, "max", 3) == 0) return -2;
    return atoll(b);
}

/* "key value" lines (memory.events, pids.events, cpu.stat, memory.stat) */
static long long kv(const char *name, const char *key) {
    char p[160], b[4096];
    snprintf(p, sizeof p, CG "%s", name);
    if (slurp(p, b, sizeof b) < 0) return -1;
    size_t kl = strlen(key);
    for (char *l = b; l && *l;) {
        if (strncmp(l, key, kl) == 0 && l[kl] == ' ') return atoll(l + kl + 1);
        l = strchr(l, '\n');
        if (l) l++;
    }
    return -1;
}

static long long status_field(const char *key, int hex, char *out, size_t outn) {
    char b[4096];
    if (slurp("/proc/self/status", b, sizeof b) < 0) return -1;
    size_t kl = strlen(key);
    for (char *l = b; l && *l;) {
        if (strncmp(l, key, kl) == 0 && l[kl] == ':') {
            char *v = l + kl + 1;
            while (*v == ' ' || *v == '\t') v++;
            if (out) {
                size_t i = 0;
                while (v[i] && v[i] != '\n' && i + 1 < outn) { out[i] = v[i]; i++; }
                out[i] = 0;
            }
            return hex ? (long long)strtoull(v, NULL, 16) : atoll(v);
        }
        l = strchr(l, '\n');
        if (l) l++;
    }
    return -1;
}

static long long now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long long)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

static int net_ifaces(void) { /* interfaces other than loopback */
    char b[4096];
    if (slurp("/proc/net/dev", b, sizeof b) < 0) return -1;
    int n = 0, line = 0;
    for (char *l = b; l && *l; l = strchr(l, '\n')) {
        if (*l == '\n') l++;
        if (++line <= 2) continue;
        while (*l == ' ') l++;
        if (*l && strncmp(l, "lo:", 3) != 0) n++;
    }
    return n;
}

int main(int argc, char **argv) {
    if (argc > 1 && strcmp(argv[1], "--check") == 0) { puts("kg-probe ok"); return 0; }
    int interval = argc > 1 ? atoi(argv[1]) : 250;
    if (interval < 50) interval = 50;
    char cpumax[64] = "", seccomp_mode[16] = "";
    {
        char b[128];
        if (slurp(CG "cpu.max", b, sizeof b) > 0) {
            char *nl = strchr(b, '\n');
            if (nl) *nl = 0;
            snprintf(cpumax, sizeof cpumax, "%s", b);
        }
    }
    int rw = open("/kg_rw_test", O_WRONLY | O_CREAT | O_EXCL, 0600);
    int rootfs_ro = 0;
    if (rw >= 0) { close(rw); unlink("/kg_rw_test"); } else rootfs_ro = (errno == EROFS);
    long long nnp = status_field("NoNewPrivs", 0, NULL, 0);
    long long sec = status_field("Seccomp", 0, seccomp_mode, sizeof seccomp_mode);
    long long capeff = status_field("CapEff", 1, NULL, 0);
    long long capbnd = status_field("CapBnd", 1, NULL, 0);
    printf("{\"type\":\"hello\",\"pid\":%d,\"uid\":%d,\"euid\":%d,\"no_new_privs\":%lld,\"seccomp\":%lld,\"cap_eff\":%lld,"
           "\"cap_bnd\":%lld,\"rootfs_read_only\":%d,\"net_ifaces\":%d,\"docker_sock\":%d,\"pids_max\":%lld,"
           "\"memory_max\":%lld,\"memory_swap_max\":%lld,\"cpu_max\":\"%s\",\"cgroup_v2\":%d}\n",
           (int)getpid(), (int)getuid(), (int)geteuid(), nnp, sec, capeff, capbnd, rootfs_ro, net_ifaces(),
           access("/var/run/docker.sock", F_OK) == 0, num("pids.max"), num("memory.max"), num("memory.swap.max"), cpumax,
           num("pids.current") >= 0);
    fflush(stdout);
    long long t0 = now_ms();
    for (;;) {
        printf("{\"type\":\"s\",\"t\":%lld,\"pc\":%lld,\"pp\":%lld,\"pe\":%lld,\"mc\":%lld,\"mp\":%lld,\"if\":%lld,"
               "\"mo\":%lld,\"mk\":%lld,\"mm\":%lld,\"cu\":%lld,\"nt\":%lld,\"tu\":%lld,\"pm\":%lld,\"mx\":%lld}\n",
               now_ms() - t0, num("pids.current"), num("pids.peak"), kv("pids.events", "max"), num("memory.current"),
               num("memory.peak"), kv("memory.stat", "inactive_file"), kv("memory.events", "oom"),
               kv("memory.events", "oom_kill"), kv("memory.events", "max"), kv("cpu.stat", "usage_usec"),
               kv("cpu.stat", "nr_throttled"), kv("cpu.stat", "throttled_usec"), num("pids.max"), num("memory.max"));
        if (fflush(stdout) != 0) return 0;      /* reader went away */
        usleep((useconds_t)interval * 1000);
    }
}
