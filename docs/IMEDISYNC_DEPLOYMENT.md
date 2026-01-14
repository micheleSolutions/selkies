# iMediSync Bastion Deployment Guide

This document describes how to deploy Selkies-GStreamer with iMediSync Bastion in a containerized environment with split internal/external network topology.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           Docker Network (enclave0)                      │
│                              172.19.0.0/16                               │
│                                                                          │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐                │
│  │   Selkies   │     │   Spring    │     │   coTURN    │                │
│  │  Container  │◄───►│   Portal    │◄───►│   Server    │                │
│  │ 172.19.0.4  │     │ 172.19.0.3  │     │ 172.19.0.11 │                │
│  │   :8081     │     │   :8081     │     │   :13478    │                │
│  └─────────────┘     └──────┬──────┘     └──────┬──────┘                │
│                             │                   │                        │
└─────────────────────────────┼───────────────────┼────────────────────────┘
                              │                   │
                              │ Host Ports        │
                              ▼                   ▼
                         118.131.80.198:18081  118.131.80.198:13478
                              │                   │
                              └─────────┬─────────┘
                                        │
                                        ▼
                                   ┌─────────┐
                                   │ Browser │
                                   └─────────┘
```

## Key Concepts

### Split TURN Configuration

In containerized environments, the Selkies container and the browser use different network paths to reach the TURN server:

- **Container → TURN**: Uses internal Docker IP (e.g., `172.19.0.11:13478`)
- **Browser → TURN**: Uses external IP (e.g., `118.131.80.198:13478`)

This requires two separate configuration values:
- `SELKIES_TURN_HOST` - Internal IP for container's ICE gathering
- `SELKIES_TURN_HOST_EXTERNAL` - External IP sent to browser via `/turn` endpoint

## Environment Variables

### Required for Split Network Topology

| Variable | Example | Description |
|----------|---------|-------------|
| `SELKIES_TURN_HOST` | `172.19.0.11` | Internal TURN server IP (container uses this) |
| `SELKIES_TURN_HOST_EXTERNAL` | `118.131.80.198` | External TURN server IP (browser uses this) |
| `SELKIES_TURN_PORT` | `13478` | TURN server port |
| `SELKIES_TURN_SHARED_SECRET` | `<secret>` | Shared secret for HMAC credential generation |
| `SELKIES_STUN_HOST` | `stun.l.google.com` | STUN server hostname |
| `SELKIES_STUN_PORT` | `19302` | STUN server port |

### Optional Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `SELKIES_ADDR` | `0.0.0.0` | Address for signaling server to bind (use `0.0.0.0` for container deployments) |
| `SELKIES_PORT` | `8081` | Port for signaling server |
| `SELKIES_TURN_PROTOCOL` | `udp` | TURN protocol (`udp` or `tcp`) |
| `SELKIES_ENABLE_RESIZE` | `true` | Enable remote desktop resolution resize to match browser window |

## coTURN Configuration

### Required Settings

```bash
docker run -d --name coturn \
  --network your-docker-network \
  -p 13478:13478/udp \
  -p 13478:13478/tcp \
  -p 49152-49200:49152-49200/udp \
  coturn/coturn:latest \
  -n \
  --listening-ip=0.0.0.0 \
  --listening-port=13478 \
  --realm=your-domain.com \
  --use-auth-secret \
  --static-auth-secret=YOUR_SHARED_SECRET_HERE \
  --min-port=49152 \
  --max-port=49200 \
  --external-ip=YOUR_EXTERNAL_IP \
  --relay-ip=CONTAINER_INTERNAL_IP \
  --log-file=stdout \
  --no-cli \
  --no-multicast-peers
```

### Important Notes

1. **Port Exposure**: Ports `13478` (UDP/TCP) and relay ports (`49152-49200` UDP) must be exposed on the host
2. **Shared Secret**: Must match `SELKIES_TURN_SHARED_SECRET` in Selkies container
3. **External IP**: Set to the host's external IP address

## Spring Boot Gateway Configuration

The gateway must proxy these endpoints to the Selkies container:

### WebSocket Signaling
```
/app/.../proxy/**/signaling/ → ws://selkies-container:8081/ws/
```

### TURN Configuration Endpoint
```
/app/.../proxy/**/turn → http://selkies-container:8081/turn
```

### Example Spring Cloud Gateway Route

```java
@Bean
public RouteLocator customRouteLocator(RouteLocatorBuilder builder) {
    return builder.routes()
        // WebSocket signaling
        .route("selkies-signaling", r -> r
            .path("/app/research-lab/workspace/*/proxy/**/signaling/**")
            .filters(f -> f.rewritePath(".*/signaling(/.*)?", "/ws$1"))
            .uri("ws://selkies-container:8081"))

        // TURN endpoint (CRITICAL: must use port 8081, not 3000)
        .route("selkies-turn", r -> r
            .path("/app/research-lab/workspace/*/proxy/**/turn")
            .filters(f -> f.rewritePath(".*/turn", "/turn"))
            .uri("http://selkies-container:8081"))
        .build();
}
```

## Session Takeover

When a user disconnects (closes browser/tab) and reconnects, the signaling server supports automatic session takeover:

- If the old session partner is no longer connected, the new connection takes over
- No manual cleanup required
- Implemented in `signaling_server.py`

## Troubleshooting

### "Peer busy" Error on Reconnect

**Cause**: Old session not cleaned up properly.

**Solution**: Upgrade to v1.2.0+ which includes session takeover fix.

### No Relay Candidates in Browser

**Symptoms**: Browser shows only `host` and `srflx` candidates, no `relay` candidates.

**Check**:
1. Is coTURN port exposed on host?
   ```bash
   nc -vzu YOUR_EXTERNAL_IP 13478
   ```

2. Is TURN config correct?
   ```javascript
   // In browser console
   fetch('./turn').then(r=>r.json()).then(console.log)
   ```
   Should show `turn:EXTERNAL_IP:13478`

3. Can container reach TURN internally?
   ```bash
   docker exec selkies-container nc -vzu INTERNAL_TURN_IP 13478
   ```

### Container Cannot Reach External TURN IP

**Cause**: Container on isolated Docker network cannot reach external IP.

**Solution**: Use internal TURN IP for container:
```bash
SELKIES_TURN_HOST=172.19.0.11        # Internal
SELKIES_TURN_HOST_EXTERNAL=118.131.80.198  # External
```

### Signaling Server Not Reachable from Gateway

**Cause**: Selkies using `--addr=localhost` (default was localhost).

**Solution**: Set `SELKIES_ADDR=0.0.0.0` or upgrade to v1.1.0+ which defaults to `0.0.0.0`.

### Data Channel Not Opening (Mouse/Keyboard Not Working)

**Symptoms**: Video/audio work but no input.

**Check ICE connectivity**:
1. Browser generates relay candidates? (see above)
2. Container generates relay candidates?
   ```bash
   docker exec selkies-container tail -100 /tmp/selkies-entrypoint.log | grep -i relay
   ```

### Clipboard Logging Error

```
Cannot connect to host portal:8080
```

**Cause**: Wrong port in `CLIPBOARD_LOG_ENDPOINT`.

**Solution**: Set correct port (e.g., `http://portal:8081/api/clipboard-log`).

## Version History

| Version | Changes |
|---------|---------|
| v1.0.0 | Initial iMediSync Bastion release |
| v1.1.0 | Fixed `--addr=localhost` to `--addr=0.0.0.0`, added `TURN_HOST_EXTERNAL` support |
| v1.2.0 | Added session takeover for reconnection handling |
| v1.3.0 | Enabled `SELKIES_ENABLE_RESIZE` by default for automatic resolution adaptation |
| v2.0.0 | New minimal base image without browsers/gadgets, infrastructure-ready |

## Building the Image

### Option 1: Base Image (Minimal - Recommended for Infrastructure)

```bash
git clone https://github.com/your-org/selkies.git
cd selkies
git checkout claude/debug-clipboard-control-hCcAd  # or main after merge

docker build -f Dockerfile.base -t selkies-base:v2.0.0 --no-cache .
```

**Base image includes:**
- XFCE desktop environment (clean menu, no browser/mail entries)
- Selkies-GStreamer WebRTC streaming
- Secure clipboard with logging support
- TURN/STUN support
- Basic tools: git, vim, nano, tmux, htop, zip, unzip

**Base image excludes (security hardening):**
- Web browsers (Chrome, Firefox)
- Email clients
- Unnecessary desktop gadgets (xfce4-goodies)
- python3/pip3 (removed after build)
- curl/wget (removed after build)

### Option 2: Full Image (with Firefox and extras)

```bash
docker build -f Dockerfile.isyncbrain -t selkies-secure:v2.0.0 --no-cache .
```

## Quick Start

```bash
docker run -d \
  --name selkies-workspace \
  --network your-docker-network \
  -e SELKIES_TURN_HOST=172.19.0.11 \
  -e SELKIES_TURN_HOST_EXTERNAL=118.131.80.198 \
  -e SELKIES_TURN_PORT=13478 \
  -e SELKIES_TURN_SHARED_SECRET=your-secret-here \
  -e CLIPBOARD_OUT_ENABLED=true \
  -e CLIPBOARD_OUT_MAX_BYTES=10240 \
  selkies-base:v2.0.0
```
