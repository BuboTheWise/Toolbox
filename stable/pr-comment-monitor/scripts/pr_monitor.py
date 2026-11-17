#!/usr/bin/env python3
"""pr_monitor.py — Scan third-party open PRs for comments needing professional replies from BuboTheWise."""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Skill directory (where this script lives)
SKILL_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = SKILL_DIR / "config" / "repos.json"
DEFAULT_REPOS = ["ollama/ollama", "huggingface/transformers"]


def load_repos():
    """Load configured repos from config file. Falls back to defaults with warning."""
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                data = json.load(f)
            return data.get("repos", DEFAULT_REPOS)
        except (json.JSONDecodeError, IOError) as e:
            print(f"⚠️  Warning: could not load {CONFIG_PATH}: {e}", file=sys.stderr)
            print("   Using defaults.", file=sys.stderr)
    return DEFAULT_REPOS


def run_cmd(cmd, timeout=30):
    """Run a shell command, return (stdout, stderr, rc)."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        return result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        return "", "timeout", 124


def api_get(repo, resource):
    """Call gh api GET /repos/{repo}/{resource}. Returns (parsed_json, error_string)."""
    cmd = ["gh", "api", f"repos/{repo}/{resource}", "--jq", ".[]"]
    stdout, stderr, rc = run_cmd(cmd)
    if rc != 0:
        err_msg = stderr.strip()
        # Rate limit detection
        if "rate limit" in err_msg.lower() or "403" in err_msg:
            return None, f"RATE_LIMITED"
        return None, err_msg[:200]

    results = []
    for line in stdout.strip().split("\n"):
        if not line.strip():
            continue
        try:
            results.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return results, None


def pr_open_count(repo):
    """Get count of open PRs for a repo via API."""
    stdout, stderr, rc = run_cmd(
        ["gh", "api", f"repos/{repo}/pulls?state=open&per_page=1", "-H", "Accept: application/vnd.github+json"],
        timeout=15
    )
    # Check X-RateLimit-Remaining header
    if "x-ratelimit-remaining" in stderr.lower():
        pass
    return 0


def scan_repo(repo, since_days=14):
    """Scan a single repo for actionable PR comments."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)

    # Get list of open PRs (just numbers — use page to avoid massive output)
    stdout, stderr, rc = run_cmd(["gh", "pr", "list", "--repo", repo, "--state", "open", "-L", "100"], timeout=30)
    if rc != 0:
        return {
            "repo": repo,
            "error": f"gh failed: {stderr.strip()[:200]}",
            "actionable": [],
            "total_open": 0,
            "rate_limited": False,
        }

    pr_lines = [l for l in stdout.strip().split("\n") if l.strip()]
    total_open = len(pr_lines)

    actionable = []
    skipped = {"no_comments": 0, "too_old": 0}

    # Track PRs with comments to avoid duplicate work
    checked_prs = set()

    # Strategy: iterate PRs, check their review comments via API
    for line in pr_lines[:100]:  # Cap at 100
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            pr_num = int(parts[0])
        except ValueError:
            continue

        if pr_num in checked_prs:
            continue
        checked_prs.add(pr_num)

        # Get review comments for this PR
        comments, err = api_get(repo, f"pulls/{pr_num}/comments")
        if err == "RATE_LIMITED":
            return {
                "repo": repo,
                "error": "API rate limit hit — skipping remaining repos",
                "actionable": actionable,
                "total_open": total_open,
                "rate_limited": True,
            }
        if err:
            continue  # PR not found or API error on this PR

        # Get general comments (not review-specific) for richer context
        pr_comments, _ = api_get(repo, f"issues/{pr_num}/comments")
        all_comments = list(comments) + list(pr_comments if pr_comments else [])

        for comment in all_comments:
            result = needs_response(comment, cutoff)
            if result["needs_resp"]:
                actionable.append({
                    "repo": repo,
                    "pr_number": pr_num,
                    "commenter": comment.get("user", {}).get("login", "?"),
                    "date": comment.get("created_at", ""),
                    "body": format_context(comment),
                    "is_review": comment.get("commit_id") is not None,
                    "html_url": comment.get("html_url", f"https://github.com/{repo}/pull/{pr_num}"),
                })

    return {
        "repo": repo,
        "total_open": total_open,
        "actionable": actionable,
        "skipped": skipped,
        "rate_limited": False,
    }


def format_context(comment):
    """Extract short context from a comment."""
    body = comment.get("body", "").strip()
    if not body:
        return "(no body text)"

    # If it has HTML content, try to get plain text approximation
    lines = [l.strip() for l in body.split("\n") if l.strip()]
    if lines:
        text = lines[0][:120]
        return text + ("..." if len(lines[0]) > 120 else "")
    return "(no meaningful body)"


def needs_response(comment, cutoff):
    """Determine if a comment needs a response from BuboTheWise."""
    author = comment.get("user", {}).get("login", "")

    # Skip our own comments (BuboTheWise)
    if author.lower() in ("bubothewise", "nyarlathotep"):
        return {"needs_resp": False, "reason": "own comment"}

    body = comment.get("body", "").strip()
    if not body:
        return {"needs_resp": False, "reason": "empty body"}

    # Skip stale comments (>14 days old)
    created_str = comment.get("created_at", "")
    if created_str:
        try:
            created_dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
            if created_dt < cutoff:
                return {"needs_resp": False, "reason": f"stale ({(cutoff - created_dt).days} days ago)"}
        except ValueError:
            pass

    body_lower = body.lower()

    # Question patterns — likely need response
    question_patterns = [
        "how does", "why did", "why not", "could you", "should we",
        "is there", "what about", "can you explain", "are these",
        "does this handle", "should this be"
    ]
    if any(q in body_lower for q in question_patterns):
        return {"needs_resp": True, "reason": "question pattern"}

    # Design rationale requests
    design_patterns = [
        "rationale", "design choice", "why choose", "motivation",
        "background on", "context for", "explain why", "intended behavior"
    ]
    if any(d in body_lower for d in design_patterns):
        return {"needs_resp": True, "reason": "design rationale request"}

    # Suggestion/imperative patterns that likely need discussion
    suggestion_indicators = [
        ("change ", 1), ("update ", 1), ("fix ", 1), ("add ", 1),
        ("remove ", 1), ("rename ", 1), ("move ", 1), ("consider ", 1),
        ("instead of ", 1), ("could also", 0.5), ("would be better", 1),
        ("might want", 0.5), ("you might", 0.5), ("i think you", 0.5),
        ("please add", 1), ("please fix", 1),
    ]

    score = 0
    for pattern, weight in suggestion_indicators:
        if pattern in body_lower:
            score += weight

    if score >= 1:
        return {"needs_resp": True, "reason": f"suggestion (score: {score:.1f})"}

    # Reject pure nits
    nit_patterns = ["nit:", "nit ", "typo", "formatting issue", "style fix"]
    if any(n in body_lower for n in nit_patterns):
        return {"needs_resp": False, "reason": "pure nit"}

    return {"needs_resp": False, "reason": "no actionable pattern"}


def generate_draft(prompt_text):
    """Generate a professional draft reply. In cron context, called by the skill with model access."""
    # This is called by the cron runner — it gets the LLM to produce the draft.
    # For standalone dry-run / testing, we return a template.
    return f"[DRAFT] Respond to @{prompt_text.split()[3] if len(prompt_text.split()) > 3 else 'reviewer'}: \"Thank you for the feedback on this PR. Let me address your concern about {prompt_text.split()[-1]}. I believe the current approach works because... [model will fill this in during cron execution]\""


def main():
    parser = argparse.ArgumentParser(
        description="Scan third-party open PRs for comments needing professional replies from BuboTheWise."
    )
    parser.add_argument("--dry-run", action="store_true", help="Show repo stats without analyzing comments")
    parser.add_argument("--repo", type=str, help="Scan only a specific repo (owner/repo)")
    parser.add_argument("--since-days", type=int, default=14,
                        help="Only consider comments within last N days (default: 14)")
    parser.add_argument("--generate-drafts", action="store_true", help="Generate draft replies for actionable items")
    args = parser.parse_args()

    repos = load_repos()
    if args.repo:
        repos = [args.repo]

    print(f"📋 PR Comment Monitor — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"   Monitored repos: {', '.join(repos)}")
    print(f"   Scan window: last {args.since_days} days\n")

    all_actionable = []
    has_error = False
    rate_limited = False

    for repo in repos:
        if args.dry_run:
            stdout, stderr, rc = run_cmd(
                ["gh", "api", f"repos/{repo}/pulls?state=open&per_page=1", "-H", "Accept: application/vnd.github+json"],
                timeout=15
            )
            if rc == 0 and not stderr.strip():
                print(f"✅ {repo}: API accessible")
            else:
                print(f"❌ {repo}: API error — {stderr.strip()[:100]}")
            continue

        result = scan_repo(repo, args.since_days)

        if result.get("rate_limited"):
            rate_limited = True
            has_error = True
            print(f"⚠️  {result['repo']}: Rate limited — stopping scan. Please wait and rerun.\n")
            break

        if result.get("error"):
            has_error = True
            print(f"❌ {result['repo']}: {result['error']}\n")
            continue

        n = len(result["actionable"])
        total = result.get("total_open", "?")

        if n > 0:
            print(f"🔍 {result['repo']}: **{n}** comment(s) needing response (of {total} open PRs)")
            all_actionable.extend(result["actionable"])
        else:
            print(f"✅ {result['repo']}: no comments needing response")

    # Output actionable items with draft prompts
    if all_actionable:
        print("\n" + "=" * 60)
        print("ACTIONABLE ITEMS\n")
        for i, item in enumerate(all_actionable, 1):
            print(f"--- #{i} ---")
            print(f"**Repo:** {item['repo']} PR #{item['pr_number']}")
            print(f"**Commenter:** @{item['commenter']} ({item['date'][:10]})")
            print(f"**Link:** {item['html_url']}")
            print(f"**Context:** {item['body']}")
            if item.get('is_review'):
                print("**Type:** Review comment (code feedback)")
            print()
            if args.generate_drafts:
                draft = generate_draft(item['body'])
                print(draft)
            else:
                print("*Run with --generate-drafts to produce AI-generated reply drafts.*")
            print()
    else:
        print("\n" + "=" * 60)
        print("✅ No comments needing response across all monitored repos.")

    if has_error and not rate_limited:
        print("\n⚠️  Some repos had errors. Check output above for details.")

    return 1 if rate_limited else 0


if __name__ == "__main__":
    sys.exit(main())
