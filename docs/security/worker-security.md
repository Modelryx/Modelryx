# CAD Worker Security Guide

This guide documents the security model for Modelryx CAD workers, including authentication, communication, and best practices for deploying custom workers.

## Overview

CAD workers are external services that process CAD files and return conversion results to the Modelryx server. The security model ensures:

- **Worker Authentication**: Only authorized workers can communicate with the server
- **Request Validation**: All requests include cryptographic signatures
- **Message Integrity**: HMAC verification prevents tampering
- **Secure Secrets**: Worker keys never transmitted over insecure channels
- **Authorization**: Each operation is tied to authenticated requests

---

## Authentication Model

### Shared Secret (Internal Worker Key)

All workers share a single long-lived secret key configured at deployment time:

```
MODELRYX_INTERNAL_WORKER_KEY = base64(random(32 bytes))
```

**Requirements:**
- ✅ Minimum 32 bytes (256 bits) of cryptographic random data
- ✅ Unique per Modelryx deployment
- ✅ Stored securely (environment variables, secrets manager)
- ✅ Rotated periodically (annually or on suspected compromise)
- ✅ Never logged or exposed in debug output

**Generation (Unix/Linux/macOS):**
```bash
# Generate secure worker key
MODELRYX_INTERNAL_WORKER_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")

# Or with OpenSSL
MODELRYX_INTERNAL_WORKER_KEY=$(openssl rand -base64 32)

# Verify it's correct length
echo $MODELRYX_INTERNAL_WORKER_KEY | base64 -d | wc -c
# Should output: 33 (32 bytes + newline)
```

**Generation (Windows PowerShell):**
```powershell
# Generate secure worker key
$key = [Convert]::ToBase64String([byte[]] (1..32 | ForEach-Object { Get-Random -Maximum 256 }))
$env:MODELRYX_INTERNAL_WORKER_KEY = $key
echo $env:MODELRYX_INTERNAL_WORKER_KEY
```

---

## Request Authentication (HMAC)

### Request Signing

All requests from workers to the server include an `Authorization` header with HMAC signature:

```
Authorization: Bearer <payload>.<signature>
X-Request-Timestamp: <unix-timestamp>
```

**Payload Structure:**
```
payload = base64url(json({
  "worker_url": "http://localhost:8080",
  "timestamp": 1705315200,
  "request_id": "uuid-v4",
  "action": "heartbeat"
}))
```

**Signature Calculation:**
```
signature = base64url(hmac_sha256(
  key=INTERNAL_WORKER_KEY,
  message=payload + "." + timestamp
))

auth_token = payload + "." + signature
```

### Example Worker Implementation (Python)

```python
import hmac
import hashlib
import base64
import json
import time
import uuid
from datetime import datetime

class WorkerAuth:
    def __init__(self, worker_key: str):
        self.key = base64.b64decode(worker_key)
    
    def create_auth_header(self, worker_url: str, action: str) -> dict:
        """Create HMAC-signed authorization header"""
        timestamp = int(time.time())
        request_id = str(uuid.uuid4())
        
        # Create payload
        payload_obj = {
            "worker_url": worker_url,
            "timestamp": timestamp,
            "request_id": request_id,
            "action": action
        }
        
        # Encode payload
        payload = base64.urlsafe_b64encode(
            json.dumps(payload_obj, separators=(',', ':')).encode()
        ).decode().rstrip('=')
        
        # Create signature
        message = f"{payload}.{timestamp}"
        signature = base64.urlsafe_b64encode(
            hmac.new(self.key, message.encode(), hashlib.sha256).digest()
        ).decode().rstrip('=')
        
        # Return headers
        return {
            "Authorization": f"Bearer {payload}.{signature}",
            "X-Request-Timestamp": str(timestamp),
            "Content-Type": "application/json"
        }

# Usage
auth = WorkerAuth(worker_key)
headers = auth.create_auth_header(
    worker_url="http://worker:8080",
    action="heartbeat"
)

# Use headers in requests
import requests
response = requests.post(
    "http://server:8000/workers/heartbeat",
    headers=headers,
    json={"status": "ready"}
)
```

### Example Worker Implementation (TypeScript/Node)

```typescript
import crypto from 'crypto';
import { randomUUID } from 'crypto';

class WorkerAuth {
  private key: Buffer;

  constructor(workerKey: string) {
    this.key = Buffer.from(workerKey, 'base64');
  }

  createAuthHeader(workerUrl: string, action: string): Record<string, string> {
    const timestamp = Math.floor(Date.now() / 1000);
    const requestId = randomUUID();

    // Create payload
    const payloadObj = {
      worker_url: workerUrl,
      timestamp,
      request_id: requestId,
      action
    };

    // Encode payload
    const payload = Buffer.from(
      JSON.stringify(payloadObj, null, 0)
    )
      .toString('base64')
      .replace(/=/g, ''); // Remove padding

    // Create signature
    const message = `${payload}.${timestamp}`;
    const signature = crypto
      .createHmac('sha256', this.key)
      .update(message)
      .digest('base64')
      .replace(/=/g, ''); // Remove padding

    return {
      Authorization: `Bearer ${payload}.${signature}`,
      'X-Request-Timestamp': String(timestamp),
      'Content-Type': 'application/json'
    };
  }
}

// Usage
const auth = new WorkerAuth(process.env.MODELRYX_INTERNAL_WORKER_KEY!);
const headers = auth.createAuthHeader(
  'http://worker:8080',
  'heartbeat'
);

// Use headers in fetch/axios
const response = await fetch('http://server:8000/workers/heartbeat', {
  method: 'POST',
  headers,
  body: JSON.stringify({ status: 'ready' })
});
```

---

## Request Validation (Server-Side)

### Verification Steps

1. **Extract signature components**
   ```
   Authorization: Bearer <payload>.<signature>
   ```

2. **Validate timestamp**
   ```
   current_time - header_timestamp < 300  # 5 minute window
   ```

3. **Verify HMAC signature**
   ```
   expected_signature = hmac_sha256(
     key=INTERNAL_WORKER_KEY,
     message=payload + "." + timestamp
   )
   
   signature == expected_signature
   ```

4. **Verify payload claims**
   ```
   - Parse base64 payload
   - Validate worker_url is registered
   - Validate request_id hasn't been used (replay protection)
   - Validate action is allowed for this worker
   ```

### Python Server Implementation

```python
import hmac
import hashlib
import base64
import json
import time
from typing import Optional, Dict, Any
from datetime import datetime

class WorkerVerifier:
    def __init__(self, worker_key: str, max_age_seconds: int = 300):
        self.key = base64.b64decode(worker_key)
        self.max_age_seconds = max_age_seconds
        self.seen_request_ids = set()  # In production: use Redis/database
    
    def verify_request(self, auth_header: str, timestamp_header: str) -> Optional[Dict[str, Any]]:
        """Verify and extract worker request"""
        try:
            # Validate header format
            if not auth_header.startswith('Bearer '):
                return None
            
            token = auth_header[7:]  # Remove "Bearer "
            parts = token.split('.')
            if len(parts) != 2:
                return None
            
            payload_b64, signature_b64 = parts
            timestamp = int(timestamp_header)
            
            # Validate timestamp
            current_time = int(time.time())
            if abs(current_time - timestamp) > self.max_age_seconds:
                return None  # Request too old
            
            # Verify signature
            message = f"{payload_b64}.{timestamp}"
            expected_signature_bytes = hmac.new(
                self.key,
                message.encode(),
                hashlib.sha256
            ).digest()
            
            # Add padding for comparison
            signature_bytes = base64.urlsafe_b64decode(
                signature_b64 + '=' * (4 - len(signature_b64) % 4)
            )
            
            if not hmac.compare_digest(expected_signature_bytes, signature_bytes):
                return None  # Signature invalid
            
            # Decode payload
            payload_json = base64.urlsafe_b64decode(
                payload_b64 + '=' * (4 - len(payload_b64) % 4)
            )
            payload = json.loads(payload_json)
            
            # Check replay protection
            request_id = payload.get('request_id')
            if request_id in self.seen_request_ids:
                return None  # Duplicate request
            self.seen_request_ids.add(request_id)
            
            return payload
            
        except (ValueError, KeyError, json.JSONDecodeError):
            return None

# Usage in FastAPI
from fastapi import Request, HTTPException

async def verify_worker(request: Request) -> Dict[str, Any]:
    auth_header = request.headers.get('Authorization', '')
    timestamp_header = request.headers.get('X-Request-Timestamp', '')
    
    if not auth_header or not timestamp_header:
        raise HTTPException(status_code=401, detail="Missing auth headers")
    
    verifier = WorkerVerifier(os.getenv('MODELRYX_INTERNAL_WORKER_KEY'))
    payload = verifier.verify_request(auth_header, timestamp_header)
    
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid signature")
    
    return payload
```

---

## Network Security

### TLS/HTTPS

**Development:**
- HTTP is acceptable for localhost testing
- Use unencrypted only for `127.0.0.1` and `::1`

**Production:**
- ✅ All worker-server communication must use HTTPS
- ✅ Valid TLS certificates (not self-signed in production)
- ✅ Minimum TLS 1.2
- ✅ Certificate pinning recommended

**Configuration:**
```bash
# Development (self-signed, development only)
openssl req -x509 -newkey rsa:4096 -nodes -out cert.pem -keyout key.pem -days 365

# Production (use Let's Encrypt, cloud provider, etc.)
# See DEPLOYMENT.md for HTTPS setup
```

### Worker URL Registration

Workers are registered by URL in the Modelryx configuration:

```env
# Registered workers
MODELRYX_STEP_WORKER_URL=http://step-worker:8080
MODELRYX_FREECAD_WORKER_URL=http://freecad:8090
```

**Validation:**
- ✅ Only registered worker URLs accept jobs
- ✅ Requests from unknown workers are rejected
- ✅ Admin can add/remove workers via API
- ✅ Worker URLs are validated against DNS/network ACLs

---

## Best Practices

### Development Workers

```bash
# Generate new key for development
export MODELRYX_INTERNAL_WORKER_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")

# Start with unencrypted HTTP (development only)
docker run -e MODELRYX_INTERNAL_WORKER_KEY=$MODELRYX_INTERNAL_WORKER_KEY \
  -p 8080:8080 modelryx-step-worker
```

### Production Workers

```bash
# 1. Generate secure key (do once, never regenerate)
export MODELRYX_INTERNAL_WORKER_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")

# 2. Store in secrets manager
aws secretsmanager create-secret --name modelryx/worker-key \
  --secret-string "$MODELRYX_INTERNAL_WORKER_KEY"

# 3. Deploy with HTTPS and certificate pinning
docker run -e MODELRYX_INTERNAL_WORKER_KEY=$MODELRYX_INTERNAL_WORKER_KEY \
  -e MODELRYX_SERVER_URL=https://server.example.com \
  -e MODELRYX_VERIFY_SSL=true \
  -e MODELRYX_CA_BUNDLE=/etc/ssl/certs/ca-bundle.crt \
  -v /etc/ssl/certs:/etc/ssl/certs:ro \
  -p 8080:8080 modelryx-step-worker
```

### Key Rotation

1. **Plan rotation window** - Schedule during low-traffic period
2. **Generate new key** - Same process as initial generation
3. **Deploy new key to workers** - Update configuration, restart
4. **Update server** - Set new key, keep old key for grace period
5. **Monitor logs** - Verify all workers authenticate successfully
6. **Remove old key** - After grace period (e.g., 24 hours)

```bash
# Rotation process
NEW_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
KEYS="${MODELRYX_INTERNAL_WORKER_KEY}:${NEW_KEY}"

# During transition (server accepts both old and new)
export MODELRYX_INTERNAL_WORKER_KEYS=$KEYS

# After workers updated and verified
export MODELRYX_INTERNAL_WORKER_KEY=$NEW_KEY
```

---

## Troubleshooting

### "Invalid signature" errors

**Causes:**
- Wrong worker key (mismatch between worker and server)
- Clock skew (worker and server time not synchronized)
- Malformed signature encoding (padding issues)
- Message tampering (packet loss, proxy interference)

**Fix:**
```bash
# Verify key matches
echo $MODELRYX_INTERNAL_WORKER_KEY

# Sync time
ntpdate pool.ntp.org  # Or use systemd-timesyncd

# Check logs
docker logs <worker-container> | grep -i auth
docker logs <server-container> | grep -i signature
```

### Worker heartbeat failing

**Causes:**
- Network connectivity issues
- Firewall blocking traffic
- Incorrect worker URL configuration
- TLS certificate issues

**Diagnosis:**
```bash
# Test worker connectivity
curl -v http://server:8000/health/db

# Check worker is running
docker ps | grep worker

# View logs
docker logs <worker-container>
```

---

## Security Checklist

- [ ] `MODELRYX_INTERNAL_WORKER_KEY` generated with cryptographic randomness
- [ ] Key is 32+ bytes (256+ bits)
- [ ] Key stored in environment variables or secrets manager
- [ ] Key not logged, not in code, not in version control
- [ ] Workers use HTTPS in production
- [ ] Request timestamps validated (5-minute window)
- [ ] Replay protection via request ID tracking
- [ ] HMAC signatures verified on every request
- [ ] Worker URLs registered and validated
- [ ] Network firewalls restrict worker-server traffic
- [ ] TLS certificates valid and not self-signed (production)
- [ ] Logs reviewed for authentication failures
- [ ] Key rotation plan documented
- [ ] Incident response plan for compromised keys
- [ ] Security audit conducted before launch

---

## References

- [NIST Cryptographic Key Management](https://csrc.nist.gov/publications/detail/sp/800-57/part-1/final)
- [OWASP Authentication Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html)
- [RFC 2104 - HMAC](https://tools.ietf.org/html/rfc2104)
- [RFC 7231 - HTTP Authorization](https://tools.ietf.org/html/rfc7231#section-6.3.1)
