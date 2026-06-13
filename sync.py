import os
import requests

GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
NOTION_TOKEN = os.environ["NOTION_TOKEN"]
GITHUB_USERNAME = os.environ.get("GITHUB_USERNAME", "nitrimandylis")

# Coding Projects database ID
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

# Map GitHub language → Notion Stack multi-select options
LANGUAGE_MAP = {
    "Python": "Python",
    "JavaScript": "JavaScript",
    "TypeScript": "TypeScript",
    "Swift": "Swift",
    "HTML": "HTML/CSS",
    "CSS": "HTML/CSS",
    "React": "React",
}


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


def get_existing_notion_pages():
    """Returns {github_repo_id: notion_page_id}"""
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
            props = page["properties"]
            repo_id = props.get("GitHub Repo ID", {}).get("number")
            if repo_id is not None:
                pages[int(repo_id)] = page["id"]
        if not data.get("has_more"):
            break
        cursor = data["next_cursor"]
    return pages


def build_properties(repo):
    lang = repo.get("language")
    stack_name = LANGUAGE_MAP.get(lang, "Other") if lang else None

    pushed_at = repo.get("pushed_at")
    # GitHub returns ISO format like 2024-01-01T00:00:00Z — strip to date
    pushed_date = pushed_at[:10] if pushed_at else None

    props = {
        "Project": {
            "title": [{"text": {"content": repo["name"]}}]
        },
        "GitHub Repo ID": {
            "number": repo["id"]
        },
        "Repo URL": {
            "url": repo["html_url"]
        },
        "Description": {
            "rich_text": [{"text": {"content": repo.get("description") or ""}}]
        },
        "Type": {
            "select": {"name": "Private" if repo["private"] else "Public"}
        },
    }

    if stack_name:
        props["Stack"] = {"multi_select": [{"name": stack_name}]}

    if pushed_date:
        props["Last Pushed"] = {"date": {"start": pushed_date}}

    return props


def create_page(repo):
    r = requests.post(
        "https://api.notion.com/v1/pages",
        headers=NOTION_HEADERS,
        json={
            "parent": {"database_id": NOTION_DB_ID},
            "properties": build_properties(repo),
        },
    )
    r.raise_for_status()
    print(f"  Created: {repo['name']}")


def update_page(page_id, repo):
    r = requests.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers=NOTION_HEADERS,
        json={"properties": build_properties(repo)},
    )
    r.raise_for_status()
    print(f"  Updated: {repo['name']}")


def main():
    print("Fetching GitHub repos...")
    repos = get_all_repos()
    print(f"Found {len(repos)} repos")

    print("Fetching existing Notion entries...")
    existing = get_existing_notion_pages()
    print(f"Found {len(existing)} existing entries")

    for repo in repos:
        if repo["id"] in existing:
            update_page(existing[repo["id"]], repo)
        else:
            create_page(repo)

    print("Done.")


if __name__ == "__main__":
    main()
