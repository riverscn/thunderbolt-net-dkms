/* SPDX-License-Identifier: GPL-2.0-only */
/* Small bounded TCP transfer with payload verification, for diskless CI only. */
#define _DEFAULT_SOURCE
#define _POSIX_C_SOURCE 200809L
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#define BYTES (1024 * 1024)
static long now_ms(void)
{
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec * 1000L + t.tv_nsec / 1000000;
}
int main(int argc, char **argv)
{
    struct sockaddr_storage addr = {0};
    struct tcp_info info = {0};
    socklen_t ilen = sizeof(info), alen;
    unsigned char buf[32768];
    size_t total = 0;
    int fd, server, af, ok = 0, one = 1;
    long end;
    if (argc != 3) return 2;
    server = !strcmp(argv[1], "server");
    af = strchr(argv[2], ':') ? AF_INET6 : AF_INET;
    if (af == AF_INET) {
        struct sockaddr_in *a = (void *)&addr;
        a->sin_family = af; a->sin_port = htons(5001);
        if (inet_pton(af, argv[2], &a->sin_addr) != 1) return 2;
        alen = sizeof(*a);
    } else {
        struct sockaddr_in6 *a = (void *)&addr;
        a->sin6_family = af; a->sin6_port = htons(5001);
        if (inet_pton(af, argv[2], &a->sin6_addr) != 1) return 2;
        alen = sizeof(*a);
    }
    alarm(15);
    fd = socket(af, SOCK_STREAM, 0);
    if (fd < 0) return 2;
    if (server) {
        int client;
        setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
        if (bind(fd, (void *)&addr, alen) || listen(fd, 1)) return 2;
        client = accept(fd, NULL, NULL);
        close(fd); fd = client;
        if (fd < 0) return 2;
        while (total < BYTES) {
            ssize_t n = read(fd, buf, sizeof(buf));
            if (n <= 0) break;
            for (ssize_t i = 0; i < n; i++)
                if (buf[i] != (unsigned char)((total + i) % 251)) return 3;
            total += n;
        }
        if (total == BYTES && write(fd, "OK", 2) == 2) ok = 1;
    } else {
        if (connect(fd, (void *)&addr, alen)) return 2;
        fcntl(fd, F_SETFL, O_NONBLOCK);
        end = now_ms() + 8000;
        while (now_ms() < end) {
            struct pollfd p = { .fd = fd, .events = POLLIN };
            if (total < BYTES) p.events |= POLLOUT;
            if (poll(&p, 1, 100) < 0 && errno != EINTR) break;
            if ((p.revents & POLLOUT) && total < BYTES) {
                size_t len = BYTES - total;
                if (len > sizeof(buf)) len = sizeof(buf);
                for (size_t i = 0; i < len; i++) buf[i] = (total + i) % 251;
                ssize_t n = send(fd, buf, len, MSG_NOSIGNAL);
                if (n > 0) total += n;
                else if (errno != EAGAIN && errno != EINTR) break;
            }
            if (p.revents & POLLIN) {
                ssize_t n = recv(fd, buf, sizeof(buf), 0);
                if (n == 2 && !memcmp(buf, "OK", 2)) ok = 1;
                if (n >= 0) break;
            }
        }
        getsockopt(fd, IPPROTO_TCP, TCP_INFO, &info, &ilen);
        printf("TBNET_TCP success=%d sent=%zu pmtu=%u snd_mss=%u retrans=%u\n",
               ok, total, info.tcpi_pmtu, info.tcpi_snd_mss, info.tcpi_total_retrans);
    }
    close(fd);
    return server ? !ok : 0; /* Timeout is an observation; setup errors are not. */
}
