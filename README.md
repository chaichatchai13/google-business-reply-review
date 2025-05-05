# Google Business Review Reply Cloud Function

This project contains a Google Cloud Function (`reply_reviews.py`) that fetches unreplied Google Business reviews, generates replies using OpenAI(`gpt-4o-mini`), and posts them back to Google Business. It runs daily at midnight EST via Cloud Scheduler.

This Google Cloud Function is currently running for [Osha Thai Sushi Galleria restaurant](https://g.co/kgs/XbbB1ri) which is located in Allanta, Georgia. If you are in Atlanta, come try our foods!

If you are interested in automating your business review replies, contact me(Chai) at [LinkedLn](https://www.linkedin.com/in/chatchai-satienpattanakul-97aaa668) or chaisatien13@gmail.com

## Prerequisites

- **Python 3.11**: For local development.
- **Google Cloud Project**: `YOUR_GCP_PROJECT_ID`.
- **Service Accounts**:
  - `your-cloud-run-sa@YOUR_GCP_PROJECT_ID.iam.gserviceaccount.com`: For accessing Secret Manager (`roles/secretmanager.secretAccessor`).
  - `gmb-api-sa@YOUR_GCP_PROJECT_ID.iam.gserviceaccount.com`: For Google Business API (`roles/mybusiness`).
  - `scheduler-sa@YOUR_GCP_PROJECT_ID.iam.gserviceaccount.com`: For Cloud Scheduler (`roles/cloudfunctions.invoker`).
- **Secrets in GCP Secret Manager** (Production):
  - `gmb-api-credentials`: Google Business API service account JSON.
  - `openai-api-key`: OpenAI API key.
- **ngrok**: For local HTTPS testing ([Installation Guide](https://ngrok.com/download)).
- **Google Workspace**: Required for domain-wide delegation to impersonate a user for Google Business API access.

## Project Structure

- `functions/reply_reviews.py`: Cloud Function code.
- `functions/requirements.txt`: Python dependencies.
- `.env.example`: An example of environment variables for development.

## Setup

### Development (Local)
1. **Clone Repository**:
   ```bash
   git clone <repository-url>
   cd <repository-dir>/functions
   ```

2. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Create `.env`**:
   - In `functions/.env`:
     ```env
     # Environment mode (development or production)
     ENV=development
     # Path to JSON credentials for Google Business API (gmb-api-sa@YOUR_GCP_PROJECT_ID.iam.gserviceaccount.com)
     GMB_API_CREDENTIALS_FILE=/path/to/gmb-api-sa.json
     # OpenAI API key for generating review replies
     OPENAI_API_KEY=your_openai_key
     # Path to service account key file for accessing GCP services (your-cloud-run-sa@YOUR_GCP_PROJECT_ID.iam.gserviceaccount.com)
     GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account-secret.json
     # Email of Google Workspace user to impersonate for Google Business API access
     IMPERSONATED_USER_EMAIL=admin@myresuarantdomain.com
     ```
   - Replace `/path/to/gmb-api-sa.json` with the path to the `gmb-api-sa` service account key file.
   - Replace `/path/to/service-account-secret.json` with the path to the `your-cloud-run-sa` service account key file.
   - Replace `admin@myresuarantdomain.com` with the email of a Google Workspace user with admin access to the Google Business Profile.

4. **Run Locally**:
   ```bash
   python -m functions_framework --target reply_reviews --source reply_reviews.py --port 8080
   ```

5. **Test with HTTPS (ngrok)**:
   ```bash
   ngrok http 8080
   ```
   - Get the HTTPS URL (e.g., `https://abc123.ngrok.io`).
   - Test with valid `account_id` and `location_id`:
     ```bash
     curl -X GET "https://abc123.ngrok.io?account_id=accounts/1234567890&location_id=locations/0987654321&days=30"
     ```

### Production (GCP)
1. **Authenticate with GCP**:
   ```bash
   gcloud auth login
   gcloud config set project YOUR_GCP_PROJECT_ID
   ```

2. **Ensure Secrets**:
   - Verify `gmb-api-credentials` and `openai-api-key` in Secret Manager:
     ```bash
     gcloud secrets versions access latest --secret=gmb-api-credentials --project=YOUR_GCP_PROJECT_ID
     gcloud secrets versions access latest --secret=openai-api-key --project=YOUR_GCP_PROJECT_ID
     ```

3. **Deploy Cloud Function**:
   ```bash
   cd /Users/chatchaisatienpattanakul/PycharmProjects/review-summarization-2/functions
   gcloud functions deploy reply-reviews \
     --runtime python311 \
     --trigger-http \
     --service-account your-cloud-run-sa@YOUR_GCP_PROJECT_ID.iam.gserviceaccount.com \
     --region us-central1 \
     --project YOUR_GCP_PROJECT_ID \
     --set-env-vars "ENV=production,GCP_SECRET_NAME=projects/YOUR_GCP_PROJECT_ID/secrets/gmb-api-credentials/versions/latest,OPENAI_SECRET_NAME=projects/YOUR_GCP_PROJECT_ID/secrets/openai-api-key/versions/latest,IMPERSONATED_USER_EMAIL=admin@myresuarantdomain.com,PORT=8080" \
     --no-allow-unauthenticated \
     --source . \
     --entry-point reply_reviews \
     --timeout 540
   ```
   - Replace `admin@myresuarantdomain.com` with the Google Workspace user email.

4. **Grant Invoker Permission**:
   ```bash
   gcloud functions add-iam-policy-binding reply-reviews \
     --member=serviceAccount:scheduler-sa@YOUR_GCP_PROJECT_ID.iam.gserviceaccount.com \
     --role=roles/cloudfunctions.invoker \
     --region=us-central1 \
     --project=YOUR_GCP_PROJECT_ID
   ```

5. **Update Cloud Scheduler Job**:
   ```bash
   gcloud scheduler jobs update http reply-reviews-job \
     --schedule="0 5 * * *" \
     --uri="https://us-central1-YOUR_GCP_PROJECT_ID.cloudfunctions.net/reply-reviews?account_id=your-account-id&location_id=your-location-id&days=30" \
     --http-method=GET \
     --oidc-service-account-email=scheduler-sa@YOUR_GCP_PROJECT_ID.iam.gserviceaccount.com \
     --oidc-token-audience=https://us-central1-YOUR_GCP_PROJECT_ID.cloudfunctions.net/reply-reviews \
     --project=YOUR_GCP_PROJECT_ID \
     --time-zone="America/New_York" \
     --location=us-central1
   ```
   - Replace `your-account-id` and `your-location-id` with valid Google Business Profile IDs.

## Testing
- **Local**:
  - Verify HTTPS enforcement and review processing:
    ```bash
    curl -X GET "https://abc123.ngrok.io?account_id=your-account-id&location_id=locations/your-location-id&days=1"
    ```
  - Check logs for `Successfully fetched secret`, `Fetching reviews`.

- **Production**:
  - Test manually:
    ```bash
    gcloud auth print-identity-token --audiences=https://us-central1-YOUR_GCP_PROJECT_ID.cloudfunctions.net/reply-reviews \
      --impersonate-service-account=scheduler-sa@YOUR_GCP_PROJECT_ID.iam.gserviceaccount.com
    curl -X GET "https://us-central1-YOUR_GCP_PROJECT_ID.cloudfunctions.net/reply-reviews?account_id=your-account-id&location_id=your-location-id&days=1" \
      -H "Authorization: Bearer your_oidc_token"
    ```
  - Check logs:
    ```bash
    gcloud functions logs read reply-reviews --region=us-central1 --project=YOUR_GCP_PROJECT_ID
    ```

## Troubleshooting
- **Local**:
  - Ensure `.env` has valid `GMB_API_CREDENTIALS_FILE`, `OPENAI_API_KEY`, and `IMPERSONATED_USER_EMAIL`.
  - Verify `GOOGLE_APPLICATION_CREDENTIALS` and `GMB_API_CREDENTIALS_FILE` point to valid JSON files.
  - Use `ngrok` for HTTPS testing.
- **Production**:
  - Verify service account permissions:
    ```bash
    gcloud secrets get-iam-policy gmb-api-credentials --project=YOUR_GCP_PROJECT_ID
    gcloud secrets get-iam-policy openai-api-key --project=YOUR_GCP_PROJECT_ID
    ```
  - Check logs for errors:
    ```bash
    gcloud functions logs read reply-reviews --region=us-central1 --project=YOUR_GCP_PROJECT_ID
    ```

## Notes
- Runs daily at midnight EST (`0 0 * * *` UTC, `America/New_York` timezone).
- HTTPS enforced in development and production.
- Development: Credentials read from `.env` files (`GMB_API_CREDENTIALS_FILE`, `GOOGLE_APPLICATION_CREDENTIALS`, `IMPERSONATED_USER_EMAIL`).
- Production: Credentials fetched from GCP Secret Manager, with `IMPERSONATED_USER_EMAIL` set for Google Business API access.
