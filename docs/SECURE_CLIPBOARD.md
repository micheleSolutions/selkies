# Secure Clipboard Control for iSyncBrain Research Lab

This document describes the secure clipboard control feature for Selkies-GStreamer, designed to prevent data exfiltration while maintaining usability.

## Overview

The secure clipboard feature provides granular control over clipboard data flow between the remote workspace and the user's browser:

- **Clipboard IN** (browser → workspace): Always allowed, logged for audit
- **Clipboard OUT** (workspace → browser): Controlled with configurable limits and audit logging

## Features

1. **Enable/Disable Flag**: Completely enable or disable clipboard OUT
2. **Max Bytes Per Operation**: Limit the size of individual clipboard transfers
3. **Rate Limiting**: Limit total bytes transferred within a time window
4. **Bidirectional Audit Logging**: Log all clipboard operations (IN and OUT) with:
   - Timestamp (ISO 8601 format)
   - Direction (`in` or `out`)
   - Data size in bytes
   - SHA256 hash of content (for audit without storing actual data)
   - User ID and Session ID
   - Block status and reason (if blocked)
   - MIME type
5. **Ctrl+C/V Interception**: Automatic clipboard sync on keyboard shortcuts
   - Ctrl+V: Syncs browser clipboard to remote before paste
   - Ctrl+C: Requests remote clipboard after copy (100ms delay)

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CLIPBOARD_OUT_ENABLED` | `true` | Enable/disable clipboard OUT completely |
| `CLIPBOARD_OUT_MAX_BYTES` | `10240` | Maximum bytes per single operation (10KB) |
| `CLIPBOARD_OUT_RATE_LIMIT_BYTES` | `51200` | Maximum bytes allowed per time window (50KB) |
| `CLIPBOARD_OUT_RATE_LIMIT_WINDOW_SECONDS` | `60` | Time window for rate limiting (seconds) |
| `CLIPBOARD_LOG_FILE` | `` | Path to JSON lines log file (empty = disabled) |
| `CLIPBOARD_LOG_ENDPOINT` | `` | HTTP endpoint for log submission (empty = disabled) |
| `CLIPBOARD_LOG_VERIFY_SSL` | `true` | Verify SSL certificates (set `false` for self-signed certs) |
| `SELKIES_USER_ID` | `$USER` | User ID for audit logging |
| `SELKIES_SESSION_ID` | `` | Session ID for audit logging |

## Building the Custom Image

### Using the iSyncBrain Dockerfile

```bash
# Clone the repository
git clone https://github.com/your-org/selkies-gstreamer-isyncbrain.git
cd selkies-gstreamer-isyncbrain

# Build the image
docker build -f Dockerfile.isyncbrain -t selkies-isyncbrain:latest .
```

### Using Docker Compose

```yaml
version: '3.8'
services:
  workspace:
    build:
      context: .
      dockerfile: Dockerfile.isyncbrain
    environment:
      # Clipboard Security
      - CLIPBOARD_OUT_ENABLED=true
      - CLIPBOARD_OUT_MAX_BYTES=10240
      - CLIPBOARD_OUT_RATE_LIMIT_BYTES=51200
      - CLIPBOARD_OUT_RATE_LIMIT_WINDOW_SECONDS=60
      - CLIPBOARD_LOG_FILE=/var/log/clipboard/clipboard.jsonl
      - CLIPBOARD_LOG_ENDPOINT=https://portal:8443/api/clipboard-log
      - CLIPBOARD_LOG_VERIFY_SSL=false  # For self-signed certificates
      - SELKIES_USER_ID=${USER_ID}
      - SELKIES_SESSION_ID=${SESSION_ID}
      # Other Selkies settings
      - SELKIES_ENABLE_CLIPBOARD=true
    volumes:
      - clipboard-logs:/var/log/clipboard
    ports:
      - "8080:8080"

volumes:
  clipboard-logs:
```

## Testing Locally

### 1. Build and Run the Container

```bash
# Build
docker build -f Dockerfile.isyncbrain -t selkies-isyncbrain:latest .

# Run with clipboard security enabled
docker run -it --rm \
  -p 8080:8080 \
  -e CLIPBOARD_OUT_ENABLED=true \
  -e CLIPBOARD_OUT_MAX_BYTES=1024 \
  -e CLIPBOARD_OUT_RATE_LIMIT_BYTES=5120 \
  -e CLIPBOARD_OUT_RATE_LIMIT_WINDOW_SECONDS=60 \
  -e CLIPBOARD_LOG_FILE=/var/log/clipboard/clipboard.jsonl \
  -e SELKIES_USER_ID=testuser \
  -e SELKIES_SESSION_ID=test-session-001 \
  -v $(pwd)/logs:/var/log/clipboard \
  selkies-isyncbrain:latest
```

### 2. Access the Desktop

Open your browser and navigate to `http://localhost:8080`

### 3. Test Clipboard Operations

1. **Test Ctrl+C/V**: Use keyboard shortcuts - check browser console for sync messages:
   - `Ctrl+V detected - syncing clipboard before paste`
   - `Ctrl+C detected - requesting clipboard from remote`

2. **Test Normal Operation**: Copy small text (< 1KB) in the workspace and verify it appears in your browser clipboard

3. **Test Max Bytes Limit**: Copy a large file or text (> 1KB with the settings above) and verify it's blocked

4. **Test Rate Limiting**: Rapidly copy multiple items to exceed the rate limit and verify blocking

5. **Check Logs**: View the clipboard logs:
   ```bash
   tail -f logs/clipboard.jsonl
   ```

## Log Output Examples

### Clipboard IN (Browser → Workspace)

```json
{
  "timestamp": "2024-01-15T10:30:45.123456Z",
  "direction": "in",
  "size_bytes": 256,
  "sha256_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "user_id": "researcher01",
  "session_id": "session-abc123",
  "blocked": false,
  "block_reason": null,
  "mime_type": "text/plain"
}
```

### Clipboard OUT - Successful Transfer

```json
{
  "timestamp": "2024-01-15T10:31:00.123456Z",
  "direction": "out",
  "size_bytes": 512,
  "sha256_hash": "a591a6d40bf420404a011733cfb7b190d62c65bf0bcda32b57b277d9ad9f146e",
  "user_id": "researcher01",
  "session_id": "session-abc123",
  "blocked": false,
  "block_reason": null,
  "mime_type": "text/plain"
}
```

### Blocked - Exceeds Max Bytes

```json
{
  "timestamp": "2024-01-15T10:31:12.789012Z",
  "direction": "out",
  "size_bytes": 15360,
  "sha256_hash": "a591a6d40bf420404a011733cfb7b190d62c65bf0bcda32b57b277d9ad9f146e",
  "user_id": "researcher01",
  "session_id": "session-abc123",
  "blocked": true,
  "block_reason": "exceeds_max_bytes:15360>10240",
  "mime_type": "text/plain"
}
```

### Blocked - Rate Limit Exceeded

```json
{
  "timestamp": "2024-01-15T10:32:00.456789Z",
  "direction": "out",
  "size_bytes": 8192,
  "sha256_hash": "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9",
  "user_id": "researcher01",
  "session_id": "session-abc123",
  "blocked": true,
  "block_reason": "rate_limit_exceeded:usage=48000,limit=51200",
  "mime_type": "text/plain"
}
```

### Blocked - Clipboard OUT Disabled

```json
{
  "timestamp": "2024-01-15T10:33:15.111222Z",
  "direction": "out",
  "size_bytes": 512,
  "sha256_hash": "2c26b46b68ffc68ff99b453c1d30413413422d706483bfa0f98a5e886266e7ae",
  "user_id": "researcher01",
  "session_id": "session-abc123",
  "blocked": true,
  "block_reason": "clipboard_out_disabled",
  "mime_type": "text/plain"
}
```

## HTTP Endpoint Integration

When `CLIPBOARD_LOG_ENDPOINT` is configured, logs are sent via HTTP POST with JSON body:

### Request Format

```http
POST /api/clipboard-log HTTP/1.1
Content-Type: application/json

{
  "timestamp": "2024-01-15T10:30:45.123456Z",
  "direction": "in",
  "size_bytes": 256,
  "sha256_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "user_id": "researcher01",
  "session_id": "session-abc123",
  "blocked": false,
  "block_reason": null,
  "mime_type": "text/plain"
}
```

### Self-Signed SSL Certificates

For enterprise deployments using self-signed certificates:

```bash
CLIPBOARD_LOG_ENDPOINT=https://portal:8443/api/clipboard-log
CLIPBOARD_LOG_VERIFY_SSL=false
```

### Spring Boot Integration Example

```java
@RestController
@RequestMapping("/api")
public class ClipboardLogController {

    @PostMapping("/clipboard-log")
    public ResponseEntity<Void> logClipboardEvent(@RequestBody ClipboardLogEntry entry) {
        // Store in database
        clipboardLogRepository.save(entry);

        // Alert if blocked
        if (entry.isBlocked()) {
            alertService.sendSecurityAlert(entry);
        }

        return ResponseEntity.ok().build();
    }
}

@Data
public class ClipboardLogEntry {
    private String timestamp;
    private String direction;  // "in" or "out"
    private int sizeBytes;
    private String sha256Hash;
    private String userId;
    private String sessionId;
    private boolean blocked;
    private String blockReason;
    private String mimeType;
}
```

## Keyboard Shortcut Behavior

The web client intercepts Ctrl+C and Ctrl+V to ensure clipboard synchronization:

| Shortcut | Behavior |
|----------|----------|
| **Ctrl+V** | 1. Read browser clipboard<br>2. Send to remote workspace<br>3. Forward keystroke to remote |
| **Ctrl+C** | 1. Forward keystroke to remote<br>2. Wait 100ms for clipboard update<br>3. Request clipboard from remote<br>4. Write to browser clipboard |

This ensures clipboard works reliably with both keyboard shortcuts and context menu (right-click).

## Security Considerations

1. **Hash-Only Logging**: The system logs SHA256 hashes instead of actual content, enabling audit trails without storing sensitive data

2. **Defense in Depth**: This feature should be used alongside other security measures (network segmentation, DLP, etc.)

3. **Binary Clipboard**: Binary data (images) is subject to the same controls as text

4. **Clipboard IN**: Incoming clipboard data is always allowed but logged for complete audit trail

5. **Bidirectional Logging**: Both directions are logged, enabling complete reconstruction of clipboard activity

## Troubleshooting

### Clipboard Not Working

1. Check that `SELKIES_ENABLE_CLIPBOARD=true` is set
2. Verify the browser has clipboard permissions
3. Check browser console for sync messages
4. Check server logs for errors

### Ctrl+C/V Not Syncing

1. Open browser developer console (F12)
2. Look for messages like `Ctrl+V detected - syncing clipboard before paste`
3. If no messages appear, keyboard events may not be reaching the handler
4. Try clicking on the video element first to ensure focus

### Logs Not Appearing

1. Verify `CLIPBOARD_LOG_FILE` path is writable
2. Check that the directory exists
3. Review container logs for errors

### HTTP Endpoint Not Receiving Logs

1. Verify the endpoint URL is correct
2. Check network connectivity between containers
3. For HTTPS with self-signed certs, set `CLIPBOARD_LOG_VERIFY_SSL=false`
4. Review the endpoint service logs

## Files Modified

- `src/selkies/clipboard_security.py` - Security control module with bidirectional logging
- `src/selkies/input_handler.py` - Integrated security checks and clipboard IN logging
- `addons/gst-web/src/input.js` - Ctrl+C/V interception for clipboard sync
- `addons/gst-web/src/app.js` - Clipboard sync callbacks
- `Dockerfile.isyncbrain` - Custom Dockerfile for iSyncBrain

## Compatibility

This implementation maintains full compatibility with upstream Selkies-GStreamer. The security controls are additive and can be disabled by setting `CLIPBOARD_OUT_ENABLED=true` with high limits, effectively returning to default behavior.
