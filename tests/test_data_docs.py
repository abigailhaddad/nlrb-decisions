"""Offline tests for the data dictionary, the SQLite build and the Zenodo upload logic.

The Zenodo tests run against a fake that answers the way https://developers.zenodo.org/ documents the
deposition API: newversion returns the original record with links.latest_draft, the new draft starts with a
copy of the previous version's files, and calling newversion while a draft exists returns that same draft.
"""
import hashlib, os, sqlite3, sys
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import build_sqlite, data_docs, zenodo_upload as Z

CFG = {
    "dataset": "someone/example",
    "tables": {
        "parent": {"file": "parent.parquet", "config": "default", "grain": "one parent", "primary_key": "id",
                   "columns": {"id": "row id", "n": "a number", "flag": "a flag"}},
        "child": {"file": "child.parquet", "config": "child", "grain": "one child", "primary_key": "cid",
                  "unconfirmed": ["note"], "columns": {"cid": "row id", "id": "parent id | pipe", "note": "a note"}},
    },
    "joins": [{"left": "parent.id", "right": "child.id", "kind": "||--o{", "label": "id", "note": "exact"}],
}


@pytest.fixture
def data(tmp_path):
    pd.DataFrame({"id": ["a", "b", "c"], "n": [1, None, 3], "flag": [True, False, True]}).astype({"n": "Int64"}) \
        .to_parquet(tmp_path / "parent.parquet", index=False)
    pd.DataFrame({"cid": ["x", "y"], "id": ["a", None], "note": ["", "hi"]}).to_parquet(tmp_path / "child.parquet", index=False)
    return tmp_path


def test_check_catches_drift(data):
    data_docs.check(str(data), CFG)
    bad = {**CFG, "tables": {**CFG["tables"], "parent": {**CFG["tables"]["parent"], "columns": {"id": "x", "n": "y"}}}}
    with pytest.raises(SystemExit, match="no description"):
        data_docs.check(str(data), bad)


def test_markdown(data):
    md = data_docs.markdown(str(data), CFG)
    assert "| `parent` | `parent.parquet` | `default` | 3 |" in md
    assert "parent ||--o{ child" in md and "string id FK" in md
    assert "| `n` | int64 | a number | 66.7% | 66.7% | `1` |" in md
    assert "| `note` | string | a note **(meaning not confirmed)** | 100.0% | 50.0% | `hi` |" in md
    assert "parent id \\| pipe" in md


def test_sqlite_matches_parquet(data, tmp_path):
    out = tmp_path / "x.sqlite"
    assert build_sqlite.build(str(data), str(out), CFG) == {"parent": 3, "child": 2}
    con = sqlite3.connect(out)
    assert con.execute("select id, n, flag from parent order by id").fetchall() == [("a", 1, 1), ("b", None, 0), ("c", 3, 1)]
    assert con.execute("select count(*) from child join parent using (id)").fetchone() == (1,)


class Resp:
    def __init__(self, code, body=None):
        self.status_code, self._body = code, body
        self.content = b"x" if body is not None else b""
        self.text = str(body)

    def json(self):
        return self._body


class FakeZenodo:
    """Just enough of the deposition API, keyed by URL."""
    API = "https://sandbox.zenodo.org/api"

    def __init__(self, records=()):
        self.deps = {d["id"]: d for d in records}
        self.calls = []
        self.next_id = 100

    def _new(self, files, concept, title=None):
        self.next_id += 1
        i = self.next_id
        d = {"id": i, "submitted": False, "conceptrecid": concept, "metadata": {"title": title} if title else {},
             "files": [dict(f, id=f"{i}-{f['filename']}") for f in files],
             "links": {"bucket": f"https://bucket/{i}", "html": f"https://sandbox.zenodo.org/deposit/{i}",
                       "latest_draft": f"{self.API}/deposit/depositions/{i}"}}
        self.deps[i] = d
        return d

    def get(self, url, params=None):
        self.calls.append(("GET", url))
        if url == f"{self.API}/deposit/depositions":
            return Resp(200, list(self.deps.values()))
        i = int(url.split("/depositions/")[1].split("/")[0])
        return Resp(200, self.deps[i]["files"] if url.endswith("/files") else self.deps[i])

    def post(self, url, json=None):
        self.calls.append(("POST", url))
        if url == f"{self.API}/deposit/depositions":
            return Resp(201, self._new([], concept=None))
        i = int(url.split("/depositions/")[1].split("/")[0])
        d = self.deps[i]
        if url.endswith("/newversion"):
            draft = next((x for x in self.deps.values() if x["conceptrecid"] == d["conceptrecid"] and not x["submitted"]), None)
            draft = draft or self._new(d["files"], d["conceptrecid"], d["metadata"]["title"])
            return Resp(201, dict(d, links=dict(d["links"], latest_draft=draft["links"]["latest_draft"])))
        if url.endswith("/publish"):
            d["submitted"], d["doi"], d["conceptdoi"] = True, f"10.5072/zenodo.{i}", "10.5072/zenodo.1"
            d["conceptrecid"] = d["conceptrecid"] or "1"
            return Resp(202, d)

    def put(self, url, data=None, json=None):
        self.calls.append(("PUT", url))
        if url.startswith("https://bucket/"):
            i, name = url.split("/")[-2:]
            body = data.read()
            self.deps[int(i)]["files"].append({"id": f"{i}-{name}", "filename": name, "checksum": "md5:" + hashlib.md5(body).hexdigest()})
            return Resp(201, {})
        i = int(url.split("/depositions/")[1])
        self.deps[i]["metadata"] = json["metadata"]
        return Resp(200, self.deps[i])

    def delete(self, url):
        self.calls.append(("DELETE", url))
        i, fid = url.split("/depositions/")[1].split("/files/")
        self.deps[int(i)]["files"] = [f for f in self.deps[int(i)]["files"] if f["id"] != fid]
        return Resp(204)


META = {"title": "T", "upload_type": "dataset", "creators": [{"name": "Haddad, Abigail"}], "description": "d",
        "license": "cc0-1.0", "access_right": "open"}


@pytest.fixture
def files(tmp_path):
    p = tmp_path / "a.parquet"
    p.write_bytes(b"version one")
    return [str(p)]


def test_zenodo_first_then_new_version_then_unchanged(files):
    z = FakeZenodo()
    first = Z.archive(z, z.API, META, files, "v1")
    assert first["submitted"] and first["metadata"]["version"] == "v1" and first["metadata"]["license"] == "cc0-1.0"
    assert [f["filename"] for f in first["files"]] == ["a.parquet"]

    open(files[0], "wb").write(b"version two")
    second = Z.archive(z, z.API, META, files, "v2")
    assert second["id"] != first["id"] and second["submitted"] and second["conceptrecid"] == first["conceptrecid"]
    assert ("POST", f"{z.API}/deposit/depositions/{first['id']}/actions/newversion") in z.calls
    # the copied file was deleted and replaced by today's
    assert [f["checksum"] for f in second["files"]] == ["md5:" + hashlib.md5(b"version two").hexdigest()]
    assert first["files"][0]["checksum"] == "md5:" + hashlib.md5(b"version one").hexdigest()

    n = len(z.calls)
    third = Z.archive(z, z.API, META, files, "v3")
    assert third["id"] == second["id"] and not any(c[0] in ("POST", "PUT", "DELETE") for c in z.calls[n:])


def test_zenodo_draft_only_then_publish(files):
    z = FakeZenodo()
    draft = Z.archive(z, z.API, META, files, "v1", publish=False)
    assert not draft["submitted"]
    again = Z.archive(z, z.API, META, files, "v1")  # finds the draft by title and publishes it
    assert again["id"] == draft["id"] and again["submitted"] and len(again["files"]) == 1


def test_zenodo_skips_without_token(monkeypatch, capsys):
    monkeypatch.delenv("ZENODO_TOKEN", raising=False)
    monkeypatch.setattr(Z.data_docs, "fetch", lambda *a, **k: pytest.fail("should not fetch"))
    Z.main()
    assert "ZENODO_TOKEN secret is not set" in capsys.readouterr().out
