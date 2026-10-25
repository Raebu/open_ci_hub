#!/usr/bin/env python3
"""Verified, opt-in GitHub branch cleanser for Open CI Hub.

The normal scheduled mode retires *only* branches proven merged and inactive.
Every deletion uses remote-SHA compare-and-swap and a verified permanent tag.
Unique, unmerged, unreviewed and active branches are never deleted by a timer.
"""
from __future__ import annotations
import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "config" / "branch-cleanser.json"
SHA = re.compile(r"^[a-f0-9]{40}$")
REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./-]{0,180}$")
SUCCESS = {"closed", "merged"}

class GuardError(RuntimeError):
    pass

def validated_branch(name: str) -> str:
    if (not isinstance(name,str) or not BRANCH.fullmatch(name) or
        ".." in name or "//" in name or name.endswith(("/", ".", ".lock")) or
        name.startswith(("archive/", "refs/", "-")) or
        any(x in (".","..") for x in name.split("/"))):
        raise GuardError("Invalid Git branch name.")
    return name

def read_policy(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise GuardError("Refusing missing or symlinked policy file.")
    raw = path.read_bytes()
    if len(raw) > 16_384:
        raise GuardError("Policy file is too large.")
    data=json.loads(raw)
    if not isinstance(data,dict) or set(data)!={"schema","repository","base","min_age_hours","protected"}:
        raise GuardError("Invalid branch-cleanser policy envelope.")
    if data["schema"]!="open-ci-hub.branch-cleanser.v1" or data["repository"]!="Raebu/open_ci_hub" or not REPO.fullmatch(data["repository"]):
        raise GuardError("Unsupported policy/repository.")
    if data["base"]!="main":
        raise GuardError("Only main can be this cleanser's base.")
    if type(data["min_age_hours"]) is not int or not 48<=data["min_age_hours"]<=720:
        raise GuardError("Grace period must be 48–720 hours.")
    protected=data["protected"]
    if not isinstance(protected,list) or len(protected)>50:
        raise GuardError("Invalid protected branches.")
    for branch in protected:
        validated_branch(branch)
    return data

def _exec(argv, *, cwd=ROOT, timeout=20, max_output=1_000_000):
    env=dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="Never", GH_PROMPT_DISABLED="1")
    try:
        result=subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=timeout, check=False)
    except (OSError,subprocess.TimeoutExpired):
        raise GuardError("Provider command failed or timed out.") from None
    if result.returncode or len(result.stdout)>max_output:
        raise GuardError("Provider command failed, returned unexpected output or denied access.")
    return result.stdout.strip()

def git(*args, timeout=20):
    return _exec(["git", *args],timeout=timeout)

def api(endpoint, *, timeout=20):
    # Only fixed repository API paths generated from owner policy. No user URL.
    return json.loads(_exec(["gh","api",endpoint],timeout=timeout))

def _repo_origin_matches(repo):
    origin=git("remote","get-url","origin")
    allowed=(f"https://github.com/{repo}", f"https://github.com/{repo}.git",
             f"git@github.com:{repo}.git", f"git@github.com:{repo}")
    if origin not in allowed:
        raise GuardError("Local Git origin does not match the approved repository.")

def _worktree_refs():
    output=git("worktree","list","--porcelain")
    return {line[len("branch refs/heads/"):] for line in output.splitlines()
            if line.startswith("branch refs/heads/")}

def _sync_remote():
    # Fetch refs only, never checkout or merge a branch into live work.
    git("fetch","--quiet","--prune","origin",timeout=60)

def _heads_from_remote():
    raw=git("ls-remote","--heads","origin",timeout=40)
    heads={}
    for line in raw.splitlines():
        pieces=line.split("\t")
        if len(pieces)!=2 or not pieces[1].startswith("refs/heads/") or not SHA.fullmatch(pieces[0]):
            raise GuardError("Unexpected remote-branch response.")
        branch=validated_branch(pieces[1].removeprefix("refs/heads/"))
        heads[branch]=pieces[0]
    if not heads or "main" not in heads or len(heads)>500:
        raise GuardError("Remote has missing main or too many branches; require manual audit.")
    return heads

def _protected_from_github(repository):
    repo=repository
    branches=[]
    page=1
    while True:
        rows=api(f"repos/{repo}/branches?per_page=100&page={page}",timeout=35)
        if not isinstance(rows,list):
            raise GuardError("Invalid GitHub branch list.")
        for obj in rows:
            if (not isinstance(obj,dict) or not isinstance(obj.get("name"),str)
                or type(obj.get("protected")) is not bool
                or not isinstance(obj.get("commit"),dict)
                or not SHA.fullmatch(obj["commit"].get("sha",""))):
                raise GuardError("Invalid protected-branch response.")
            branches.append(obj)
        if len(rows)<100:
            break
        page+=1
        if page>5:
            raise GuardError("More than 500 branches; stop for human audit.")
    return {b["name"]:(b["commit"]["sha"],b["protected"]) for b in branches}

def _open_pr_heads(repository):
    branches=set()
    page=1
    while True:
        prs=api(f"repos/{repository}/pulls?state=open&per_page=100&page={page}",timeout=35)
        if not isinstance(prs,list):
            raise GuardError("Invalid GitHub pull-request list.")
        for pr in prs:
            if not isinstance(pr,dict) or not isinstance(pr.get("head"),dict):
                raise GuardError("Invalid open pull-request.")
            name=pr["head"].get("ref")
            if isinstance(name,str):
                branches.add(name)
        if len(prs)<100:
            break
        page+=1
        if page>5:
            raise GuardError("Too many open PRs for automated cleanup.")
    return branches

def _head_timestamp(sha):
    # Use a verified Git object, never unauthenticated date strings.
    if not SHA.fullmatch(sha):
        raise GuardError("Invalid Git head SHA.")
    output=git("show","-s","--format=%ct",sha)
    if not output.isdigit():
        raise GuardError("Commit timestamp unavailable.")
    return int(output)

def _is_ancestor(sha, base):
    result=subprocess.run(["git","merge-base","--is-ancestor",sha,base],
                          cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                          timeout=10,check=False)
    if result.returncode not in (0,1):
        raise GuardError("Cannot determine commit ancestry.")
    return result.returncode==0

def _merged_pr(repository, branch, sha):
    # Squashed PRs can be merged even if the original commits aren't ancestors.
    # Exact head SHA and expected base are still required, no guessing by title.
    prs=api(f"repos/{repository}/commits/{sha}/pulls?per_page=100",timeout=25)
    if not isinstance(prs,list):
        raise GuardError("Invalid merged-PR evidence.")
    return any(isinstance(p,dict) and p.get("merged_at") and
               isinstance(p.get("head"),dict) and p["head"].get("ref")==branch and
               p["head"].get("sha")==sha and
               isinstance(p.get("base"),dict) and
               p["base"].get("ref")=="main" and
               p.get("state")=="closed" for p in prs)

def inspect(config:dict, *, now=None, provider=None):
    _repo_origin_matches(config["repository"])
    _sync_remote()
    now=time.time() if now is None else now
    remote=_heads_from_remote()
    provider=provider or GithubProvider(config["repository"])
    gh=provider.heads()
    if set(gh)!=set(remote):
        raise GuardError("GitHub and Git remote branch lists disagree; refusing cleanup.")
    active=_worktree_refs()
    open_prs=provider.open_pr_heads()
    default=remote["main"]
    findings=[]
    for name, sha in sorted(remote.items()):
        if gh[name][0]!=sha:
            raise GuardError("GitHub/remote branch SHA disagreement.")
        status="preserve"
        why="not merged; would require code review before retirement"
        age=None
        if name=="main" or name in config["protected"] or gh[name][1]:
            why="default or protected branch"
        elif name in active:
            why="checked out in a local Git worktree"
        elif name in open_prs:
            why="open pull request"
        else:
            # Git objects might be absent in a sparse/local checkout:
            # fail closed rather than fetch or trust an external date.
            try:
                age=max(0,int((now-_head_timestamp(sha))/3600))
                if age<config["min_age_hours"]:
                    why="within minimum grace period"
                elif _is_ancestor(sha,default) or provider.merged_pr(name,sha):
                    status="eligible"
                    why="verified merged and past grace period"
            except (GuardError,OSError,subprocess.TimeoutExpired):
                why="unable to prove branch merged safely"
        findings.append({"branch":name,"sha":sha,"status":status,
                         "reason":why,"age_hours":age})
    return findings

class GithubProvider:
    def __init__(self, repository):
        self.repo=repository
    def heads(self):
        return _protected_from_github(self.repo)
    def open_pr_heads(self):
        return _open_pr_heads(self.repo)
    def merged_pr(self, branch, sha):
        return _merged_pr(self.repo,branch,sha)

def _archive_tag(branch,sha):
    # Namespaced immutable archive protects source history even after deletion.
    return f"archive/cleanser/{branch}/{sha[:12]}"

def archive_and_delete(branch,sha, *, provider=None):
    validated_branch(branch)
    if branch=="main" or not SHA.fullmatch(sha):
        raise GuardError("Never retire a default/invalid ref.")
    tag=_archive_tag(branch,sha)
    ref=f"refs/tags/{tag}"
    remote=git("ls-remote","origin",ref,ref+"^{}")
    if remote:
        # A tag may have been written on a previous partial run. Prove exact
        # annotated-tag target before using it as the retention guarantee.
        peeled=[row.split("\t")[0] for row in remote.splitlines()
                if row.endswith(ref+"^{}")]
        if peeled!=[sha]:
            raise GuardError("Archive tag exists but does not point to the reviewed commit.")
    else:
        git("tag","-a",tag,sha,"-m",
            "Automatic Git branch retention: archived exact reviewed head "+sha,
            timeout=20)
        git("push","origin",ref,timeout=40)
        verified=git("ls-remote","origin",ref+"^{}")
        if verified.split("\t")[0]!=sha:
            raise GuardError("Remote archive verification failed; branch retained.")
    current=git("ls-remote","origin",f"refs/heads/{branch}").split("\t")
    if len(current)!=2 or current[0]!=sha:
        raise GuardError("Branch moved since review; branch retained.")
    # git push lease is atomic w.r.t. branch head: remote changes abort deletion.
    git("push",f"--force-with-lease=refs/heads/{branch}:{sha}","origin",
        f":refs/heads/{branch}",timeout=40)
    return {"branch":branch,"result":"deleted","archive":tag,"sha":sha}

def run(config, *, apply=False, now=None, provider=None):
    findings=inspect(config,now=now,provider=provider)
    retired=[]
    if apply:
        for f in findings:
            if f["status"]!="eligible":
                continue
            # Recheck provider guardrails immediately before touching a ref.
            # If permissions, protection or PR status have changed, do not delete.
            fresh=provider or GithubProvider(config["repository"])
            meta=fresh.heads()
            if (f["branch"] not in meta or meta[f["branch"]]!=(f["sha"],False)
                or f["branch"] in fresh.open_pr_heads()
                or f["branch"] in _worktree_refs()):
                raise GuardError("Branch safety changed since review; no more deletion.")
            retired.append(archive_and_delete(f["branch"],f["sha"]))
    return {"repository":config["repository"],"dry_run":not apply,
            "eligible":sum(f["status"]=="eligible" for f in findings),
            "retired":retired,"findings":findings}

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config",type=Path,default=DEFAULT_CONFIG)
    parser.add_argument("--apply",action="store_true",
        help="Delete ONLY verified merged inactive aged branches after permanent archive")
    parser.add_argument("--confirm",action="store_true",
        help="Explicit noninteractive approval required for --apply")
    args=parser.parse_args(argv)
    try:
        if args.apply and not args.confirm:
            raise GuardError("--apply requires explicit --confirm, including service setup.")
        config=read_policy(args.config)
        result=run(config,apply=args.apply)
        print(json.dumps(result,indent=2,sort_keys=True))
        return 0
    except (GuardError,ValueError,TypeError) as exc:
        print("Branch-cleanser refused: "+str(exc)[:180],file=sys.stderr)
        return 2

if __name__=="__main__":
    raise SystemExit(main())
