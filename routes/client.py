from fastapi import APIRouter, Depends, Request

from core.auth import require_client_id
from core.client_keys import client_key_store
from core.rate_limiter import limiter

router = APIRouter(prefix="/api/v1/client", tags=["client"])


@router.post("/key", summary="Generate a personal API key for this client")
@limiter.limit("5/minute;20/hour")
async def generate_client_key(request: Request, client_id: str = Depends(require_client_id)):
    """
    Creates a persistent API key bound to the caller's client-id.
    The key can replace X-Client-ID on all subsequent requests.
    Store it securely — it grants full access to this client's jobs.

    Security note: X-Client-ID is a bearer token — anyone who holds it
    can already access the client's jobs. Minting a key from a client_id
    does not escalate privileges beyond what the bearer already has. It
    does however create a second, DB-backed credential. A proper fix
    requires a registration system (out of scope for this demo API).
    """
    api_key = client_key_store.create_key(client_id)
    return {"api_key": api_key, "client_id": client_id}
