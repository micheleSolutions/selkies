# Developer Guide: Selkies Clipboard Security for iSyncBrain Portal

## Overview

This document describes the modifications made to Selkies-GStreamer to add secure clipboard control for the iSyncBrain Research Lab platform. This custom build prevents data exfiltration via clipboard while maintaining full usability for researchers.

## Summary of Changes

### Files Added

1. **`src/selkies/clipboard_security.py`** - Core security control module
2. **`Dockerfile.isyncbrain`** - Custom Dockerfile for building the secure image
3. **`docs/SECURE_CLIPBOARD.md`** - User documentation

### Files Modified

1. **`src/selkies/input_handler.py`** - Integrated clipboard security checks

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Browser (User)                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│   ┌──────────────┐                      ┌──────────────┐        │
│   │ Clipboard IN │ ──────────────────▶  │ Remote       │        │
│   │ (paste)      │    Always Allowed    │ Workspace    │        │
│   └──────────────┘                      └──────────────┘        │
│                                                                  │
│   ┌──────────────┐                      ┌──────────────┐        │
│   │ Clipboard OUT│ ◀─────────────────── │ Remote       │        │
│   │ (copy)       │    Security Check    │ Workspace    │        │
│   └──────────────┘                      └──────────────┘        │
│                           │                                      │
│                           ▼                                      │
│              ┌────────────────────────┐                         │
│              │ ClipboardSecurityManager│                         │
│              ├────────────────────────┤                         │
│              │ • Enable/Disable check  │                         │
│              │ • Max bytes check       │                         │
│              │ • Rate limit check      │                         │
│              │ • Audit logging         │                         │
│              └────────────────────────┘                         │
│                           │                                      │
│              ┌────────────┴────────────┐                        │
│              ▼                         ▼                        │
│     ┌─────────────────┐      ┌─────────────────┐               │
│     │ JSON Lines File │      │ HTTP Endpoint   │               │
│     │ (local log)     │      │ (portal API)    │               │
│     └─────────────────┘      └─────────────────┘               │
│                                      │                          │
└──────────────────────────────────────│──────────────────────────┘
                                       │
                                       ▼
                          ┌─────────────────────┐
                          │  iSyncBrain Portal  │
                          │  (Spring Boot)      │
                          └─────────────────────┘
```

## Data Flow

### Clipboard IN (Browser → Workspace)
1. User pastes content in browser
2. Browser sends clipboard data via WebSocket
3. Selkies receives and writes to X11 clipboard
4. **No restrictions applied** - always allowed

### Clipboard OUT (Workspace → Browser)
1. User copies content in remote workspace
2. Selkies detects clipboard change via X11 monitoring
3. **Security checks applied:**
   - Is `CLIPBOARD_OUT_ENABLED=true`?
   - Is data size ≤ `CLIPBOARD_OUT_MAX_BYTES`?
   - Is rate limit not exceeded?
4. If all checks pass → send to browser + log as allowed
5. If any check fails → block + log with reason

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CLIPBOARD_OUT_ENABLED` | `true` | Master switch for clipboard OUT |
| `CLIPBOARD_OUT_MAX_BYTES` | `10240` | Max bytes per operation (10KB) |
| `CLIPBOARD_OUT_RATE_LIMIT_BYTES` | `51200` | Max bytes per time window (50KB) |
| `CLIPBOARD_OUT_RATE_LIMIT_WINDOW_SECONDS` | `60` | Rate limit window in seconds |
| `CLIPBOARD_LOG_FILE` | (empty) | Path to JSON lines log file |
| `CLIPBOARD_LOG_ENDPOINT` | (empty) | HTTP endpoint URL for log submission |
| `SELKIES_USER_ID` | `$USER` | User identifier for logging |
| `SELKIES_SESSION_ID` | (empty) | Session identifier for logging |

## Building the Docker Image

### Option 1: Build from Source

```bash
cd /path/to/selkies
docker build -f Dockerfile.isyncbrain -t selkies-isyncbrain:latest .
```

### Option 2: Multi-stage Build for CI/CD

```bash
# Build and tag with version
docker build -f Dockerfile.isyncbrain \
  -t your-registry.com/selkies-isyncbrain:v1.0.0 \
  -t your-registry.com/selkies-isyncbrain:latest .

# Push to registry
docker push your-registry.com/selkies-isyncbrain:v1.0.0
docker push your-registry.com/selkies-isyncbrain:latest
```

## Portal Integration

### Kubernetes/Docker Compose Configuration

When deploying workspaces, set these environment variables:

```yaml
# Example Kubernetes Pod spec
containers:
  - name: workspace
    image: your-registry.com/selkies-isyncbrain:latest
    env:
      # Clipboard Security Configuration
      - name: CLIPBOARD_OUT_ENABLED
        value: "true"
      - name: CLIPBOARD_OUT_MAX_BYTES
        value: "10240"  # 10KB
      - name: CLIPBOARD_OUT_RATE_LIMIT_BYTES
        value: "51200"  # 50KB per minute
      - name: CLIPBOARD_OUT_RATE_LIMIT_WINDOW_SECONDS
        value: "60"

      # Logging Configuration
      - name: CLIPBOARD_LOG_FILE
        value: "/var/log/clipboard/clipboard.jsonl"
      - name: CLIPBOARD_LOG_ENDPOINT
        value: "http://portal-service:8080/api/v1/clipboard-logs"

      # User/Session Identification (inject from portal)
      - name: SELKIES_USER_ID
        valueFrom:
          fieldRef:
            fieldPath: metadata.annotations['isyncbrain.io/user-id']
      - name: SELKIES_SESSION_ID
        valueFrom:
          fieldRef:
            fieldPath: metadata.name
```

### Spring Boot API Endpoint

Implement this endpoint in the portal to receive clipboard logs:

```java
@RestController
@RequestMapping("/api/v1")
@Slf4j
public class ClipboardLogController {

    private final ClipboardLogRepository clipboardLogRepository;
    private final SecurityAlertService securityAlertService;

    @PostMapping("/clipboard-logs")
    public ResponseEntity<Void> receiveClipboardLog(
            @RequestBody ClipboardLogEntry entry) {

        log.debug("Received clipboard log: user={}, blocked={}",
                  entry.getUserId(), entry.isBlocked());

        // Persist to database
        clipboardLogRepository.save(entry);

        // Trigger alert if blocked
        if (entry.isBlocked()) {
            securityAlertService.handleBlockedClipboard(entry);
        }

        return ResponseEntity.ok().build();
    }
}

@Entity
@Table(name = "clipboard_logs")
@Data
public class ClipboardLogEntry {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "timestamp")
    private String timestamp;  // ISO 8601 format

    @Column(name = "size_bytes")
    private Integer sizeBytes;

    @Column(name = "sha256_hash")
    private String sha256Hash;

    @Column(name = "user_id")
    private String userId;

    @Column(name = "session_id")
    private String sessionId;

    @Column(name = "blocked")
    private Boolean blocked;

    @Column(name = "block_reason")
    private String blockReason;

    @Column(name = "mime_type")
    private String mimeType;
}
```

### Log Entry JSON Schema

```json
{
  "timestamp": "2024-01-15T10:30:45.123456Z",
  "size_bytes": 256,
  "sha256_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "user_id": "researcher01",
  "session_id": "workspace-abc123",
  "blocked": false,
  "block_reason": null,
  "mime_type": "text/plain"
}
```

### Block Reasons

| Reason | Description |
|--------|-------------|
| `clipboard_out_disabled` | `CLIPBOARD_OUT_ENABLED=false` |
| `exceeds_max_bytes:X>Y` | Data size X exceeds limit Y |
| `rate_limit_exceeded:usage=X,limit=Y` | Rate limit reached |

## Security Recommendations

### Recommended Default Settings

For research environments with sensitive data:

```yaml
CLIPBOARD_OUT_ENABLED: "true"        # Allow controlled clipboard OUT
CLIPBOARD_OUT_MAX_BYTES: "10240"     # 10KB max per operation
CLIPBOARD_OUT_RATE_LIMIT_BYTES: "51200"  # 50KB per minute
CLIPBOARD_OUT_RATE_LIMIT_WINDOW_SECONDS: "60"
```

For high-security environments:

```yaml
CLIPBOARD_OUT_ENABLED: "false"       # Completely disable clipboard OUT
```

### Monitoring Dashboard Queries

Example SQL queries for monitoring:

```sql
-- Total blocked attempts by user
SELECT user_id, COUNT(*) as blocked_count
FROM clipboard_logs
WHERE blocked = true
GROUP BY user_id
ORDER BY blocked_count DESC;

-- Data transfer volume by user (last 24h)
SELECT user_id, SUM(size_bytes) as total_bytes
FROM clipboard_logs
WHERE blocked = false
  AND timestamp > NOW() - INTERVAL '24 hours'
GROUP BY user_id;

-- Recent blocked attempts
SELECT * FROM clipboard_logs
WHERE blocked = true
ORDER BY timestamp DESC
LIMIT 100;
```

## Testing

### Unit Test Verification

```bash
# Run clipboard security tests
cd /path/to/selkies
python -m pytest src/selkies/test_clipboard_security.py -v
```

### Integration Test

```bash
# Run container with test settings
docker run -it --rm \
  -p 8080:8080 \
  -e CLIPBOARD_OUT_ENABLED=true \
  -e CLIPBOARD_OUT_MAX_BYTES=100 \
  -e CLIPBOARD_LOG_FILE=/tmp/clipboard.jsonl \
  -e SELKIES_USER_ID=testuser \
  -e SELKIES_SESSION_ID=test-001 \
  selkies-isyncbrain:latest

# In another terminal, watch logs
docker exec -it <container_id> tail -f /tmp/clipboard.jsonl
```

### Test Scenarios

1. **Normal clipboard OUT**: Copy small text, verify it works and is logged
2. **Max bytes block**: Copy large text (>100 bytes with test settings), verify blocked
3. **Rate limit block**: Rapidly copy multiple items to exceed rate limit
4. **Disabled clipboard**: Set `CLIPBOARD_OUT_ENABLED=false`, verify all copies blocked

## Troubleshooting

### Clipboard Not Working

1. Check `SELKIES_ENABLE_CLIPBOARD=true` (upstream Selkies setting)
2. Verify browser has clipboard permissions
3. Check container logs: `docker logs <container_id>`

### Logs Not Appearing

1. Verify log file path is writable
2. Check directory exists before container starts
3. Verify HTTP endpoint is reachable from container

### HTTP Endpoint Errors

The security module will log HTTP errors but not fail clipboard operations:

```python
# From clipboard_security.py
except Exception as e:
    # Log error but don't fail the clipboard operation
    logger.error(f"Failed to send log to endpoint: {e}")
```

## Code Reference

### Key Files

- **`src/selkies/clipboard_security.py`** - Main security module
  - `ClipboardSecurityConfig` - Configuration from environment
  - `RateLimiter` - Sliding window rate limiter
  - `ClipboardSecurityManager` - Main security check logic

- **`src/selkies/input_handler.py`** - Integration points
  - Line ~50: Import clipboard_security
  - Line ~150: Initialize security manager
  - Line ~300: Security check in `start_clipboard()`
  - Line ~400: Security check in clipboard read handler

### Adding Custom Block Rules

To add custom blocking rules (e.g., content filtering), modify `ClipboardSecurityManager.check_clipboard_out()`:

```python
async def check_clipboard_out(self, data: Any, mime_type: str = "text/plain") -> Tuple[bool, Optional[str]]:
    # ... existing checks ...

    # Custom rule example: block specific patterns
    if isinstance(data, str):
        blocked_patterns = ["CONFIDENTIAL", "SECRET"]
        for pattern in blocked_patterns:
            if pattern in data.upper():
                return False, f"contains_blocked_pattern:{pattern}"

    return True, None
```

## Migration Guide

### Replacing Existing Selkies Deployment

1. **Build new image**:
   ```bash
   docker build -f Dockerfile.isyncbrain -t selkies-isyncbrain:latest .
   ```

2. **Update Kubernetes deployment** to use new image:
   ```yaml
   image: your-registry/selkies-isyncbrain:latest
   ```

3. **Add environment variables** for clipboard security

4. **Implement API endpoint** in portal for log collection

5. **Test in staging** before production rollout

### Backward Compatibility

To run with original Selkies behavior (no restrictions):
```yaml
CLIPBOARD_OUT_ENABLED: "true"
CLIPBOARD_OUT_MAX_BYTES: "1073741824"  # 1GB
CLIPBOARD_OUT_RATE_LIMIT_BYTES: "1073741824"
```

## Contact

For questions about this implementation, refer to the git history on branch `claude/secure-clipboard-control-Uo4lM`.
