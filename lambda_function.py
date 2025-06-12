import boto3
import json
import os
import logging
import traceback
from urllib.parse import unquote_plus
from botocore.exceptions import ClientError
from datetime import datetime

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
s3_client = boto3.client('s3')
bedrock_client = boto3.client("bedrock-runtime", region_name="eu-west-2")
dynamodb_client = boto3.client('dynamodb', region_name='eu-west-2')

# DynamoDB table configuration
DYNAMODB_TABLE_NAME = "awslambdahackathoncarelogs"

# Bedrock model configuration
MODEL_ID = "amazon.titan-text-express-v1"

def classify_visit_note(note):
    """
    Classify a visit note using Amazon Bedrock.
    Returns: 'red', 'amber', or 'green'
    """
    if not note or not note.strip():
        logger.warning("Empty note provided for classification")
        return 'green'  # Default to green for empty notes
    
    # Define the classification prompt
    prompt = f"""You are a healthcare professional reviewing care visit notes. Please classify the following visit note into one of three categories based on the level of concern:

RED: Urgent/critical issues requiring immediate attention (safety concerns, medical emergencies, serious incidents, safeguarding issues)
AMBER: Moderate concerns that need follow-up (minor health changes, care plan adjustments needed, family concerns)  
GREEN: Routine visit with no significant concerns (normal care delivery, positive outcomes, standard activities)

Visit Note: "{note.strip()}"

Classification (respond with only RED, AMBER, or GREEN):"""

    # Format the request payload using Titan's native structure
    native_request = {
        "inputText": prompt,
        "textGenerationConfig": {
            "maxTokenCount": 10,  # We only need a short response
            "temperature": 0.1,   # Low temperature for consistent classification
        },
    }

    # Convert the native request to JSON
    request = json.dumps(native_request)

    try:
        # Invoke the model with the request
        response = bedrock_client.invoke_model(modelId=MODEL_ID, body=request)
        
        # Decode the response body
        model_response = json.loads(response["body"].read())
        
        # Extract the response text
        response_text = model_response["results"][0]["outputText"].strip().lower()
        
        # Map response to our classification system
        if 'red' in response_text:
            classification = 'red'
        elif 'amber' in response_text:
            classification = 'amber'
        elif 'green' in response_text:
            classification = 'green'
        else:
            logger.warning(f"Unexpected classification response: {response_text}, defaulting to amber")
            classification = 'amber'  # Default to amber if unclear
            
        logger.info(f"Bedrock classification result: {classification} (raw response: {response_text})")
        return classification

    except (ClientError, Exception) as e:
        logger.error(f"ERROR: Can't invoke Bedrock model '{MODEL_ID}'. Reason: {e}")
        logger.error(f"Classification error stack trace: {traceback.format_exc()}")
        # Return amber as a safe default when classification fails
        return 'amber'

def write_to_dynamodb(batch_id, visit_record, classification):
    """
    Write a classified visit log record to DynamoDB.
    
    Args:
        batch_id (str): The batch ID to use as primary key
        visit_record (dict): The visit log record data
        classification (str): The AI classification result ('red', 'amber', 'green')
    
    Returns:
        bool: True if successful, False otherwise
    """
    if not batch_id:
        logger.error("Cannot write to DynamoDB: batch_id is required")
        return False
    
    try:
        # Prepare the item for DynamoDB
        item = {
            'batch_id': {'S': str(batch_id)},  # Primary key
            'ai_classification': {'S': classification},
            'timestamp': {'S': json.dumps(datetime.now().isoformat())},
        }
        
        # Add visit record fields if they exist
        if 'client' in visit_record:
            item['client'] = {'S': str(visit_record['client'])}
        if 'carer' in visit_record:
            item['carer'] = {'S': str(visit_record['carer'])}
        if 'date' in visit_record:
            item['visit_date'] = {'S': str(visit_record['date'])}
        if 'note' in visit_record:
            item['note'] = {'S': str(visit_record['note'])}
        if 'classification' in visit_record:
            item['classification'] = {'S': str(visit_record['classification'])}
        
        # Write to DynamoDB
        logger.info(f"Writing classified record to DynamoDB table: {DYNAMODB_TABLE_NAME}")
        logger.info(f"Item batch_id: {batch_id}, classification: {classification}")
        
        response = dynamodb_client.put_item(
            TableName=DYNAMODB_TABLE_NAME,
            Item=item
        )
        
        logger.info(f"Successfully wrote record to DynamoDB. ResponseMetadata: {response.get('ResponseMetadata', {})}")
        return True
        
    except ClientError as e:
        logger.error(f"DynamoDB ClientError: {e}")
        logger.error(f"Error code: {e.response.get('Error', {}).get('Code', 'Unknown')}")
        logger.error(f"Error message: {e.response.get('Error', {}).get('Message', 'Unknown')}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error writing to DynamoDB: {e}")
        logger.error(f"DynamoDB write error stack trace: {traceback.format_exc()}")
        return False

def lambda_handler(event, context):
    """
    AWS Lambda handler triggered by S3 object creation.
    Expected S3 object key format: {batchId}.json (flat file structure)
    """
    
    try:
        # Process each S3 record in the event
        for record in event['Records']:
            bucket = record['s3']['bucket']['name']
            key = unquote_plus(record['s3']['object']['key'])
            
            logger.info(f"Processing S3 object: s3://{bucket}/{key}")
            
            # Extract batch ID from S3 object key (simplified - filename is the batch ID)
            extracted_batch_id = None
            original_filename = None
            
            try:
                # For flat file structure, the key is just the filename
                original_filename = key
                # Remove file extension to get batch ID
                extracted_batch_id = os.path.splitext(original_filename)[0]
                
                logger.info(f"Successfully parsed S3 key - Batch ID: {extracted_batch_id}, File: {original_filename}")
                    
            except Exception as parse_ex:
                logger.error(f"Could not parse batch ID from S3 key '{key}': {parse_ex}")
                logger.error(f"Parse error stack trace: {traceback.format_exc()}")
                extracted_batch_id = None
            
            # Download and read the JSON content from S3
            try:
                logger.info(f"Downloading S3 object content...")
                response = s3_client.get_object(Bucket=bucket, Key=key)
                content = response['Body'].read().decode('utf-8')
                
                # Parse JSON content
                records = json.loads(content)
                record_count = len(records)
                logger.info(f"Successfully parsed S3 object content. Found {record_count} records to process.")
                
                # Log extracted information (MVP - just console logging)
                logger.info("=== EXTRACTED INFORMATION ===")
                logger.info(f"S3 Bucket: {bucket}")
                logger.info(f"S3 Key: {key}")
                logger.info(f"Batch ID: {extracted_batch_id}")
                logger.info(f"Original Filename: {original_filename}")
                logger.info(f"Total Records: {record_count}")
                
                # Log sample records for debugging (first 3 records)
                logger.info("=== SAMPLE RECORDS ===")
                sample_records = records[:3] if len(records) >= 3 else records
                for idx, sample_record in enumerate(sample_records):
                    logger.info(f"Record {idx + 1}: {json.dumps(sample_record, indent=2)}")
                
                if len(records) > 3:
                    logger.info(f"... and {len(records) - 3} more records")
                
                # Log record structure analysis
                if records:
                    first_record = records[0]
                    logger.info("=== RECORD STRUCTURE ANALYSIS ===")
                    logger.info(f"Record keys: {list(first_record.keys())}")
                    
                    # Log key field types and sample values
                    for field_name in ['client', 'carer', 'date', 'note', 'classification']:
                        if field_name in first_record:
                            value = first_record[field_name]
                            logger.info(f"{field_name}: {type(value).__name__} = '{str(value)[:100]}{'...' if len(str(value)) > 100 else ''}'")
                        else:
                            logger.info(f"{field_name}: NOT PRESENT")
                
                # NEW: Classify the first record using Bedrock and write to DynamoDB
                if records and len(records) > 0:
                    logger.info("=== CLASSIFICATION AND DYNAMODB WRITE (FIRST RECORD) ===")
                    first_record = records[0]
                    note_text = first_record.get('note', '')
                    
                    if note_text:
                        logger.info(f"Classifying note: '{note_text[:200]}{'...' if len(note_text) > 200 else ''}'")
                        classification_result = classify_visit_note(note_text)
                        logger.info(f"Classification result: {classification_result}")
                        
                        # Add classification to the record for logging
                        first_record_with_classification = first_record.copy()
                        first_record_with_classification['ai_classification'] = classification_result
                        
                        logger.info(f"First record with classification: {json.dumps(first_record_with_classification, indent=2)}")
                        
                        # Write classified record to DynamoDB
                        if extracted_batch_id:
                            logger.info("=== WRITING TO DYNAMODB ===")
                            dynamodb_success = write_to_dynamodb(extracted_batch_id, first_record, classification_result)
                            
                            if dynamodb_success:
                                logger.info("✅ Successfully wrote classified record to DynamoDB")
                            else:
                                logger.error("❌ Failed to write classified record to DynamoDB")
                        else:
                            logger.warning("⚠️  Cannot write to DynamoDB: No valid batch_id extracted")
                            
                    else:
                        logger.warning("First record has no 'note' field to classify")
                else:
                    logger.warning("No records found to classify")
                
                logger.info("=== MVP PROCESSING COMPLETE ===")
                logger.info(f"Successfully processed S3 object: s3://{bucket}/{key}")
                
            except json.JSONDecodeError as json_error:
                logger.error(f"Failed to parse JSON content from S3 object: {json_error}")
                raise
            except Exception as s3_error:
                logger.error(f"Failed to download or process S3 object: {s3_error}")
                logger.error(f"S3 error stack trace: {traceback.format_exc()}")
                raise
                
    except Exception as e:
        logger.error(f"Lambda function failed: {str(e)}")
        logger.error(f"Full error stack trace: {traceback.format_exc()}")
        raise
    
    return {
        'statusCode': 200,
        'body': json.dumps({
            'message': 'Successfully processed S3 trigger',
            'processed_objects': len(event['Records'])
        })
    }
