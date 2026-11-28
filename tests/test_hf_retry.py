"""hf_retry: retries 429/5xx and connection errors, raises everything else at once. No network."""
import os, sys, urllib.error
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import hf_retry as H


class Resp:
    def __init__(self, code):
        self.status_code = code


class HubError(Exception):
    def __init__(self, code):
        super().__init__(f"HTTP {code}")
        self.response = Resp(code)


def flaky(errors):
    calls = []

    def fn():
        calls.append(1)
        if errors:
            raise errors.pop(0)
        return "ok"
    return fn, calls


@pytest.mark.parametrize("err", [HubError(429), HubError(503), ConnectionError("reset"), TimeoutError(),
                                 urllib.error.HTTPError("u", 502, "bad gateway", None, None)])
def test_transient_errors_are_retried(err):
    fn, calls = flaky([err, err])
    assert H.hf_retry(fn, wait=0) == "ok" and len(calls) == 3


@pytest.mark.parametrize("err", [HubError(404), HubError(401), urllib.error.HTTPError("u", 404, "nf", None, None),
                                 FileNotFoundError("out/x.parquet"), ValueError("bad")])
def test_other_errors_raise_at_once(err):
    fn, calls = flaky([err])
    with pytest.raises(type(err)):
        H.hf_retry(fn, wait=0)
    assert len(calls) == 1


def test_gives_up_after_the_last_try():
    fn, calls = flaky([HubError(500)] * 4)
    with pytest.raises(HubError):
        H.hf_retry(fn, wait=0)
    assert len(calls) == 4
