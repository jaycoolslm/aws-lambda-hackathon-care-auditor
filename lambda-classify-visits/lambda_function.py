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

# Initialize AWS clients
# It's a good practice to initialize clients outside the handler
s3_client = boto3.client('s3')
bedrock_client = boto3.client("bedrock-runtime", region_name="eu-west-2")
dynamodb = boto3.resource('dynamodb', region_name='eu-west-2')

# DynamoDB table configuration
DYNAMODB_TABLE_NAME = "awslambdahackathoncarelogs"
dynamodb_table = dynamodb.Table(DYNAMODB_TABLE_NAME)

# Bedrock model configuration
MODEL_ID = "amazon.titan-text-express-v1"

# --- [MODIFIED] Helper function for parallel processing ---
def process_record(args):
    """
    Wrapper function to classify a single record and handle potential errors.
    This function is designed to be called by the ThreadPoolExecutor.
    """
    idx, visit_record, batch_id = args
    try:
        note_text = visit_record.get('note', '')
        if not note_text.strip():
            logger.warning(f"Record {idx} has an empty note, skipping classification.")
            return None

        classification_result = classify_visit_note(note_text)
        
        # Prepare the item for DynamoDB
        item = {
            'id': str(idx),  # Partition key (convert to string)
            'batch_id': str(batch_id),  # Sort key (ensure string)
            'ai_classification': classification_result,
            'timestamp': datetime.now().isoformat(),
            'client': str(visit_record.get('client', '')),
            'care_pro': str(visit_record.get('care_pro', '')),
            'visit_date': str(visit_record.get('visit_date', '')),
            'note': note_text,
        }
        return item, classification_result
    except Exception as e:
        logger.error(f"Failed to process record {idx}. Error: {e}")
        logger.error(traceback.format_exc())
        return None


def classify_visit_note(note):
    """
    Classify a visit note using Amazon Bedrock.
    Returns: 'red', 'amber', or 'green'
    """
    # This function remains largely the same but is now called concurrently.
    if not note or not note.strip():
        logger.warning("Empty note provided for classification")
        return 'green'

    prompt = f"""You are a healthcare professional reviewing care visit notes. Please classify the following visit note into one of three categories based on the level of concern:

RED: Urgent/critical issues requiring immediate attention (safety concerns, medical emergencies, serious incidents, safeguarding issues)
AMBER: Moderate concerns that need follow-up (minor health changes, care plan adjustments needed, family concerns)
GREEN: Routine visit with no significant concerns (normal care delivery, positive outcomes, standard activities)

Visit Note: "{note.strip()}"

Classification (respond with only RED, AMBER, or GREEN):"""

    native_request = {
        "inputText": prompt,
        "textGenerationConfig": {
            "maxTokenCount": 10,
            "temperature": 0.1,
        },
    }
    request = json.dumps(native_request)

    try:
        response = bedrock_client.invoke_model(modelId=MODEL_ID, body=request)
        model_response = json.loads(response["body"].read())
        response_text = model_response["results"][0]["outputText"].strip().lower()

        if 'red' in response_text:
            return 'red'
        elif 'amber' in response_text:
            return 'amber'
        elif 'green' in response_text:
            return 'green'
        else:
            logger.warning(f"Unexpected classification response: {response_text}, defaulting to amber")
            return 'amber'
    except (ClientError, Exception) as e:
        logger.error(f"ERROR: Can't invoke Bedrock model '{MODEL_ID}'. Reason: {e}")
        logger.error(f"Classification error stack trace: {traceback.format_exc()}")
        return 'amber'

# --- [NEW] Scalable function to write to DynamoDB in batches ---
def batch_write_to_dynamodb(items_to_write):
    """
    Writes a list of items to DynamoDB using a batch writer for efficiency.
    """
    if not items_to_write:
        logger.info("No items to write to DynamoDB.")
        return 0
    
    try:
        with dynamodb_table.batch_writer() as batch:
            for item in items_to_write:
                batch.put_item(Item=item)
        logger.info(f"Successfully wrote {len(items_to_write)} items to DynamoDB.")
        return len(items_to_write)
    except ClientError as e:
        logger.error(f"DynamoDB ClientError during batch write: {e}")
        logger.error(f"Error code: {e.response.get('Error', {}).get('Code', 'Unknown')}")
        logger.error(f"Error message: {e.response.get('Error', {}).get('Message', 'Unknown')}")
        return 0
    except Exception as e:
        logger.error(f"Unexpected error during batch write to DynamoDB: {e}")
        return 0

# --- [MODIFIED] Main Lambda handler for scalability ---
def lambda_handler(event, context):
    """
    AWS Lambda handler triggered by SNS notifications containing S3 events, optimized for parallel processing.
    """
    logger.info("Received event: " + json.dumps(event))
    
    for record in event['Records']:
        # Extract the SNS message
        if 'Sns' in record:
            # Parse the SNS message which contains the S3 event
            sns_message = json.loads(record['Sns']['Message'])
            
            # Process each S3 record in the SNS message
            for s3_record in sns_message['Records']:
                bucket = s3_record['s3']['bucket']['name']
                key = unquote_plus(s3_record['s3']['object']['key'])
                
                logger.info(f"Processing S3 object: s3://{bucket}/{key}")

                try:
                    extracted_batch_id = os.path.splitext(key)[0]
                    logger.info(f"Extracted batch ID: {extracted_batch_id}")
                    
                    response = s3_client.get_object(Bucket=bucket, Key=key)
                    content = response['Body'].read().decode('utf-8')
                    records = json.loads(content)
                    record_count = len(records)
                    logger.info(f"Found {record_count} records to process in batch '{extracted_batch_id}'")

                    if not records:
                        logger.warning("No records found to classify.")
                        continue

                    # --- Parallel Processing using ThreadPoolExecutor ---
                    items_for_dynamodb = []
                    classification_counts = {'red': 0, 'amber': 0, 'green': 0}
                    
                    # Create a list of arguments for each record to be processed
                    tasks = [(idx, visit_record, extracted_batch_id) for idx, visit_record in enumerate(records)]

                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        # map will apply the function to each item in tasks and return results in order
                        results = executor.map(process_record, tasks)

                    for result in results:
                        if result:
                            item, classification = result
                            items_for_dynamodb.append(item)
                            classification_counts[classification] += 1
                    
                    processed_count = len(items_for_dynamodb)
                    
                    # --- Batch write to DynamoDB ---
                    success_count = batch_write_to_dynamodb(items_for_dynamodb)

                    # --- Log summary ---
                    logger.info("=== PROCESSING SUMMARY ===")
                    logger.info(f"✅ Successfully processed and saved: {success_count}/{record_count} records")
                    if processed_count != record_count:
                         logger.warning(f"⚠️ {record_count - processed_count} records failed during classification step.")
                    if success_count < processed_count:
                        logger.error(f"❌ {processed_count - success_count} classified records failed to write to DynamoDB.")

                    logger.info(f"🔴 Red classifications: {classification_counts['red']}")
                    logger.info(f"🟡 Amber classifications: {classification_counts['amber']}")
                    logger.info(f"🟢 Green classifications: {classification_counts['green']}")

                except json.JSONDecodeError as json_error:
                    logger.error(f"Failed to parse JSON from s3://{bucket}/{key}: {json_error}")
                    continue # Move to the next S3 record if any
                except Exception as e:
                    logger.error(f"Lambda function failed for s3://{bucket}/{key}: {str(e)}")
                    logger.error(f"Full error stack trace: {traceback.format_exc()}")
                    # Depending on requirements, you might want to re-raise the exception
                    # to have the Lambda invocation marked as failed.
                    continue
        else:
            logger.warning("Record doesn't contain SNS message")

    return {
        'statusCode': 200,
        'body': json.dumps({
            'message': 'Processing complete.',
            'processed_objects': len(event.get('Records', []))
        })
    }