import warnings
# StarletteDeprecationWarning inherits from Warning directly (not FutureWarning,
# not DeprecationWarning). It fires from fastapi/testclient.py when importing
# starlette.testclient. Filter by message text to avoid over-suppression.
warnings.filterwarnings("ignore",
    message="Using `httpx` with `starlette.testclient`")
