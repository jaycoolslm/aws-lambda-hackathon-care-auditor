import boto3
import json
import os
import logging
import traceback
from urllib.parse import unquote_plus
from botocore.exceptions import ClientError
from datetime import datetime
import concurrent.futures

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients (outside handler for connection reuse)
s3_client = boto3.client("s3")
bedrock_client = boto3.client("bedrock-runtime", region_name="eu-west-2")
dynamodb = boto3.resource("dynamodb", region_name="eu-west-2")

# DynamoDB table configuration
DYNAMODB_TABLE_NAME = "awslambdahackathonsummaries"
dynamodb_table = dynamodb.Table(DYNAMODB_TABLE_NAME)

# Bedrock model configuration (using Titan Express for text generation)
MODEL_ID = "amazon.titan-text-express-v1"


def summarise_notes(notes):
    """Summarise a list of chronological visit notes into a concise paragraph."""
    if not notes:
        return "No summary available."

    # Build prompt with numbered, chronological notes
    bullet_notes = "\n".join([f"{idx+1}. {note}" for idx, note in enumerate(notes)])
    prompt = (
        "You are a healthcare professional summarising a client's home-care visit notes. "
        "Provide a concise summary (max 150 words) that highlights changes, concerns, and any "
        "trends over time. Use clear, professional language.\n\n"
        f"Visit Notes (oldest to newest):\n{bullet_notes}\n\nSummary:" 
    )

    native_request = {
        "inputText": prompt,
        "textGenerationConfig": {
            "maxTokenCount": 200,
            "temperature": 0.3,
        },
    }
    request_body = json.dumps(native_request)

    try:
        response = bedrock_client.invoke_model(modelId=MODEL_ID, body=request_body)
        model_response = json.loads(response["body"].read())
        summary_text = model_response["results"][0]["outputText"].strip()
        return summary_text
    except (ClientError, Exception) as e:
        logger.error(f"ERROR: Can't invoke Bedrock model '{MODEL_ID}'. Reason: {e}")
        logger.error(f"Summarisation error stack trace: {traceback.format_exc()}")
        return "Summary unavailable due to an error."


# Helper for parallel client processing
def process_client(args):
    """Aggregate notes for a single client, summarise them, and build an item for DynamoDB."""
    idx, client_name, client_records, batch_id = args
    try:
        # Sort records by visit_date (ascending chronological)
        sorted_records = sorted(
            client_records,
            key=lambda r: r.get("visit_date", "")
        )
        notes = [r.get("note", "").strip() for r in sorted_records if r.get("note", "").strip()]
        if not notes:
            logger.warning(f"Client '{client_name}' has no non-empty notes – skipping.")
            return None

        summary = summarise_notes(notes)

        latest_visit_date = max(r.get("visit_date", "") for r in sorted_records)
        item = {
            "batch_id": batch_id,
            "client": client_name,
            "latest_visit_date": latest_visit_date,
            "visit_count": len(sorted_records),
            "summary": summary,
            "timestamp": datetime.now().isoformat(),
        }
        return item
    except Exception as e:
        logger.error(f"Failed to process client '{client_name}'. Error: {e}")
        logger.error(traceback.format_exc())
        return None


def batch_write_to_dynamodb(items_to_write):
    """Writes items to DynamoDB efficiently via a batch writer."""
    if not items_to_write:
        logger.info("No summary items to write to DynamoDB.")
        return 0

    try:
        with dynamodb_table.batch_writer() as batch:
            for item in items_to_write:
                batch.put_item(Item=item)
        logger.info(f"Successfully wrote {len(items_to_write)} summary items to DynamoDB.")
        return len(items_to_write)
    except ClientError as e:
        logger.error(f"DynamoDB ClientError during batch write: {e}")
        logger.error(f"Error code: {e.response.get('Error', {}).get('Code', 'Unknown')}")
        logger.error(f"Error message: {e.response.get('Error', {}).get('Message', 'Unknown')}")
        return 0
    except Exception as e:
        logger.error(f"Unexpected error during batch write to DynamoDB: {e}")
        return 0


# Lambda handler

def lambda_handler(event, context):
    """Triggered by S3 object creation. Groups visit logs per client and summarises them."""
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = unquote_plus(record["s3"]["object"]["key"])
        logger.info(f"Processing S3 object for summarisation: s3://{bucket}/{key}")

        try:
            batch_id = os.path.splitext(key)[0]
            logger.info(f"Extracted batch ID for summarisation: {batch_id}")

            response = s3_client.get_object(Bucket=bucket, Key=key)
            content = response["Body"].read().decode("utf-8")
            visit_records = json.loads(content)
            logger.info(f"Loaded {len(visit_records)} visit records for summarisation.")

            # Group records by client
            client_map = {}
            for rec in visit_records:
                client = rec.get("client", "Unknown")
                client_map.setdefault(client, []).append(rec)

            # Prepare tasks for parallel processing
            tasks = [
                (idx, client_name, records, batch_id)
                for idx, (client_name, records) in enumerate(client_map.items())
            ]

            items_for_dynamodb = []
            with concurrent.futures.ThreadPoolExecutor() as executor:
                results = executor.map(process_client, tasks)

            for item in results:
                if item:
                    items_for_dynamodb.append(item)

            processed_count = len(items_for_dynamodb)
            success_count = batch_write_to_dynamodb(items_for_dynamodb)

            logger.info("=== SUMMARY PROCESSING COMPLETE ===")
            logger.info(f"✅ Summarised {processed_count} clients and wrote {success_count} items to DynamoDB.")
        except json.JSONDecodeError as json_error:
            logger.error(f"Failed to parse JSON from s3://{bucket}/{key}: {json_error}")
            continue
        except Exception as e:
            logger.error(f"Summarisation Lambda failed for s3://{bucket}/{key}: {str(e)}")
            logger.error(traceback.format_exc())
            continue

    return {
        "statusCode": 200,
        "body": json.dumps({
            "message": "Summarisation complete.",
            "processed_objects": len(event.get("Records", []))
        })
    }
