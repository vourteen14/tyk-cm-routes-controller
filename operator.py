#!/usr/bin/env python3
"""Tyk Route Operator - Kubernetes Operator for managing Tyk API Gateway routes"""

import kopf
import logging
import json
import http.server
import threading
import requests
import os
import time
import hashlib
from kubernetes import client, config
from kubernetes.client.rest import ApiException
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

try:
    config.load_incluster_config()
    logger.info("Loaded in-cluster config")
except:
    config.load_kube_config()
    logger.info("Loaded local kubeconfig")

v1 = client.CoreV1Api()
apps_v1 = client.AppsV1Api()
custom_api = client.CustomObjectsApi()

GROUP = "vourteen14.labs"
VERSION = "v1"
TYK_SECRET = os.getenv('TYK_SECRET', 'change-me')
TYK_ADMIN_PORT = os.getenv('TYK_ADMIN_PORT', '8080')
OPERATOR_NAMESPACE = os.getenv('OPERATOR_NAMESPACE', 'default')
HEALTH_PORT = int(os.getenv('HEALTH_PORT', '8081'))

logger.info(f"Operator configured for namespace: {OPERATOR_NAMESPACE}")

class HealthHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ['/healthz', '/livez', '/readyz']:
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            
            health_status = {
                'status': 'healthy',
                'timestamp': datetime.utcnow().isoformat(),
                'version': '1.0.0'
            }
            self.wfile.write(json.dumps(health_status).encode())
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        return

def start_health_server():
    try:
        server = http.server.HTTPServer(('0.0.0.0', HEALTH_PORT), HealthHandler)
        logger.info(f"Health server started on :{HEALTH_PORT}")
        server.serve_forever()
    except Exception as e:
        logger.error(f"Health server error: {e}")

def validate_target_configmap(cm_name, namespace):
    try:
        v1.read_namespaced_config_map(name=cm_name, namespace=namespace)
        logger.info(f"ConfigMap validation passed: {namespace}/{cm_name}")
        return True, None
    except ApiException as e:
        if e.status == 404:
            error_msg = f"ConfigMap {namespace}/{cm_name} not found"
            logger.error(f"{error_msg}")
            return False, error_msg
        elif e.status == 403:
            error_msg = f"No permission to access ConfigMap {namespace}/{cm_name}"
            logger.error(f"{error_msg}")
            return False, error_msg
        else:
            error_msg = f"Error accessing ConfigMap: {e.reason}"
            logger.error(f"{error_msg}")
            return False, error_msg

def validate_tyk_deployment(deployment_name, namespace):
    try:
        deployment = apps_v1.read_namespaced_deployment(
            name=deployment_name,
            namespace=namespace
        )

        if deployment.status.ready_replicas and deployment.status.ready_replicas > 0:
            logger.info(f"Tyk deployment {deployment_name} is ready ({deployment.status.ready_replicas} replicas)")
        else:
            logger.warning(f"Tyk deployment {deployment_name} has no ready replicas")

        return True, None

    except ApiException as e:
        if e.status == 404:
            error_msg = f"Tyk deployment {deployment_name} not found in namespace {namespace}"
            logger.error(f"{error_msg}")
            return False, error_msg
        error_msg = f"Error checking Tyk deployment: {e.reason}"
        logger.error(f"{error_msg}")
        return False, error_msg

def validate_api_definition(api_def):
    errors = []
    if 'name' not in api_def:
        errors.append("apiDefinition.name is required")
    
    if 'proxy' not in api_def:
        errors.append("apiDefinition.proxy is required")
    else:
        if 'listen_path' not in api_def['proxy']:
            errors.append("apiDefinition.proxy.listen_path is required")
        if 'target_url' not in api_def['proxy']:
            errors.append("apiDefinition.proxy.target_url is required")
        target_url = api_def['proxy'].get('target_url', '')
        if not target_url.startswith(('http://', 'https://')):
            errors.append(f"Invalid target_url: {target_url} (must start with http:// or https://")
    listen_path = api_def.get('proxy', {}).get('listen_path', '')
    if listen_path and not listen_path.startswith('/'):
        errors.append(f"listen_path must start with /: {listen_path}")
    
    if errors:
        error_msg = "; ".join(errors)
        logger.error(f"API definition validation failed: {error_msg}")
        return False, error_msg

    logger.info("API definition validation passed")
    return True, None

def validate_listen_path_unique(listen_path, cm_name, namespace, exclude_filename=None):
    try:
        cm = v1.read_namespaced_config_map(name=cm_name, namespace=namespace)
        
        if not cm.data:
            return True, None
        
        for filename, content in cm.data.items():
            if exclude_filename and filename == exclude_filename:
                continue
                
            try:
                route_config = json.loads(content)
                existing_path = route_config.get('proxy', {}).get('listen_path')
                
                if existing_path == listen_path:
                    error_msg = f"listen_path {listen_path} already exists in {filename}"
                    logger.error(f"{error_msg}")
                    return False, error_msg

            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON in {filename}, skipping validation")
                continue

        logger.info(f"listen_path {listen_path} is unique")
        return True, None

    except ApiException:
        return True, None

def update_status_safe(group, version, namespace, plural, name, status_data, retry=3):
    for attempt in range(retry):
        try:
            obj = custom_api.get_namespaced_custom_object(
                group=group,
                version=version,
                namespace=namespace,
                plural=plural,
                name=name
            )
            
            obj['status'] = status_data
            
            custom_api.patch_namespaced_custom_object_status(
                group=group,
                version=version,
                namespace=namespace,
                plural=plural,
                name=name,
                body=obj
            )
            
            logger.info(f"Status updated for {plural}/{name}: {status_data.get('state', 'unknown')}")
            return True

        except ApiException as e:
            if e.status == 404:
                logger.warning(f"Object {name} not found yet, retrying... (attempt {attempt+1}/{retry})")
                time.sleep(0.5 * (attempt + 1))
                continue
            elif e.status == 409:
                logger.warning(f"Conflict updating status, retrying... (attempt {attempt+1}/{retry})")
                time.sleep(0.2)
                continue
            else:
                logger.error(f"Error updating status: {e.status} - {e.reason}")
                if attempt == retry - 1:
                    raise
                time.sleep(0.5)
        except Exception as e:
            logger.error(f"Unexpected error updating status: {e}")
            if attempt == retry - 1:
                raise
            time.sleep(0.5)
    
    return False

def rollout_restart_deployment(deployment_name, namespace):
    try:
        logger.info(f"Triggering rollout restart: {namespace}/{deployment_name}")

        now = datetime.utcnow().isoformat()

        patch = {
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {
                            "tyk.vourteen14.labs/restartedAt": now
                        }
                    }
                }
            }
        }

        apps_v1.patch_namespaced_deployment(
            name=deployment_name,
            namespace=namespace,
            body=patch
        )

        logger.info(f"Rollout restart triggered successfully: {namespace}/{deployment_name}")
        return True

    except ApiException as e:
        if e.status == 404:
            logger.error(f"Deployment {deployment_name} not found in namespace {namespace}")
        else:
            logger.error(f"Failed to trigger rollout restart: {e.reason}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error triggering rollout restart: {e}")
        return False

def delete_from_configmap(configmap_name, namespace, filename):
    try:
        cm = v1.read_namespaced_config_map(name=configmap_name, namespace=namespace)

        if cm.data and filename in cm.data:
            del cm.data[filename]

            v1.replace_namespaced_config_map(
                name=configmap_name,
                namespace=namespace,
                body=cm
            )

            logger.info(f"Removed {filename} from {namespace}/{configmap_name}")
            return True
        else:
            logger.warning(f"File {filename} not found in ConfigMap")
            return False

    except ApiException as e:
        logger.error(f"Error removing from ConfigMap: {e.reason}")
        return False

@kopf.on.create(GROUP, VERSION, 'tykroutes')
def create_tyk_route(spec, name, namespace, **kwargs):
    logger.info(f"Creating TykRoute: {namespace}/{name}")

    try:
        target_cm = spec['target']['configMapName']
        target_ns = spec['target'].get('namespace', namespace)
        tyk_deployment = spec['target'].get('tykDeployment')
        api_def = spec['apiDefinition']

        logger.info(f"Target ConfigMap: {target_ns}/{target_cm}")
        logger.info(f"Tyk Deployment: {tyk_deployment if tyk_deployment else 'not specified'}")
        cm_valid, cm_error = validate_target_configmap(target_cm, target_ns)
        if not cm_valid:
            raise kopf.PermanentError(f"Target validation failed: {cm_error}")

        if tyk_deployment:
            deploy_valid, deploy_error = validate_tyk_deployment(tyk_deployment, target_ns)
            if not deploy_valid:
                raise kopf.PermanentError(f"Tyk deployment validation failed: {deploy_error}")

        api_valid, api_error = validate_api_definition(api_def)
        if not api_valid:
            raise kopf.PermanentError(f"API definition validation failed: {api_error}")
        listen_path = api_def['proxy']['listen_path']
        filename = f"{name}.json"

        path_valid, path_error = validate_listen_path_unique(
            listen_path, target_cm, target_ns, exclude_filename=filename
        )
        if not path_valid:
            raise kopf.PermanentError(f"Path conflict: {path_error}")

        logger.info("All validations passed")

        json_content = json.dumps(api_def, indent=2)
        try:
            cm = v1.read_namespaced_config_map(name=target_cm, namespace=target_ns)
        except ApiException as e:
            if e.status == 404:
                cm = client.V1ConfigMap(
                    metadata=client.V1ObjectMeta(
                        name=target_cm,
                        namespace=target_ns,
                        labels={
                            'app': 'tyk-gateway',
                            'managed-by': 'tyk-route-operator.vourteen14.labs'
                        }
                    ),
                    data={}
                )
            else:
                raise kopf.TemporaryError(f"Failed to read ConfigMap: {e.reason}", delay=30)

        if cm.data is None:
            cm.data = {}
        cm.data[filename] = json_content

        try:
            if hasattr(cm.metadata, 'resource_version') and cm.metadata.resource_version:
                v1.replace_namespaced_config_map(name=target_cm, namespace=target_ns, body=cm)
            else:
                v1.create_namespaced_config_map(namespace=target_ns, body=cm)
            logger.info(f"ConfigMap updated: {target_ns}/{target_cm} -> {filename}")
        except Exception as e:
            logger.error(f"Failed to update ConfigMap: {e}")
            raise kopf.TemporaryError(f"ConfigMap update failed: {e}", delay=30)

        rollout_triggered = False
        if tyk_deployment:
            rollout_triggered = rollout_restart_deployment(tyk_deployment, target_ns)
            if not rollout_triggered:
                logger.warning(f"Rollout restart failed for {tyk_deployment}, but ConfigMap was updated")

        message = f'Route successfully deployed to {target_cm}'
        if tyk_deployment and rollout_triggered:
            message += f' and rollout restart triggered for {tyk_deployment}'
        elif tyk_deployment and not rollout_triggered:
            message += f' (rollout restart failed for {tyk_deployment})'

        success_status = {
            'state': 'active',
            'message': message,
            'targetConfigMap': target_cm,
            'targetNamespace': target_ns,
            'tykDeployment': tyk_deployment if tyk_deployment else '',
            'filename': filename,
            'listenPath': listen_path,
            'lastUpdated': datetime.utcnow().isoformat(),
            'conditions': [{
                'type': 'Ready',
                'status': 'True',
                'lastTransitionTime': datetime.utcnow().isoformat() + 'Z',
                'reason': 'Created',
                'message': 'TykRoute successfully created and deployed'
            }]
        }

        update_status_safe(GROUP, VERSION, namespace, 'tykroutes', name, success_status)

        logger.info(f"TykRoute {namespace}/{name} created successfully")
        return success_status

    except kopf.PermanentError:
        raise

    except Exception as e:
        logger.error(f"Unexpected error creating TykOperator: {e}", exc_info=True)
        
        error_status = {
            'state': 'failed',
            'message': f'Creation failed: {str(e)}',
            'lastUpdated': datetime.utcnow().isoformat(),
            'conditions': [{
                'type': 'Ready',
                'status': 'False',
                'lastTransitionTime': datetime.utcnow().isoformat() + 'Z',
                'reason': 'CreationFailed',
                'message': str(e)
            }]
        }

        try:
            update_status_safe(GROUP, VERSION, namespace, 'tykroutes', name, error_status)
        except:
            pass

        raise kopf.PermanentError(f"Creation failed: {str(e)}")

@kopf.on.update(GROUP, VERSION, 'tykroutes')
def update_tyk_route(spec, name, namespace, old, new, diff, **kwargs):
    logger.info(f"Updating TykRoute: {namespace}/{name}")
    logger.info(f"Changes detected: {diff}")

    try:
        target_cm = spec['target']['configMapName']
        target_ns = spec['target'].get('namespace', namespace)
        tyk_deployment = spec['target'].get('tykDeployment')
        api_def = spec['apiDefinition']
        filename = f"{name}.json"

        logger.info(f"Target ConfigMap: {target_ns}/{target_cm}")
        logger.info(f"Tyk Deployment: {tyk_deployment if tyk_deployment else 'not specified'}")

        api_valid, api_error = validate_api_definition(api_def)
        if not api_valid:
            raise kopf.PermanentError(f"API definition validation failed: {api_error}")

        listen_path = api_def['proxy']['listen_path']
        path_valid, path_error = validate_listen_path_unique(
            listen_path, target_cm, target_ns, exclude_filename=filename
        )
        if not path_valid:
            raise kopf.PermanentError(f"Path conflict: {path_error}")

        json_content = json.dumps(api_def, indent=2)
        try:
            cm = v1.read_namespaced_config_map(name=target_cm, namespace=target_ns)
            if cm.data is None:
                cm.data = {}
            cm.data[filename] = json_content
            v1.replace_namespaced_config_map(name=target_cm, namespace=target_ns, body=cm)
            logger.info(f"ConfigMap updated: {target_ns}/{target_cm} -> {filename}")
        except Exception as e:
            logger.error(f"Failed to update ConfigMap: {e}")
            raise kopf.TemporaryError(f"ConfigMap update failed: {e}", delay=30)

        rollout_triggered = False
        if tyk_deployment:
            rollout_triggered = rollout_restart_deployment(tyk_deployment, target_ns)
            if not rollout_triggered:
                logger.warning(f"Rollout restart failed for {tyk_deployment}, but ConfigMap was updated")

        message = f'Route updated in {target_cm}'
        if tyk_deployment and rollout_triggered:
            message += f' and rollout restart triggered for {tyk_deployment}'
        elif tyk_deployment and not rollout_triggered:
            message += f' (rollout restart failed for {tyk_deployment})'

        success_status = {
            'state': 'active',
            'message': message,
            'targetConfigMap': target_cm,
            'targetNamespace': target_ns,
            'tykDeployment': tyk_deployment if tyk_deployment else '',
            'filename': filename,
            'listenPath': listen_path,
            'lastUpdated': datetime.utcnow().isoformat(),
            'conditions': [{
                'type': 'Ready',
                'status': 'True',
                'lastTransitionTime': datetime.utcnow().isoformat() + 'Z',
                'reason': 'Updated',
                'message': 'TykRoute successfully updated'
            }]
        }

        update_status_safe(GROUP, VERSION, namespace, 'tykroutes', name, success_status)

        logger.info(f"TykRoute {namespace}/{name} updated successfully")
        return success_status

    except kopf.PermanentError:
        raise

    except Exception as e:
        logger.error(f"Error updating TykOperator: {e}", exc_info=True)
        
        error_status = {
            'state': 'failed',
            'message': f'Update failed: {str(e)}',
            'lastUpdated': datetime.utcnow().isoformat(),
            'conditions': [{
                'type': 'Ready',
                'status': 'False',
                'lastTransitionTime': datetime.utcnow().isoformat() + 'Z',
                'reason': 'UpdateFailed',
                'message': str(e)
            }]
        }

        try:
            update_status_safe(GROUP, VERSION, namespace, 'tykroutes', name, error_status)
        except:
            pass

        raise kopf.TemporaryError(f"Update failed: {str(e)}", delay=30)

@kopf.on.delete(GROUP, VERSION, 'tykroutes')
def delete_tyk_route(spec, name, namespace, **kwargs):
    logger.info(f"Deleting TykRoute: {namespace}/{name}")

    try:
        target_cm = spec['target']['configMapName']
        target_ns = spec['target'].get('namespace', namespace)
        tyk_deployment = spec['target'].get('tykDeployment')
        filename = f"{name}.json"

        if delete_from_configmap(target_cm, target_ns, filename):
            logger.info(f"Removed {filename} from ConfigMap {target_cm}")

            if tyk_deployment:
                if rollout_restart_deployment(tyk_deployment, target_ns):
                    logger.info(f"Rollout restart triggered for {tyk_deployment} after deletion")
                else:
                    logger.warning(f"Rollout restart failed for {tyk_deployment} after deletion")

        logger.info(f"TykRoute {namespace}/{name} deleted successfully")

    except Exception as e:
        logger.warning(f"Error during cleanup: {e}")

@kopf.on.startup()
def configure(settings: kopf.OperatorSettings, **_):
    health_thread = threading.Thread(target=start_health_server, daemon=True)
    health_thread.start()

    settings.peering.priority = 100
    settings.persistence.finalizer = 'tyk-route-operator.vourteen14.labs/finalizer'
    settings.persistence.progress_storage = kopf.AnnotationsProgressStorage()
    settings.persistence.diffbase_storage = kopf.AnnotationsDiffBaseStorage(
        prefix='tyk-route-operator.vourteen14.labs',
        key='last-handled-configuration'
    )

    settings.posting.level = logging.INFO
    settings.watching.server_timeout = 600
    settings.watching.client_timeout = 660
    settings.watching.connect_timeout = 10
    settings.batching.idle_timeout = 5.0
    settings.batching.batch_window = 10.0

    logger.info("=" * 60)
    logger.info("Tyk Route Operator v1.0.0 - Starting up...")
    logger.info(f"Namespace: {OPERATOR_NAMESPACE}")
    logger.info(f"API Group: {GROUP}/{VERSION}")
    logger.info(f"Health Port: {HEALTH_PORT}")
    logger.info(f"Tyk Admin Port: {TYK_ADMIN_PORT}")
    logger.info("=" * 60)

def background_logger():
    while True:
        logger.info("Operator is alive and watching for changes...")
        time.sleep(300)

logger_thread = threading.Thread(target=background_logger, daemon=True)
logger_thread.start()

logger.info("Tyk Route Operator initialization complete")