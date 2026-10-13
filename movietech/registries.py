"""Grounder lookups: ask the real npm, PyPI and GitHub registries whether a dependency exists,
is maintained and has a usable licence. Plain code, so the facts don't come from a model.

Only five fields are kept from each response, so nothing bulky reaches a model later.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

from movietech import config
from movietech.schemas import Dependency, Grounding


def fetch_json(url: str) -> dict | None:
    """GET a JSON document. None means 404 (not found); other errors raise."""
    headers = {"User-Agent": "movietech-reveng", "Accept": "application/json"}
    if "api.github.com" in url and os.getenv("GITHUB_TOKEN"):
        # Optional: lifts GitHub's limit from 60 to 5,000 requests an hour.
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers),
                                    timeout=15) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def _npm(name: str, fetch) -> dict | None:
    doc = fetch(f"https://registry.npmjs.org/{urllib.parse.quote(name, safe='@')}")
    if doc is None:
        return None
    latest = doc.get("dist-tags", {}).get("latest", "")
    lic = doc.get("license", "")
    return {"latest_version": latest, "last_release": doc.get("time", {}).get(latest, "")[:10],
            "license": lic.get("type", "") if isinstance(lic, dict) else lic,
            "archived": doc.get("versions", {}).get(latest, {}).get("deprecated") is not None}


def _pypi(name: str, fetch) -> dict | None:
    doc = fetch(f"https://pypi.org/pypi/{urllib.parse.quote(name)}/json")
    if doc is None:
        return None
    info = doc.get("info", {})
    files = doc.get("urls", [])  # the files of the latest release
    lic = info.get("license_expression") or info.get("license") or ""
    if not lic or len(lic) > 40:  # some packages paste the whole licence text here
        classifiers = [c.rsplit(" :: ", 1)[-1] for c in info.get("classifiers", [])
                       if c.startswith("License ::")]
        lic = classifiers[0] if classifiers else ""
    return {"latest_version": info.get("version", ""),
            "last_release": files[0].get("upload_time_iso_8601", "")[:10] if files else "",
            "license": lic, "archived": bool(info.get("yanked"))}


def _github(name: str, fetch) -> dict | None:
    doc = fetch(f"https://api.github.com/repos/{name}")
    if doc is None:
        return None
    return {"latest_version": "", "last_release": doc.get("pushed_at", "")[:10],
            "license": (doc.get("license") or {}).get("spdx_id", ""),
            "archived": doc.get("archived", False)}


# PyPI classifiers name licences in words; map the common ones to SPDX ids.
_LICENSE_NAMES = {"MIT License": "MIT", "Apache Software License": "Apache-2.0",
                  "BSD License": "BSD-3-Clause", "ISC License (ISCL)": "ISC",
                  "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0"}


def ground(dep: Dependency, fetch=fetch_json, today: date | None = None) -> Grounding:
    """Look one dependency up and judge it: missing, archived, stale, licence, or ok."""
    lookup = {"npm": _npm, "pypi": _pypi, "github": _github}[dep.ecosystem]
    base = Grounding(ecosystem=dep.ecosystem, name=dep.name, status="ok")
    try:
        found = lookup(dep.name, fetch)
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        return base.model_copy(update={"status": "error", "note": f"lookup failed: {e}"})
    if found is None:
        return base.model_copy(update={"status": "missing",
                                       "note": f"not found on {dep.ecosystem}"})
    lic = _LICENSE_NAMES.get(found["license"], found["license"])
    g = base.model_copy(update={"latest_version": found["latest_version"],
                                "last_release": found["last_release"], "license": lic})
    if found["archived"]:
        return g.model_copy(update={"status": "archived",
                                    "note": "archived, deprecated or yanked"})
    if found["last_release"]:
        age = ((today or date.today()) - date.fromisoformat(found["last_release"])).days
        if age > config.STALE_AFTER_DAYS:
            return g.model_copy(update={"status": "stale",
                                        "note": f"no release for {age // 365} years"})
    if not lic:
        return g.model_copy(update={"status": "license", "note": "no licence declared"})
    if not any(ok in lic for ok in config.OK_LICENSES):
        return g.model_copy(update={"status": "license",
                                    "note": f"licence {lic} is not on the allowed list"})
    return g
