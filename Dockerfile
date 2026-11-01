# Assemble the exact verified panel/runtime release from this fork.
FROM alpine:3.23
ARG TARGETARCH
ARG TARGETVARIANT
WORKDIR /app
RUN apk add --no-cache ca-certificates tzdata fail2ban iptables ip6tables bash curl openssl tar
RUN set -eu; \
    case "$TARGETARCH/$TARGETVARIANT" in \
      amd64/*) platform=amd64 ;; arm64/*) platform=arm64 ;; 386/*) platform=386 ;; \
      arm/v7) platform=armv7 ;; arm/v6) platform=armv6 ;; arm/v5) platform=armv5 ;; \
      s390x/*) platform=s390x ;; *) echo 'Unsupported architecture' >&2; exit 1 ;; \
    esac; \
    release='https://github.com/kjxv/3x-ui/releases/download/v3.10.2-preset-ipv6-hy2'; \
    curl -fLsS --retry 5 "$release/checksums.txt" -o /tmp/checksums.txt; \
    archive="x-ui-linux-${platform}.tar.gz"; \
    curl -fLsS --retry 5 "$release/$archive" -o "/tmp/$archive"; \
    expected=$(awk -v name="$archive" '$2 == name {print $1}' /tmp/checksums.txt); \
    test "${#expected}" = 64; \
    printf '%s  %s\n' "$expected" "/tmp/$archive" | sha256sum -c -; \
    tar -xzf "/tmp/$archive" --strip-components=1 -C /app; \
    if [ "$TARGETARCH" = arm ]; then \
      mv "bin/xray-linux-$platform" bin/xray-linux-arm32; \
      if [ -f "bin/mtg-linux-$platform" ]; then mv "bin/mtg-linux-$platform" bin/mtg-linux-arm; fi; \
    fi; \
    cp x-ui.sh /usr/bin/x-ui; chmod +x /app/x-ui /usr/bin/x-ui /app/bin/xray-*; \
    rm -f "/tmp/$archive" /tmp/checksums.txt
RUN rm -f /etc/fail2ban/jail.d/alpine-ssh.conf \
    && cp /etc/fail2ban/jail.conf /etc/fail2ban/jail.local \
    && sed -i 's/^\[sshd\]$/&\nenabled = false/' /etc/fail2ban/jail.local
COPY DockerEntrypoint.sh /app/
RUN chmod +x /app/DockerEntrypoint.sh
ENV TZ=Etc/UTC XUI_IN_DOCKER=true XUI_MAIN_FOLDER=/app XUI_BIN_FOLDER=/app/bin XRAY_LOCATION_ASSET=/app/bin XUI_ENABLE_FAIL2BAN=true
EXPOSE 2053 2096
VOLUME ["/etc/x-ui"]
ENTRYPOINT ["/app/DockerEntrypoint.sh"]
