# Contributing to FORGE

Thanks for your interest! This repository uses a strict PR-based workflow to ensure code quality and safety.

Branching and PR rules
- The primary branch is `main`.
- Create feature branches named like `feature/xyz` or `fix/issue-123`.
- Open a Pull Request targeting `main` when your change is ready.
- Do not push directly to `main`.

Reviews and approvals
- All PRs must be reviewed and approved by @bigknoxy (see CODEOWNERS).
- The repository is configured to require at least one approving review and passing CI before merge.

Continuous integration
- Each PR triggers the CI workflow (.github/workflows/ci.yml) which runs pytest.
- Ensure your branch passes the CI tests before requesting a review.

Pre-commit and formatting
- Please run tests locally: `pytest -q`.
- Follow existing code style and add tests for new functionality.

Security and secrets
- Never commit secrets or credentials. Use `.env` locally (never commit it).
- Use `.env.example` as a template for environment variables.

Thank you!
