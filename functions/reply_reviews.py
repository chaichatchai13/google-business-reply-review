from datetime import datetime, timedelta
import functions_framework
import os
import json
import logging
import asyncio
import aiohttp
import certifi
import ssl
from google.cloud import secretmanager
from google.oauth2 import service_account
from google.auth.transport.requests import Request
from openai import AsyncOpenAI
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("reply_reviews")

# Environment variables
ENV = os.getenv("ENV", "development")
BASE_DIR = Path(__file__).resolve().parent
GCP_SECRET_NAME = os.getenv("GCP_SECRET_NAME",
                            "projects/genial-acronym-429901-j0/secrets/gmb-api-credentials/versions/latest")
OPENAI_SECRET_NAME = os.getenv("OPENAI_SECRET_NAME",
                               "projects/genial-acronym-429901-j0/secrets/openai-api-key/versions/latest")
GMB_API_CREDENTIALS_FILE = os.getenv("GMB_API_CREDENTIALS_FILE")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
IMPERSONATED_USER_EMAIL = os.getenv("IMPERSONATED_USER_EMAIL")
SCOPES = ["https://www.googleapis.com/auth/business.manage"]
BASE_URL_LOCATIONS_V4 = "https://mybusiness.googleapis.com/v4"
PORT = int(os.getenv("PORT", 8080))


def get_secret(secret_name):
    """Fetch a secret from GCP Secret Manager."""
    logger.debug(f"Attempting to fetch secret: {secret_name}")
    if ENV == "development":
        secret_account_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", str(BASE_DIR / "service-account-secret.json"))
        if not os.path.exists(secret_account_path):
            logger.error(f"Service account file not found at {secret_account_path}")
            raise FileNotFoundError(f"Service account file not found at {secret_account_path}")
        if not os.access(secret_account_path, os.R_OK):
            logger.error(f"Service account file at {secret_account_path} is not readable")
            raise PermissionError(f"Service account file at {secret_account_path} is not readable")
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = secret_account_path
    try:
        client = secretmanager.SecretManagerServiceClient()
        response = client.access_secret_version(name=secret_name)
        secret_value = response.payload.data.decode("UTF-8")
        logger.info(f"Successfully fetched secret: {secret_name}")
        return secret_value
    except Exception as e:
        logger.error(f"Failed to load secret {secret_name}: {str(e)}")
        raise RuntimeError(f"Failed to load secret {secret_name}: {str(e)}")


# Fetch credentials and OpenAI API key
if ENV == "development":
    if not GMB_API_CREDENTIALS_FILE or not OPENAI_API_KEY or not IMPERSONATED_USER_EMAIL:
        logger.error(
            "Missing GMB_API_CREDENTIALS_FILE, OPENAI_API_KEY, or IMPERSONATED_USER_EMAIL in .env for development mode")
        raise ValueError(
            "GMB_API_CREDENTIALS_FILE, OPENAI_API_KEY, and IMPERSONATED_USER_EMAIL must be set in .env for development")
    if not os.path.exists(GMB_API_CREDENTIALS_FILE):
        logger.error(f"GMB API credentials file not found at {GMB_API_CREDENTIALS_FILE}")
        raise FileNotFoundError(f"GMB API credentials file not found at {GMB_API_CREDENTIALS_FILE}")
    if not os.access(GMB_API_CREDENTIALS_FILE, os.R_OK):
        logger.error(f"GMB API credentials file at {GMB_API_CREDENTIALS_FILE} is not readable")
        raise PermissionError(f"GMB API credentials file at {GMB_API_CREDENTIALS_FILE} is not readable")
    with open(GMB_API_CREDENTIALS_FILE, 'r') as f:
        credentials_json = json.load(f)
    openai_api_key = OPENAI_API_KEY
else:
    credentials_json = json.loads(get_secret(GCP_SECRET_NAME))
    openai_api_key = get_secret(OPENAI_SECRET_NAME)

credentials = service_account.Credentials.from_service_account_info(
    credentials_json, scopes=SCOPES, subject=IMPERSONATED_USER_EMAIL
)

# Initialize OpenAI client
openai_client = AsyncOpenAI(api_key=openai_api_key)


async def get_access_token():
    """Retrieve a valid access token."""
    logger.debug("Attempting to get access token")
    try:
        if credentials.expired or not credentials.valid:
            credentials.refresh(Request())
        logger.debug("Access token retrieved successfully")
        return credentials.token
    except Exception as e:
        logger.error(f"Failed to get access token: {str(e)}")
        raise RuntimeError(f"Failed to get access token: {str(e)}")


async def fetch_gmb_reviews(account_id: str, location_id: str, days: int = 1) -> list:
    """Fetch Google My Business reviews."""
    logger.info(f"Fetching reviews for account_id={account_id}, location_id={location_id}, days={days}")
    if not account_id or not location_id:
        logger.error("Missing account_id or location_id")
        raise ValueError("account_id and location_id must be provided")
    if not account_id.startswith("accounts/"):
        account_id = f"accounts/{account_id}"
    if not location_id.startswith("locations/"):
        location_id = f"locations/{location_id}"
    reviews = []
    next_page_token = None
    try:
        days_int = int(days)
    except (ValueError, TypeError):
        logger.error(f"Invalid days parameter: {days}")
        raise ValueError(f"Days parameter must be an integer, got {days}")
    cutoff_date = datetime.utcnow() - timedelta(days=days_int)
    logger.debug(f"Cutoff date set to {cutoff_date}")

    while True:
        url = f"{BASE_URL_LOCATIONS_V4}/{account_id}/{location_id}/reviews"
        params = {"pageToken": next_page_token} if next_page_token else {}
        try:
            token = await get_access_token()
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers={"Authorization": f"Bearer {token}"}, params=params,
                                       ssl=ssl_context, timeout=30) as response:
                    if response.status == 404:
                        error_data = await response.text()
                        logger.error(f"404 Not Found fetching reviews for URL {url}: {error_data}")
                        raise aiohttp.ClientResponseError(
                            response.request_info,
                            response.history,
                            status=response.status,
                            message=f"Not Found: {error_data}"
                        )
                    response.raise_for_status()
                    data = await response.json()
                    batch_reviews = data.get("reviews", [])
                    filtered_reviews = [
                        review for review in batch_reviews
                        if datetime.strptime(review["createTime"], "%Y-%m-%dT%H:%M:%S.%fZ") >= cutoff_date
                    ]
                    reviews.extend(filtered_reviews)
                    logger.debug(f"Fetched {len(filtered_reviews)} reviews in batch")
                    next_page_token = data.get("nextPageToken")
                    if not next_page_token or not filtered_reviews:
                        break
                    await asyncio.sleep(2)
        except aiohttp.ClientResponseError as e:
            logger.error(f"HTTP error fetching reviews: {e.status} - {e.message}")
            raise RuntimeError(f"Failed to fetch reviews: {e.status} - {e.message}")
        except Exception as e:
            logger.error(f"Unexpected error fetching reviews: {str(e)}")
            raise RuntimeError(f"Unexpected error fetching reviews: {str(e)}")
    logger.info(f"Total reviews fetched: {len(reviews)}")
    return reviews


async def generate_review_replies_batch(reviews: list, batch_size: int = 10) -> list:
    """Generate replies for a batch of reviews using OpenAI."""
    all_replies = []
    for i in range(0, len(reviews), batch_size):
        batch = reviews[i:i + batch_size]
        prompt = """
        You are a friendly, professional restaurant manager at Osha Thai Sushi Galleria, responding to Google My Business reviews with warmth and authenticity, like a real person would. For each review, generate a unique reply that:

        - Addresses the reviewer by their first name only if it is a clear, appropriate personal name (e.g., "Hi Yung" for "Yung Exotic", "Hi Erica" for "Erica Harry"). If the name is unclear, non-standard, or potentially inappropriate (e.g., "VirtuousMo", "Nyc Sells", "Asshole"), use "Hi Guest" instead. Automatically address upper camel case on customer name too (e.g., "Hi delase" should be "Hi Delase" instead).
        - Acknowledges specific feedback (e.g., dishes, staff, ambiance) to show care and attention.
        - For positive reviews, expresses genuine gratitude with varied phrasing (avoid overused words like "thrilled" or "delighted").
        - For negative reviews, offers a sincere apology and invites the reviewer to return, promising a better experience to console them from their disappointment. Use generic, warm language to reassure them without promising specific remedies (e.g., avoid mentioning free food, drinks, or refunds). Optionally, suggest calling for urgent issues needing immediate resolution (e.g., "Call us at 555-123-4567 if this needs urgent attention").
        - Uses diverse sentence structures, casual yet professional tone, and different vocabulary to sound human, not robotic.
        - Keeps replies concise (50-100 words) and tailored to the review’s details.
        - Returns plain text, no markdown or formatting.

        ### Instructions:
        - Extract the first name or a natural-sounding name from the reviewer’s display name (e.g., "Yung" from "Yung Exotic", "Erica" from "Erica Harry"). For unclear, non-standard, or potentially inappropriate names (e.g., "VirtuousMo", "Nyc Sells", "Asshole"), use "Hi Guest".
        - Vary expressions (e.g., "We’re so glad" vs. "It’s wonderful to hear").
        - Avoid repetition across replies (e.g., don’t reuse "kind words" or "look forward").
        - Reflect the review’s tone (e.g., upbeat for positive, empathetic for negative).
        - For negative reviews, do not mention refunds, free items, or procedural steps like showing the reply to a manager. Focus on emotional reassurance and a better future visit.
        - Sign replies as "Osha Thai Sushi Galleria Team".
        - Output a JSON array of objects with keys: review_id, reply_text. Even there is only one object, return output as a JSON array of an object.

        ### Example Reviews and Replies:
        1. Review: "Sushi was amazing, Kim was great!" (5 stars, Reviewer: John Smith)
           Reply: {"review_id": "review_1", "reply_text": "Hi John,\nWow, we’re so glad you enjoyed the sushi and Kim’s service! She’s a gem, and we’ll pass on your praise. Come back soon for more delicious bites!\nBest,\nOsha Thai Sushi Galleria Team"}
        2. Review: "Food was cold, service slow" (2 stars, Reviewer: VirtuousMo)
           Reply: {"review_id": "review_2", "reply_text": "Hi Guest,\nWe’re truly sorry your visit fell short with cold food and slow service. We’d love for you to come back—we’re committed to making your next experience so much better. Call us at 555-123-4567 for urgent concerns.\nWarmly,\nOsha Thai Sushi Galleria Team"}
        3. Review: "Melisa was awesome, crab fried rice was bomb!" (5 stars, Reviewer: Nyc Sells)
           Reply: {"review_id": "review_3", "reply_text": "Hi Guest,\nThanks for the love! We’re glad Melisa and the crab fried rice made your day. She’ll love hearing this. Swing by soon for another tasty meal!\nCheers,\nOsha Thai Sushi Galleria Team"}
        4. Review: "Terrible experience, rude staff" (1 star, Reviewer: Asshole)
           Reply: {"review_id": "review_4", "reply_text": "Hi Guest,\nWe’re really sorry to hear about your experience with our staff. That’s not the vibe we want at Osha Thai. Please give us another chance to show you a warm welcome. Reach out at 555-123-4567 if you’d like to discuss this.\nSincerely,\nOsha Thai Sushi Galleria Team"}

        ### Reviews to Process:
        """
        for j, review in enumerate(batch, 1):
            review_id = review.get('reviewId', f'review_{i + j}')
            reviewer_name = review.get("reviewer", {}).get("displayName", "Guest")
            star_rating = review.get("starRating", "UNKNOWN")
            review_text = review.get("comment", "")
            prompt += f"{j}. Review ID: {review_id}, Reviewer Name: {reviewer_name}, Star Rating: {star_rating}, Text: {review_text}\n"

        try:
            logger.info(f"Sending batch {i // batch_size + 1} of {len(batch)} reviews for reply generation")
            response = await openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_completion_tokens=2500
            )
            raw_content = response.choices[0].message.content.strip()
            logger.debug(f"Raw OpenAI response: {raw_content[:500]}...")
            # Remove markdown or extra formatting
            if raw_content.startswith("```json"):
                raw_content = raw_content[7:].strip()
            if raw_content.endswith("```"):
                raw_content = raw_content[:-3].strip()
            # Ensure valid JSON
            batch_replies = json.loads(raw_content)
            # Validate reply structure
            for reply in batch_replies:
                if not isinstance(reply, dict) or "review_id" not in reply or "reply_text" not in reply:
                    logger.error(f"Invalid reply format: {reply}")
                    raise ValueError(f"Invalid reply format: {reply}")
            all_replies.extend(batch_replies)
            logger.info(f"Generated {len(batch_replies)} replies for batch {i // batch_size + 1}")
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse OpenAI response as JSON for batch {i // batch_size + 1}: {str(e)}")
            continue
        except Exception as e:
            logger.error(f"Failed to generate replies for batch {i // batch_size + 1}: {str(e)}")
            continue
    logger.info(f"Total replies generated: {len(all_replies)}")
    return all_replies


async def post_review_reply(account_id: str, location_id: str, review_id: str, reply_text: str):
    """Post a reply to a Google My Business review."""
    logger.debug(f"Attempting to post reply to review_id={review_id}")
    if not account_id.startswith("accounts/"):
        account_id = f"accounts/{account_id}"
    if not location_id.startswith("locations/"):
        location_id = f"locations/{location_id}"
    url = f"{BASE_URL_LOCATIONS_V4}/{account_id}/{location_id}/reviews/{review_id}/reply"
    try:
        token = await get_access_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        payload = {"comment": reply_text}
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        async with aiohttp.ClientSession() as session:
            async with session.put(url, headers=headers, json=payload, ssl=ssl_context, timeout=30) as response:
                response.raise_for_status()
                logger.info(f"Successfully posted reply to review_id={review_id}")
    except Exception as e:
        logger.error(f"Failed to post reply to review_id={review_id}: {str(e)}")
        raise RuntimeError(f"Failed to post reply: {str(e)}")


async def reply_to_unreplied_reviews(reviews: list, account_id: str, location_id: str):
    """Generate and post replies for unreplied reviews."""
    unreplied_reviews = [r for r in reviews if not r.get("reviewReply") and r.get("comment", "").strip()]
    if not unreplied_reviews:
        logger.info(f"No unreplied reviews found for account_id={account_id}, location_id={location_id}")
        return
    logger.info(
        f"Found {len(unreplied_reviews)} unreplied reviews for account_id={account_id}, location_id={location_id}")
    try:
        replies = await generate_review_replies_batch(unreplied_reviews, batch_size=10)
        logger.debug(f"Generated {len(replies)} replies for processing")
        for review, reply in zip(unreplied_reviews, replies):
            review_id = review["reviewId"]
            reply_text = reply["reply_text"]
            await post_review_reply(account_id, location_id, review_id, reply_text)
        logger.info(f"Successfully processed {len(replies)} replies")
    except Exception as e:
        logger.error(f"Failed to process replies for account_id={account_id}, location_id={location_id}: {str(e)}")
        raise


@functions_framework.http
def reply_reviews(request):
    """Cloud Function to process unreplied reviews."""
    # Enforce HTTPS in development
    if ENV == "development":
        proto = request.headers.get("X-Forwarded-Proto", "http")
        if proto != "https":
            logger.warning(f"Non-HTTPS request detected: proto={proto}")
            return {
                "error": "HTTPS required",
                "redirect": f"https://{request.host}{request.path}{request.query_string.decode()}"
            }, 301

    # Add CORS headers
    headers = {
        "Access-Control-Allow-Origin": "http://localhost:3000,https://your-app-domain.com",
        "Access-Control-Allow-Methods": "GET,OPTIONS",
        "Access-Control-Allow-Headers": "Authorization,Content-Type",
        "Access-Control-Allow-Credentials": "true"
    }

    # Handle CORS preflight (OPTIONS) request
    if request.method == "OPTIONS":
        return {}, 204, headers

    try:
        account_id = request.args.get("account_id")
        location_id = request.args.get("location_id")
        days = request.args.get("days", "1")  # Default to 1 day
        if not account_id or not location_id:
            logger.error("Missing account_id or location_id")
            return {"error": "account_id and location_id are required"}, 400, headers
        try:
            days_int = int(days)
        except (ValueError, TypeError):
            logger.error(f"Invalid days parameter: {days}")
            return {"error": "Days must be an integer"}, 400, headers
        logger.info(
            f"Starting review processing for account_id={account_id}, location_id={location_id}, days={days_int}")
        reviews = asyncio.run(fetch_gmb_reviews(account_id, location_id, days_int))
        asyncio.run(reply_to_unreplied_reviews(reviews, account_id, location_id))
        return {
            "status": f"Processed {len([r for r in reviews if not r.get('reviewReply') and r.get('comment', '').strip()])} unreplied reviews"}, 200, headers
    except Exception as e:
        logger.error(f"Error processing reviews: {str(e)}")
        return {"error": str(e)}, 500, headers
