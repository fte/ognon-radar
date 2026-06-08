import re

ONION_URL_REGEX = re.compile(r'^https?://[a-z2-7]{56}\.onion', re.IGNORECASE)

BLACKLIST_PATHS = {
    '/register', '/signup', '/login', '/logout',
    '/register.php', '/login.php', '/signup.php',
}
