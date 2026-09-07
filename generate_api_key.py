"""Generate a permanent VAJRA API key.

Run once, copy the output into `.env` as VAJRA_API_KEY=<value>.
This is the key the dashboard/client must send as the X-API-Key header.

    python generate_api_key.py
"""

import secrets

print(secrets.token_urlsafe(32))