Branch protection checklist (apply via GitHub settings):

1) Protect the `main` branch:
   - Require a pull request before merging.
   - Dismiss stale pull request approvals when new commits are pushed.
   - Require status checks to pass before merging (select: `CI` workflow).
   - Require review from Code Owners.
   - Restrict who can push to the branch (allow administrators or specific team accounts).

2) Require linear history (optional): enable "Require linear history" to enforce rebase-only merges.

3) Enable administrators override only if needed (recommended: keep strict).

Note: These settings must be applied through the repository's Settings -> Branches -> Branch protection rules in GitHub.
