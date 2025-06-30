# Care Classifier – AWS Lambda Hackathon Submission

Home-care providers must audit hundreds of visit logs each week. Care Classifier automates that audit with two AWS Lambda functions powered by Amazon Bedrock:

1. **classify-visits** – assigns each visit note a risk colour (RED / AMBER / GREEN)
2. **summarise-visits** – produces a succinct client summary from a series of notes

Both Lambdas run in parallel every time a JSON file of visit logs is uploaded.

## How it works

```
S3 upload → SNS topic → Lambda (classify) + Lambda (summarise) → Bedrock → DynamoDB
```

1. A visit-log file lands in an Amazon S3 bucket.
2. S3 sends an event to an Amazon SNS topic.
3. SNS fan-out invokes the **classify-visits** and **summarise-visits** Lambdas.
4. Each Lambda calls the Bedrock Titan model. The classifier returns a colour; the summariser returns a paragraph.
5. Results are written in batch to DynamoDB tables for dashboards or analytics.

Inside each Lambda a `ThreadPoolExecutor` processes many notes concurrently, while Lambda itself scales horizontally per file – no servers to manage.

## AWS services used

| Service         | Purpose                                    |
| --------------- | ------------------------------------------ |
| AWS Lambda      | Classification & summarisation compute     |
| Amazon S3       | Visit-log storage and event source         |
| Amazon SNS      | Messaging between S3 and Lambdas           |
| Amazon Bedrock  | LLM for classification & summarisation     |
| Amazon DynamoDB | Persist classification and summary results |
| AWS IAM         | Secure roles & permissions                 |

## Repo & Demo

• Public repo: <https://github.com/jaycoolslm/aws-lambda-hackathon-care-auditor>  
• Demo video: <https://vimeo.com/1097499489/3cb742a3ca>
