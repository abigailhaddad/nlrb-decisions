"""Retry a Hugging Face read or upload on a transient failure: HTTP 429 or 5xx, a dropped connection, a timeout.

huggingface_hub retries its own pre-upload and LFS requests, but not the final commit POST, and
pd.read_parquet on a resolve/ URL is not retried at all. Anything else (404, 401, a bad file) is raised at once.
A retried upload is safe: if the first try landed, Hugging Face skips the second as an empty commit.
"""
import time
import urllib.error

TRANSIENT = (429, 500, 502, 503, 504)


def transient(e):
    code = getattr(getattr(e, "response", None), "status_code", None) or getattr(e, "code", None)
    if isinstance(code, int):
        return code in TRANSIENT
    names = {c.__name__ for c in type(e).__mro__}
    return isinstance(e, (ConnectionError, TimeoutError, urllib.error.URLError)) or bool(
        names & {"TransportError", "TimeoutException", "ConnectionError", "Timeout"})


def hf_retry(fn, *args, tries=4, wait=5, **kwargs):
    for i in range(tries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            if i == tries - 1 or not transient(e):
                raise
            print(f"Hugging Face call failed ({type(e).__name__}: {str(e)[:150]}); retry {i + 1} in {wait * 2 ** i}s",
                  flush=True)
            time.sleep(wait * 2 ** i)
