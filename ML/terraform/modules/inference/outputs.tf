output "endpoint_name" {
  description = "SageMaker endpoint name -> backend ALIBI_SAGEMAKER_ENDPOINT_NAME."
  value       = aws_sagemaker_endpoint.this.name
}

output "endpoint_arn" {
  value = aws_sagemaker_endpoint.this.arn
}

output "inference_component_name" {
  description = "The IC every invocation must name -> backend ALIBI_SAGEMAKER_INFERENCE_COMPONENT. aws sagemaker-runtime invoke-endpoint --endpoint-name <endpoint> --inference-component-name <this>"
  value       = awscc_sagemaker_inference_component.this.inference_component_name
}
