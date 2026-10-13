# ---- stage 1: telemetry probe. Uses the pre-built static x86-64 binary when it runs here, else builds from source. ----
FROM alpine:3.20 AS probe
COPY probe/ /src/
RUN if /src/kg-probe --check >/dev/null 2>&1; then cp /src/kg-probe /kg-probe; \
    else apk add --no-cache gcc musl-dev && gcc -O2 -static -o /kg-probe /src/kg-probe.c; fi
FROM eclipse-temurin:21-jdk-alpine
RUN adduser -D -u 1000 sandbox
RUN adduser -D -u 1001 kgprobe
COPY --from=probe /kg-probe /usr/local/bin/kg-probe
USER sandbox
WORKDIR /workspace
