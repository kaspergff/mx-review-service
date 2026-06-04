import subprocess
from pathlib import Path


def find_mpr(repo_path: str) -> str:
    mprs = list(Path(repo_path).glob("*.mpr"))
    if not mprs:
        raise FileNotFoundError(f"No .mpr found in {repo_path}")
    return str(mprs[0])


def clone_repo(app_id: str, target: str, git_base_url: str, mx_pat: str) -> None:
    repo_url = f"{git_base_url}/{app_id}.git"

    args = ["git", "clone", "--depth", "50", "--no-single-branch"]
    if git_base_url.startswith("https://"):
        # Pass PAT via git config for HTTPS URLs
        args += ["-c", f"http.{git_base_url}/.extraHeader=Authorization: Basic pat:{mx_pat}"]
    args += [repo_url, target]

    subprocess.run(args, check=True, capture_output=True, timeout=120)
