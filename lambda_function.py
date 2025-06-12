import boto3
import json
import os
import logging
import traceback
from urllib.parse import unquote_plus

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize S3 client
s3_client = boto3.client('s3')

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
                
                # Basic validation for batch ID
                if not (extracted_batch_id and len(extracted_batch_id) > 5):
                    logger.warning(f"Extracted batch ID '{extracted_batch_id}' doesn't look valid.")
                    extracted_batch_id = None
                    
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
