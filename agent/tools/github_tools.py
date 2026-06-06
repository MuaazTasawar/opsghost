"""
OpsGhost GitHub Tools
Thin async wrappers around PyGitHub and the GitHub REST API.
All functions return (result, error) tuples — never raise.
"""

import os
import base64
import logging
import httpx
from typing import Optional
from github import Github, GithubIntegration, Auth
from github.Repository import Repository
from github.GithubException import GithubException

logger = logging.getLogger(__name__)


# ── GitHub App Authentication ────────────────────────────────────────────────

def _load_private_key() -> str:
    """Loads the GitHub App private key from the path in env."""
    key_path = os.getenv("GITHUB_APP_PRIVATE_KEY_PATH", "private-key.pem")
    with open(key_path, "r") as f:
        return f.read()


def get_installation_token(repo_full_name: str) -> tuple[Optional[str], Optional[str]]:
    """
    Returns a short-lived installation access token for the given repo.
    GitHub Apps authenticate per-installation, not globally.

    Returns:
        (token, error) — error is None on success
    """
    try:
        app_id = int(os.getenv("GITHUB_APP_ID", "0"))
        private_key = _load_private_key()

        auth = Auth.AppAuth(app_id, private_key)
        gi = GithubIntegration(auth=auth)

        owner, repo_name = repo_full_name.split("/")
        installation = gi.get_repo_installation(owner, repo_name)
        token = gi.get_access_token(installation.id).token
        return token, None

    except FileNotFoundError:
        return None, "Private key file not found. Check GITHUB_APP_PRIVATE_KEY_PATH."
    except Exception as e:
        return None, f"Failed to get installation token: {str(e)}"


def get_repo_client(repo_full_name: str) -> tuple[Optional[Repository], Optional[str]]:
    """
    Returns an authenticated PyGitHub Repository object.

    Returns:
        (repo, error)
    """
    token, err = get_installation_token(repo_full_name)
    if err:
        return None, err
    try:
        g = Github(token)
        repo = g.get_repo(repo_full_name)
        return repo, None
    except GithubException as e:
        return None, f"GitHub API error: {e.status} {e.data}"
    except Exception as e:
        return None, f"Unexpected error getting repo client: {str(e)}"


# ── Log Fetching ─────────────────────────────────────────────────────────────

async def fetch_workflow_run_logs(
    repo_full_name: str,
    run_id: int,
    max_chars: int = 12000,
) -> tuple[Optional[str], Optional[str]]:
    """
    Downloads and returns the raw log text for a GitHub Actions workflow run.
    GitHub returns a zip of log files — we extract and concatenate them.

    Returns:
        (log_text, error)
    """
    import zipfile
    import io

    token, err = get_installation_token(repo_full_name)
    if err:
        return None, err

    url = f"https://api.github.com/repos/{repo_full_name}/actions/runs/{run_id}/logs"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
            response = await client.get(url, headers=headers)

        if response.status_code == 404:
            return None, f"Logs not found for run {run_id}. The run may be too old or logs were deleted."
        if response.status_code != 200:
            return None, f"GitHub returned {response.status_code} when fetching logs."

        # GitHub returns a ZIP archive of all log files
        zip_bytes = io.BytesIO(response.content)
        all_text_parts = []

        with zipfile.ZipFile(zip_bytes) as zf:
            # Sort so we read logs in step order
            names = sorted(zf.namelist())
            for name in names:
                if name.endswith(".txt"):
                    with zf.open(name) as log_file:
                        content = log_file.read().decode("utf-8", errors="replace")
                        all_text_parts.append(f"\n=== {name} ===\n{content}")

        combined = "\n".join(all_text_parts)

        # Truncate from the END — failures appear at the bottom
        if len(combined) > max_chars:
            combined = "...[truncated from start]...\n" + combined[-max_chars:]

        return combined, None

    except zipfile.BadZipFile:
        return None, "GitHub returned an invalid ZIP archive for logs."
    except Exception as e:
        return None, f"Error fetching logs: {str(e)}"


# ── Branch & File Operations ─────────────────────────────────────────────────

def create_fix_branch(
    repo: Repository,
    base_sha: str,
    branch_name: str,
) -> tuple[bool, Optional[str]]:
    """
    Creates a new branch off a specific commit SHA.

    Returns:
        (success, error)
    """
    try:
        repo.create_git_ref(
            ref=f"refs/heads/{branch_name}",
            sha=base_sha,
        )
        return True, None
    except GithubException as e:
        if e.status == 422:
            return False, f"Branch '{branch_name}' already exists."
        return False, f"Failed to create branch: {e.status} {e.data}"
    except Exception as e:
        return False, f"Unexpected error creating branch: {str(e)}"


def get_file_contents(
    repo: Repository,
    file_path: str,
    branch: str,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Fetches a file's content and its SHA (needed for updates).

    Returns:
        (content_str, file_sha, error)
    """
    try:
        file_obj = repo.get_contents(file_path, ref=branch)
        # file_obj.content is base64-encoded
        content = base64.b64decode(file_obj.content).decode("utf-8")
        return content, file_obj.sha, None
    except GithubException as e:
        if e.status == 404:
            return None, None, f"File '{file_path}' not found on branch '{branch}'."
        return None, None, f"GitHub error fetching file: {e.status} {e.data}"
    except Exception as e:
        return None, None, f"Error fetching file contents: {str(e)}"


def update_file_on_branch(
    repo: Repository,
    file_path: str,
    new_content: str,
    file_sha: str,
    branch: str,
    commit_message: str,
) -> tuple[bool, Optional[str]]:
    """
    Commits an updated file to a branch.

    Returns:
        (success, error)
    """
    try:
        repo.update_file(
            path=file_path,
            message=commit_message,
            content=new_content,
            sha=file_sha,
            branch=branch,
        )
        return True, None
    except GithubException as e:
        return False, f"Failed to update file '{file_path}': {e.status} {e.data}"
    except Exception as e:
        return False, f"Unexpected error updating file: {str(e)}"


# ── PR Creation ──────────────────────────────────────────────────────────────

def create_pull_request(
    repo: Repository,
    title: str,
    body: str,
    head_branch: str,
    base_branch: str,
) -> tuple[Optional[str], Optional[int], Optional[str]]:
    """
    Opens a pull request from head_branch → base_branch.

    Returns:
        (pr_url, pr_number, error)
    """
    try:
        pr = repo.create_pull(
            title=title,
            body=body,
            head=head_branch,
            base=base_branch,
        )
        return pr.html_url, pr.number, None
    except GithubException as e:
        if e.status == 422:
            return None, None, "PR already exists for this branch, or no commits differ."
        return None, None, f"Failed to create PR: {e.status} {e.data}"
    except Exception as e:
        return None, None, f"Unexpected error creating PR: {str(e)}"


def post_comment_on_run(
    repo: Repository,
    sha: str,
    comment: str,
) -> tuple[bool, Optional[str]]:
    """
    Posts a commit comment on the SHA that triggered the failed run.
    Used as a fallback when OpsGhost can't open a PR but wants to report.

    Returns:
        (success, error)
    """
    try:
        commit = repo.get_commit(sha)
        commit.create_comment(comment)
        return True, None
    except GithubException as e:
        return False, f"Failed to post commit comment: {e.status} {e.data}"
    except Exception as e:
        return False, f"Unexpected error posting comment: {str(e)}"