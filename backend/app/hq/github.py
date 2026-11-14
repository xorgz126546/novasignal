"""GitHub REST client for HQ.

Reads (Scout): public search, repo metadata/license, README, latest commit — ETag-cached, rate-limit aware.
Writes (Engineer only): create `agent/*` branches, commit files there, open DRAFT pull requests, read PR state.
Hard limits enforced here: writes only to the configured repo, never to `main`/default branch, never merge.
"""
from __future__ import annotations

import base64
import logging
import time

import httpx

from ..config import get_settings

log = logging.getLogger("cryptonex.hq.github")
API = "https://api.github.com"


class GitHubError(Exception):
    pass


class GitHub:
    def __init__(self, token: str | None = None, repo: str | None = None, transport: httpx.AsyncBaseTransport | None = None):
        s = get_settings()
        self.token = s.github_token if token is None else token
        self.repo = repo or s.github_repo
        self._etags: dict[str, tuple[str, object]] = {}
        self.remaining: int | None = None
        self.reset_at: float = 0
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": "cryptonex-hq"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        self._c = httpx.AsyncClient(base_url=API, headers=headers, timeout=20, transport=transport)

    async def close(self):
        await self._c.aclose()

    async def _req(self, method: str, path: str, **kw):
        if self.remaining == 0 and time.time() < self.reset_at:
            raise GitHubError(f"rate limit exhausted until {time.strftime('%H:%M:%S', time.gmtime(self.reset_at))} UTC")
        headers = kw.pop("headers", {})
        cached = self._etags.get(path) if method == "GET" else None
        if cached:
            headers["If-None-Match"] = cached[0]
        try:
            r = await self._c.request(method, path, headers=headers, **kw)
        except httpx.HTTPError as e:
            raise GitHubError(f"network error: {type(e).__name__}") from None
        if r.headers.get("x-ratelimit-remaining", "").isdigit():
            self.remaining = int(r.headers["x-ratelimit-remaining"])
            self.reset_at = float(r.headers.get("x-ratelimit-reset", "0") or 0)
        if r.status_code == 304 and cached:
            return cached[1]
        if r.status_code >= 400:
            msg = r.json().get("message", "") if "json" in r.headers.get("content-type", "") else r.text[:200]
            raise GitHubError(f"{method} {path} → {r.status_code} {msg}")
        data = r.json() if r.content else {}
        if method == "GET" and r.headers.get("etag"):
            self._etags[path] = (r.headers["etag"], data)
        return data

    # ---------------- reads (public) ----------------
    async def search_repos(self, query: str, per_page: int = 5) -> list[dict]:
        data = await self._req("GET", "/search/repositories", params={"q": query, "sort": "stars", "order": "desc",
                                                                      "per_page": per_page})
        return data.get("items", [])

    async def repo(self, full: str) -> dict:
        return await self._req("GET", f"/repos/{full}")

    async def readme(self, full: str) -> str:
        data = await self._req("GET", f"/repos/{full}/readme")
        return base64.b64decode(data.get("content", "")).decode("utf-8", "replace")

    async def latest_sha(self, full: str, branch: str) -> str:
        data = await self._req("GET", f"/repos/{full}/commits/{branch}")
        return data.get("sha", "")

    # ---------------- writes (Engineer only; agent/* branches, draft PRs) ----------------
    def _check_branch(self, branch: str) -> None:
        if not branch.startswith("agent/") or branch in ("main", "master"):
            raise GitHubError("refusing to write outside agent/* branches")

    async def file(self, path: str, ref: str = "main") -> tuple[str, str]:
        data = await self._req("GET", f"/repos/{self.repo}/contents/{path}", params={"ref": ref})
        return base64.b64decode(data["content"]).decode("utf-8"), data["sha"]

    async def create_branch(self, branch: str, from_branch: str = "main") -> None:
        self._check_branch(branch)
        sha = (await self._req("GET", f"/repos/{self.repo}/git/ref/heads/{from_branch}"))["object"]["sha"]
        await self._req("POST", f"/repos/{self.repo}/git/refs", json={"ref": f"refs/heads/{branch}", "sha": sha})

    async def put_file(self, branch: str, path: str, content: str, message: str, sha: str | None = None) -> None:
        self._check_branch(branch)
        body = {"message": message, "content": base64.b64encode(content.encode()).decode(), "branch": branch}
        if sha:
            body["sha"] = sha
        await self._req("PUT", f"/repos/{self.repo}/contents/{path}", json=body)

    async def open_draft_pr(self, branch: str, title: str, body: str, base: str = "main") -> dict:
        self._check_branch(branch)
        return await self._req("POST", f"/repos/{self.repo}/pulls",
                               json={"title": title, "head": branch, "base": base, "body": body, "draft": True})

    async def pull(self, number: int) -> dict:
        return await self._req("GET", f"/repos/{self.repo}/pulls/{number}")
