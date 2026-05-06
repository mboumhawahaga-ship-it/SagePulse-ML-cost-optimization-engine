# Action Lambda — exécutée uniquement après approbation humaine

import json
import os
from datetime import datetime, timezone

import boto3
from aws_lambda_powertools import Logger
from botocore.exceptions import ClientError

logger = Logger(service="ml-cost-optimizer")


def get_sagemaker_client():
    return boto3.client(
        "sagemaker", region_name=os.environ.get("AWS_REGION", "eu-west-1")
    )


def get_sns_client():
    return boto3.client(
        "sns", region_name=os.environ.get("AWS_REGION", "eu-west-1")
    )


def stop_notebook(notebook_name):
    """
    Arrête un notebook SageMaker (stop uniquement, pas de suppression).
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    try:
        get_sagemaker_client().stop_notebook_instance(
            NotebookInstanceName=notebook_name
        )
        logger.info(f"✅ [{timestamp}] Notebook arrêté : {notebook_name}")
        return {
            "resource": notebook_name,
            "action": "stop_notebook",
            "status": "success",
            "timestamp": timestamp,
        }
    except ClientError as e:
        logger.error(f"❌ [{timestamp}] Échec arrêt notebook {notebook_name} : {e}")
        return {
            "resource": notebook_name,
            "action": "stop_notebook",
            "status": "error",
            "error": str(e),
            "timestamp": timestamp,
        }


def notify_idle_endpoint(endpoint_name):
    """
    Envoie une notification SNS pour un endpoint idle.
    Aucune suppression — la décision appartient aux MLOps.
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    sns_topic_arn = os.environ.get("SNS_TOPIC_ARN")

    if not sns_topic_arn:
        logger.warning(f"⚠️ SNS_TOPIC_ARN non configuré, notification ignorée pour {endpoint_name}")
        return {
            "resource": endpoint_name,
            "action": "notify_idle_endpoint",
            "status": "skipped",
            "timestamp": timestamp,
        }

    try:
        get_sns_client().publish(
            TopicArn=sns_topic_arn,
            Subject=f"[SagePulse] Endpoint idle détecté : {endpoint_name}",
            Message=(
                f"L'endpoint '{endpoint_name}' n'a reçu aucune invocation depuis 4h.\n\n"
                f"Actions possibles :\n"
                f"  - Supprimer manuellement si le modèle n'est plus nécessaire\n"
                f"  - Configurer un auto-scaling avec minimum 0 instance\n"
                f"  - Conserver si des pics de trafic sont attendus\n\n"
                f"Timestamp : {timestamp}"
            ),
        )
        logger.info(f"✅ [{timestamp}] Notification envoyée pour endpoint idle : {endpoint_name}")
        return {
            "resource": endpoint_name,
            "action": "notify_idle_endpoint",
            "status": "notified",
            "timestamp": timestamp,
        }
    except ClientError as e:
        logger.error(f"❌ [{timestamp}] Échec notification endpoint {endpoint_name} : {e}")
        return {
            "resource": endpoint_name,
            "action": "notify_idle_endpoint",
            "status": "error",
            "error": str(e),
            "timestamp": timestamp,
        }


def handler(event, context):
    """
    Point d'entrée Lambda — exécute les actions uniquement si approuvé.

    Event attendu :
        {
            "approved": true | false,
            "idle_resources": {
                "notebooks": ["notebook-1"],
                "endpoints": ["endpoint-1"]
            }
        }
    """
    logger.info("🚀 Action Lambda - Démarrage")

    try:
        approved = event.get("approved", False)
        idle_resources = event.get("idle_resources", {})
        notebooks = idle_resources.get("notebooks", [])
        endpoints = idle_resources.get("endpoints", [])

        if not approved:
            logger.info("❌ Action refusée par l'humain — aucune ressource touchée")
            return {
                "statusCode": 200,
                "body": json.dumps({"success": True, "approved": False, "actions": []}),
            }

        if not notebooks and not endpoints:
            logger.info("ℹ️ Aucune ressource idle à traiter")
            return {
                "statusCode": 200,
                "body": json.dumps({"success": True, "approved": True, "actions": []}),
            }

        results = []
        for name in notebooks:
            results.append(stop_notebook(name))
        for name in endpoints:
            results.append(notify_idle_endpoint(name))

        success_count = sum(1 for r in results if r["status"] in ("success", "notified"))
        logger.info(f"✅ {success_count}/{len(results)} actions réussies")

        return {
            "statusCode": 200,
            "body": json.dumps({"success": True, "approved": True, "actions": results}),
        }

    except Exception as e:
        logger.error(f"❌ Erreur fatale : {e}")
        return {
            "statusCode": 500,
            "body": json.dumps({"success": False, "error": str(e)}),
        }
