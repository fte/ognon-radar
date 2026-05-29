import re

ONION_URL_REGEX = re.compile(r'^https?://[a-z2-7]{56}\.onion', re.IGNORECASE)
