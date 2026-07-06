# Deployment Guide

This document outlines how to deploy the CatchFees-Agents API to Google Cloud Run.

## Prerequisites

- [Google Cloud SDK (gcloud)](https://cloud.google.com/sdk/docs/install) installed and configured.
- A Google Cloud Project with Billing enabled.
- Docker installed locally (if building manually, though we use Google Cloud Build).

## Environment Variables

| Variable | Description |
|---|---|
| `GOOGLE_API_KEY` | Your Google Gemini API Key for running the agents. |
| `GOOGLE_CLOUD_PROJECT` | (Optional) Used by Vertex AI if configured instead of an API key. |
| `GOOGLE_CLOUD_LOCATION` | (Optional) e.g., `us-central1`. |

## Deployment Steps

1. **Authenticate with Google Cloud:**
   ```bash
   gcloud auth login
   gcloud config set project YOUR_PROJECT_ID
   ```

2. **Deploy to Cloud Run:**
   Deploy the application using the following command. Note the specific flags required for Server-Sent Events (SSE) and agent execution:

   ```bash
   gcloud run deploy catchfees-api \
     --source . \
     --region us-central1 \
     --allow-unauthenticated \
     --set-env-vars="GOOGLE_API_KEY=your_actual_api_key_here" \
     --timeout=300 \
     --min-instances=0 \
     --max-instances=10 \
     --use-http2=false
   ```

   **Important Flags for SSE:**
   - `--timeout=300`: Agent execution can take several minutes due to complex negotiations. We set this to at least 300 seconds (5 minutes).
   - `--use-http2=false`: Server-Sent Events (SSE) work most reliably over HTTP/1.1 on Cloud Run.

## Local Testing

To test the Docker image locally before deploying:

1. **Build the image:**
   ```bash
   docker build -t catchfees-api .
   ```

2. **Run the container:**
   ```bash
   docker run -p 8080:8080 -e GOOGLE_API_KEY="your_actual_api_key_here" catchfees-api
   ```

**Note for local development (without Docker):**
When running the server locally via `uvicorn`, it automatically loads environment variables from `.env` files located in either the repository root (`<repo_root>/.env`) or `src/catchfees/.env`. You do not need to export the `GOOGLE_API_KEY` manually if it is placed in one of those files.
