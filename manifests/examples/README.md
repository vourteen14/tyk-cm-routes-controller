# TykOperator Examples

This directory contains example manifests for TykOperator CRD.

## Prerequisites

Before applying these examples, ensure you have:

1. **TykOperator CRD installed**:
   ```bash
   kubectl apply -f ../crd.yaml
   ```

2. **Target ConfigMap created**:
   ```bash
   kubectl create configmap dynamic-api-routes -n default
   ```

3. **(Optional) Tyk Gateway deployment** running if you want automatic rollout restart:
   ```bash
   # Your Tyk deployment should exist
   kubectl get deployment tyk-gateway -n default
   ```

## Examples

### 1. Basic Example (`tykoperator-basic.yaml`)

Simple API proxy without authentication:

```yaml
spec:
  target:
    configMapName: dynamic-api-routes
    tykDeployment: tyk-gateway  # Optional: auto rollout restart

  apiDefinition:
    name: "Users API"
    use_keyless: true  # No authentication
    proxy:
      listen_path: "/api/users/"
      target_url: "http://user-service:8080"
```

**Apply**:
```bash
kubectl apply -f tykoperator-basic.yaml
```

**Features**:
- ✅ Simple HTTP proxy
- ✅ No authentication (keyless)
- ✅ Auto rollout restart on changes

---

### 2. Advanced Example (`tykoperator-advanced.yaml`)

Production-ready configuration with JWT auth, rate limiting, CORS, caching, etc:

```yaml
spec:
  target:
    configMapName: dynamic-api-routes
    tykDeployment: tyk-gateway

  apiDefinition:
    # JWT Authentication
    enable_jwt: true

    # Rate Limiting (100 req/min)
    global_rate_limit:
      rate: 100
      per: 60

    # Response Caching (5min)
    cache_options:
      enable_cache: true
      cache_timeout: 300

    # CORS configuration
    CORS:
      enable: true
      allowed_origins:
        - "https://example.com"
```

**Apply**:
```bash
kubectl apply -f tykoperator-advanced.yaml
```

**Features**:
- ✅ JWT Authentication
- ✅ Rate Limiting (100 req/min)
- ✅ Response Caching (5 min)
- ✅ CORS with multiple origins
- ✅ IP Whitelisting
- ✅ Request/Response header transformation
- ✅ URL rewriting
- ✅ Load balancing across 3 backends
- ✅ Custom middleware hooks

---

## Field Reference

### `spec.target`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `configMapName` | string | ✅ Yes | Name of ConfigMap to store Tyk API definitions |
| `namespace` | string | ❌ No | Namespace of ConfigMap (defaults to resource namespace) |
| `tykDeployment` | string | ❌ No | Name of Tyk Gateway deployment to trigger rollout restart |

### `spec.apiDefinition`

This is a standard [Tyk API Definition](https://tyk.io/docs/tyk-gateway-api/api-definition-objects/). Key fields:

| Field | Description |
|-------|-------------|
| `name` | Human-readable API name |
| `api_id` | Unique API identifier |
| `proxy.listen_path` | Path where API is exposed (e.g., `/api/users/`) |
| `proxy.target_url` | Backend service URL |
| `use_keyless` | Set `true` to disable authentication |
| `enable_jwt` | Enable JWT authentication |
| `global_rate_limit` | Rate limiting configuration |
| `cache_options` | Response caching settings |
| `CORS` | CORS configuration |

---

## How It Works

1. **Create/Update TykOperator resource**
   ```bash
   kubectl apply -f tykoperator-basic.yaml
   ```

2. **Operator watches for changes** and:
   - ✅ Validates API definition
   - ✅ Updates ConfigMap with new route
   - ✅ Triggers rollout restart (if `tykDeployment` specified)

3. **Tyk Gateway pods restart** and:
   - ✅ Mount updated ConfigMap
   - ✅ Load new API definitions
   - ✅ Start serving new routes

4. **Check status**:
   ```bash
   kubectl get tykoperator
   ```

   Output:
   ```
   NAME         STATE    TARGET CONFIGMAP      LISTEN PATH
   users-api    active   dynamic-api-routes    /api/users/
   ```

---

## Troubleshooting

### ConfigMap not updating?

Check operator logs:
```bash
kubectl logs -l app=tyk-operator --tail=50
```

### Rollout restart not triggering?

Verify `tykDeployment` field matches your deployment name:
```bash
kubectl get deployment -n default
```

### Status field empty?

Ensure CRD has status subresource:
```bash
kubectl get crd tykoperators.vourteen14.labs -o jsonpath='{.spec.versions[*].subresources}'
```

Should output: `{"status":{}}`

---

## Without Tyk Deployment

If you don't have a Tyk deployment (e.g., using external Tyk), **omit the `tykDeployment` field**:

```yaml
spec:
  target:
    configMapName: dynamic-api-routes
    # No tykDeployment - operator only updates ConfigMap
```

You'll need to manually reload Tyk or set up your own sync mechanism.
