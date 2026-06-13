import os
import re
import json
import requests

GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
NOTION_TOKEN = os.environ["NOTION_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
GITHUB_USERNAME = os.environ.get("GITHUB_USERNAME", "nitrimandylis")
RECLASSIFY = os.environ.get("RECLASSIFY", "false").lower() == "true"

NOTION_DB_ID = "cb1788bf2a1d4a7eb3e46b5daea238a8"

GH_HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
}

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

ANTHROPIC_HEADERS = {
    "x-api-key": ANTHROPIC_API_KEY,
    "anthropic-version": "2023-06-01",
    "Content-Type": "application/json",
}

LANGUAGE_MAP = {
    "Python": "Python",
    "JavaScript": "JavaScript",
    "TypeScript": "TypeScript",
    "Swift": "Swift",
    "HTML": "HTML/CSS",
    "CSS": "HTML/CSS",
    "React": "React",
}

VALID_CATEGORIES = ["IB IA", "Side Project", "Competition", "Learning", "Tool/Automation", "Web App"]
VALID_STATUSES = ["Idea", "In Progress", "Paused", "Shipped", "Archived"]
VALID_STACK = ["Python", "JavaScript", "HTML/CSS", "TypeScript", "Swift", "React", "Node.js", "Flask", "Other"]

MAX_BLOCKS = 90
RICH_TEXT_LIMIT = 1900


# ── Markdown → Notion blocks ──────────────────────────────────────────────────

def rich_text(content):
    chunks = []
    while content:
        chunks.append({"type": "text", "text": {"content": content[:RICH_TEXT_LIMIT]}})
        content = content[RICH_TEXT_LIMIT:]
    return chunks


def markdown_to_blocks(md):
    blocks = []
    in_code_block = False
    code_lines = []
    code_lang = "plain text"

    for line in md.splitlines():
        if len(blocks) >= MAX_BLOCKS:
            break

        if line.startswith("```"):
            if not in_code_block:
                in_code_block = True
                code_lang = line[3:].strip() or "plain text"
                code_lines = []
            else:
                in_code_block = False
                code_content = "\n".join(code_lines)
                if code_content.strip():
                    blocks.append({
                        "object": "block", "type": "code",
                        "code": {
                            "rich_text": rich_text(code_content[:RICH_TEXT_LIMIT]),
                            "language": code_lang if code_lang in [
                                "python", "javascript", "typescript", "bash",
                                "shell", "json", "yaml", "html", "css", "plain text",
                            ] else "plain text",
                        },
                    })
            continue

        if in_code_block:
            code_lines.append(line)
            continue

        h3 = re.match(r"^### (.+)", line)
        h2 = re.match(r"^## (.+)", line)
        h1 = re.match(r"^# (.+)", line)

        if h1:
            blocks.append({"object": "block", "type": "heading_1",
                           "heading_1": {"rich_text": rich_text(h1.group(1))}})
        elif h2:
            blocks.append({"object": "block", "type": "heading_2",
                           "heading_2": {"rich_text": rich_text(h2.group(1))}})
        elif h3:
            blocks.append({"object": "block", "type": "heading_3",
                           "heading_3": {"rich_text": rich_text(h3.group(1))}})
        elif line.strip():
            clean = re.sub(r"!\[.*?\]\(.*?\)", "", line)
            clean = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", clean)
            clean = re.sub(r"[*_`]{1,2}([^*_`]+)[*_`]{1,2}", r"\1", clean)
            clean = clean.strip()
            if clean:
                blocks.append({"object": "block", "type": "paragraph",
                               "paragraph": {"rich_text": rich_text(clean)}})

    return blocks


# ── Notion block helpers ──────────────────────────────────────────────────────

def clear_page_blocks(page_id):
    r = requests.get(
        f"https://api.notion.com/v1/blocks/{page_id}/children",
        headers=NOTION_HEADERS,
        params={"page_size": 100},
    )
    r.raise_for_status()
    for block in r.json().get("results", []):
        requests.delete(f"https://api.notion.com/v1/blocks/{block['id']}", headers=NOTION_HEADERS)


def set_page_readme(page_id, readme):
    clear_page_blocks(page_id)
    if not readme.strip():
        return
    blocks = markdown_to_blocks(readme)
    if blocks:
        r = requests.patch(
            f"https://api.notion.com/v1/blocks/{page_id}/children",
            headers=NOTION_HEADERS,
            json={"children": blocks},
        )
        r.raise_for_status()


# ── GitHub helpers ────────────────────────────────────────────────────────────

def get_all_repos():
    repos = []
    page = 1
    while True:
        r = requests.get(
            f"https://api.github.com/users/{GITHUB_USERNAME}/repos",
            headers=GH_HEADERS,
            params={"per_page": 100, "page": page, "type": "owner"},
        )
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        repos.extend(batch)
        page += 1
    return repos


def get_readme(repo_name):
    r = requests.get(
        f"https://api.github.com/repos/{GITHUB_USERNAME}/{repo_name}/readme",
        headers={**GH_HEADERS, "Accept": "application/vnd.github.raw+json"},
    )
    if r.status_code == 404:
        return ""
    r.raise_for_status()
    return r.text


# ── Claude classification ─────────────────────────────────────────────────────

def classify_repo(repo, readme):
    prompt = f"""Classify this GitHub repository. Return ONLY a JSON object, no markdown, no explanation.

Repository:
- Name: {repo["name"]}
- Description: {repo.get("description") or "none"}
- Primary language: {repo.get("language") or "unknown"}
- Is fork: {repo["fork"]}
- Stars: {repo["stargazers_count"]}
- README (truncated):
{readme[:1500] or "no readme"}

Fields:
- "category": one of {VALID_CATEGORIES}
- "status": one of {VALID_STATUSES}
- "stack": array from {VALID_STACK}

Rules:
- IB IA = school coursework; Competition = hackathon/contest; Tool/Automation = CLI/script/automation
- Web App = has frontend UI; Learning = tutorial/practice; Side Project = everything else
- Status: In Progress if recently active, Shipped if polished/deployed, Paused if stale, Archived if abandoned

Return exactly: {{"category": "...", "status": "...", "stack": [...]}}"""

    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers=ANTHROPIC_HEADERS,
        json={
            "model": "claude-sonnet-4-6",
            "max_tokens": 200,
            "messages": [{"role": "user", "content": prompt}],
        },
    )
    r.raise_for_status()
    text = r.json()["content"][0]["text"].strip()

    try:
        result = json.loads(text)
        category = result.get("category") if result.get("category") in VALID_CATEGORIES else None
        status = result.get("status") if result.get("status") in VALID_STATUSES else None
        stack = [s for s in result.get("stack", []) if s in VALID_STACK] or None
        return category, status, stack
    except (json.JSONDecodeError, KeyError):
        print(f"  Warning: failed to parse Claude response for {repo['name']}: {text}")
        return None, None, None


# ── Notion page helpers ───────────────────────────────────────────────────────

def get_existing_notion_pages():
    pages = {}
    cursor = None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        r = requests.post(
            f"https://api.notion.com/v1/databases/{NOTION_DB_ID}/query",
            headers=NOTION_HEADERS,
            json=body,
        )
        r.raise_for_status()
        data = r.json()
        for page in data["results"]:
            repo_id = page["properties"].get("GitHub Repo ID", {}).get("number")
            if repo_id is not None:
                pages[int(repo_id)] = page["id"]
        if not data.get("has_more"):
            break
        cursor = data["next_cursor"]
    return pages


def build_properties(repo, category=None, status=None, stack=None):
    lang = repo.get("language")
    fallback_stack = LANGUAGE_MAP.get(lang, "Other") if lang else None
    pushed_at = repo.get("pushed_at")
    pushed_date = pushed_at[:10] if pushed_at else None

    props = {
        "Project": {"title": [{"text": {"content": repo["name"]}}]},
        "GitHub Repo ID": {"number": repo["id"]},
        "Repo URL": {"url": repo["html_url"]},
        "Description": {"rich_text": [{"text": {"content": repo.get("description") or ""}}]},
        "Type": {"select": {"name": "Private" if repo["private"] else "Public"}},
    }

    if pushed_date:
        props["Last Pushed"] = {"date": {"start": pushed_date}}

    resolved_stack = stack or ([fallback_stack] if fallback_stack else None)
    if resolved_stack:
        props["Stack"] = {"multi_select": [{"name": s} for s in resolved_stack]}

    if category:
        props["Category"] = {"select": {"name": category}}

    if status:
        props["Status"] = {"select": {"name": status}}

    return props


def create_page(repo, readme):
    print(f"  Classifying: {repo['name']}...")
    category, status, stack = classify_repo(repo, readme)
    print(f"    → category={category}, status={status}, stack={stack}")

    r = requests.post(
        "https://api.notion.com/v1/pages",
        headers=NOTION_HEADERS,
        json={
            "parent": {"database_id": NOTION_DB_ID},
            "properties": build_properties(repo, category, status, stack),
        },
    )
    r.raise_for_status()
    page_id = r.json()["id"]

    if readme.strip():
        set_page_readme(page_id, readme)

    print(f"  Created: {repo['name']}")


def update_page(page_id, repo, readme, reclassify=False):
    category, status, stack = None, None, None

    if reclassify:
        print(f"  Reclassifying: {repo['name']}...")
        category, status, stack = classify_repo(repo, readme)
        print(f"    → category={category}, status={status}, stack={stack}")

    r = requests.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers=NOTION_HEADERS,
        json={"properties": build_properties(repo, category, status, stack)},
    )
    r.raise_for_status()

    set_page_readme(page_id, readme)
    print(f"  Updated: {repo['name']}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    mode = "full (with reclassification)" if RECLASSIFY else "mechanical"
    print(f"Starting sync — mode: {mode}")

    print("Fetching GitHub repos...")
    repos = get_all_repos()
    print(f"Found {len(repos)} repos")

    print("Fetching existing Notion entries...")
    existing = get_existing_notion_pages()
    print(f"Found {len(existing)} existing entries")

    for repo in repos:
        readme = get_readme(repo["name"])
        if repo["id"] in existing:
            update_page(existing[repo["id"]], repo, readme, reclassify=RECLASSIFY)
        else:
            create_page(repo, readme)

    print("Done.")


if __name__ == "__main__":
    main()
