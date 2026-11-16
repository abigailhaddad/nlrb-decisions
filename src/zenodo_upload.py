"""Archive a snapshot of the Hugging Face dataset on Zenodo as a new version under one concept DOI.

    python src/zenodo_upload.py

Environment:
    ZENODO_TOKEN    personal access token with scopes deposit:write and deposit:actions. If empty, the script
                    prints a notice and exits 0 without doing anything.
    ZENODO_API      https://zenodo.org/api (default) or https://sandbox.zenodo.org/api for testing. A sandbox
                    token only works on the sandbox and a production token only on production.
    ZENODO_PUBLISH  "false" to stop after uploading the files, leaving an unpublished draft to look at on the
                    website. Default "true".

Steps (https://developers.zenodo.org/):
 1. Download the dataset's parquet files and README.md from Hugging Face at its current commit, into data/.
 2. Find our record among the token owner's depositions by the title in .zenodo.json.
    - None yet: create a new deposition (the first version; Zenodo assigns the concept DOI).
    - Found, and its files are byte-for-byte the same (md5) as today's: stop, nothing to archive.
    - Found and published: POST .../actions/newversion, then open links.latest_draft. If an unpublished
      new-version draft already exists (an earlier run stopped part way), Zenodo returns that same draft.
 3. Set the metadata from .zenodo.json, with today's date as publication_date and version
    "<date> (Hugging Face <commit>)".
 4. Delete the files the draft copied from the previous version and upload today's to the draft's bucket.
 5. POST .../actions/publish. A published version can't be deleted.
"""
import hashlib, json, os, sys
from datetime import date
import requests

sys.path.insert(0, os.path.dirname(__file__))
import data_docs

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DATA = os.path.join(ROOT, "data")


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check(r, what):
    if r.status_code >= 400:
        sys.exit(f"STOP: Zenodo {what} returned {r.status_code}: {r.text[:500]}")
    return r.json() if r.content else {}


def find_record(s, api, title):
    """Our record, matched by exact title among the token owner's depositions: the published one if there is
    one (newversion then returns any unpublished draft already started), else an unpublished first draft
    left by an earlier run, else None."""
    r = s.get(f"{api}/deposit/depositions", params={"size": 100, "sort": "-mostrecent", "all_versions": "false"})
    hits = [d for d in check(r, "list depositions") if d.get("metadata", {}).get("title", d.get("title")) == title]
    published = [d for d in hits if d.get("submitted")]
    concepts = {d.get("conceptrecid") for d in published}
    if len(concepts) > 1:
        sys.exit(f"STOP: {len(concepts)} published records are titled {title!r} ({[d['id'] for d in published]}); expected one.")
    if published:
        return max(published, key=lambda d: d["id"])  # newversion needs the latest version's id; ids only grow
    if len(hits) > 1:
        sys.exit(f"STOP: {len(hits)} unpublished drafts are titled {title!r} ({[d['id'] for d in hits]}); delete the extras on Zenodo.")
    return hits[0] if hits else None


def same_files(s, api, dep, paths):
    files = check(s.get(f"{api}/deposit/depositions/{dep['id']}/files"), "list files")
    have = {f["filename"]: f["checksum"].removeprefix("md5:") for f in files}
    want = {os.path.basename(p): md5(p) for p in paths}
    return have == want


def archive(s, api, meta, paths, version, publish=True):
    dep = find_record(s, api, meta["title"])
    if dep is None:
        print("no deposition with this title yet: creating the first version")
        draft = check(s.post(f"{api}/deposit/depositions", json={}), "create deposition")
    elif dep.get("submitted") and same_files(s, api, dep, paths):
        print(f"files are unchanged since version {dep['metadata'].get('version')} ({dep.get('doi')}); nothing archived")
        return dep
    elif dep.get("submitted"):
        new = check(s.post(f"{api}/deposit/depositions/{dep['id']}/actions/newversion"), "newversion")
        draft = check(s.get(new["links"]["latest_draft"]), "open new version draft")
    else:
        print(f"reusing unpublished draft {dep['id']} from an earlier run")
        draft = dep
    # metadata first, so a run that stops part way leaves a draft the next run can find by its title
    md = dict(meta, publication_date=date.today().isoformat(), version=version)
    draft = check(s.put(f"{api}/deposit/depositions/{draft['id']}", json={"metadata": md}), "set metadata")
    old_files = check(s.get(f"{api}/deposit/depositions/{draft['id']}/files"), "list draft files")
    for f in old_files:  # a new version starts with a copy of the previous version's files
        check(s.delete(f"{api}/deposit/depositions/{draft['id']}/files/{f['id']}"), f"delete old file {f['filename']}")
    for p in paths:
        with open(p, "rb") as fh:
            check(s.put(f"{draft['links']['bucket']}/{os.path.basename(p)}", data=fh), f"upload {os.path.basename(p)}")
        print("uploaded", os.path.basename(p), f"{os.path.getsize(p):,} bytes")
    if not publish:
        print(f"ZENODO_PUBLISH=false: left unpublished draft {draft['id']}: {draft['links'].get('html')}")
        return draft
    done = check(s.post(f"{api}/deposit/depositions/{draft['id']}/actions/publish"), "publish")
    print(f"published version {version}: DOI {done.get('doi')}, concept DOI (cite this for all versions) {done.get('conceptdoi')}")
    return done


def main():
    token = os.environ.get("ZENODO_TOKEN", "").strip()
    if not token:
        print("::notice::ZENODO_TOKEN secret is not set, so no Zenodo snapshot was made. See the README section "
              "'Zenodo archive' for setup. This is not an error.")
        return
    api = os.environ.get("ZENODO_API", "").strip() or "https://zenodo.org/api"
    publish = os.environ.get("ZENODO_PUBLISH", "true").strip().lower() != "false"
    meta = json.load(open(os.path.join(ROOT, ".zenodo.json")))
    cfg = data_docs.load_config()
    sha = data_docs.fetch(DATA, cfg)
    from huggingface_hub import hf_hub_download
    hf_hub_download(cfg["dataset"], "README.md", repo_type="dataset", revision=sha, local_dir=DATA)
    paths = [os.path.join(DATA, t["file"]) for t in cfg["tables"].values()] + [os.path.join(DATA, "README.md")]
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {token}"
    print("Zenodo API:", api)
    archive(s, api, meta, paths, f"{date.today().isoformat()} (Hugging Face {sha[:8]})", publish)


if __name__ == "__main__":
    main()
