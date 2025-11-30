# Tyk Route Operator

Kubernetes Operator for managing Tyk API Gateway routes via CRDs.

## Features

- Define Tyk routes using Kubernetes CRDs
- Automatic rollout restart on route changes
- ConfigMap-based API definition storage
- Comprehensive validation (API definition, ConfigMap, deployment)
- Namespace-scoped RBAC

## Quick Start

### Prerequisites

- Kubernetes 1.19+
- Tyk Gateway deployed
- kubectl configured

### Installation

```bash
# Apply CRD
kubectl apply -f manifests/crd.yaml

# Create ConfigMap
kubectl create configmap tyk-routes --from-literal=_placeholder='{}' -n default

# Apply RBAC & Secret
kubectl apply -f manifests/secret.yaml
kubectl apply -f manifests/rbac.yaml

# Deploy operator
kubectl apply -f manifests/deployment.yaml

# Verify
kubectl get pods -l app=tyk-route-operator
kubectl logs -f deployment/tyk-route-operator
```

### Create Route

```yaml
apiVersion: vourteen14.labs/v1
kind: TykRoute
metadata:
  name: users-api
spec:
  target:
    configMapName: tyk-routes
    tykDeployment: tyk-gateway  # optional: triggers pod restart

  apiDefinition:
    name: "Users API"
    use_keyless: true
    proxy:
      listen_path: "/api/users/"
      target_url: "http://user-service:8080"
```

```bash
kubectl apply -f route.yaml
kubectl get tykroute users-api
```

## Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `OPERATOR_NAMESPACE` | Operator namespace | `default` |
| `HEALTH_PORT` | Health check port | `8081` |

### CRD Spec

```yaml
spec:
  target:
    configMapName: string       # Required
    namespace: string           # Optional, defaults to resource namespace
    tykDeployment: string       # Optional, triggers rollout restart

  apiDefinition:
    # Standard Tyk API Definition
```

## Architecture

```
TykRoute CRD
      ↓
Tyk Route Operator validates & updates ConfigMap
      ↓
kubectl rollout restart (if tykDeployment specified)
      ↓
Tyk Gateway pods restart with new config
```

## Examples

See `manifests/examples/` for:
- `tykoperator-simple.yaml` - Minimal config
- `tykoperator-basic.yaml` - With rollout restart
- `tykoperator-with-rollout.yaml` - JWT auth + rate limiting
- `tykoperator-advanced.yaml` - Production setup

## Troubleshooting

### Check Status
```bash
kubectl describe tykroute users-api
kubectl logs deployment/tyk-route-operator
```

### Common Issues

**ConfigMap not updating**: Verify ConfigMap exists and operator has permissions
```bash
kubectl get configmap tyk-routes
kubectl auth can-i update configmaps --as=system:serviceaccount:default:tyk-route-operator
```

**Rollout not triggering**: Check `tykDeployment` field and deployment exists
```bash
kubectl get deployment tyk-gateway
```

### Health Check
```bash
kubectl port-forward deployment/tyk-route-operator 8081:8081
curl http://localhost:8081/healthz
```

## License

MIT

## Author

vourteen14
