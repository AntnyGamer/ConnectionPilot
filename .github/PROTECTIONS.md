# Repository protections to enable in GitHub Settings

The workflow files in this repository provide automated checks, but GitHub
**branch rulesets and repository security settings are server-side controls**.
They cannot be enabled by committing a file. An administrator must apply them
in GitHub Settings and confirm they are active.

## Protect main

Go to **Settings → Rules → Rulesets → New branch ruleset**. Name it
`Protect main`, set enforcement to **Active**, and target the default branch.
Use the following protections:

- **Require a pull request before merging**. Because this is a solo-maintained
  repository, zero mandatory human approvals avoids blocking its sole owner.
- **Require status checks to pass**. After the CI workflow has run successfully,
  require **unit-tests** (from `Test ConnectionPilot v1`). Do not set a missing
  or unrun status as required or every merge may be blocked.
- **Block force pushes** and **restrict deletions**.
- **Require conversation resolution before merging**.
- Do not grant an always-on bypass to other users or apps.

CodeQL (`CodeQL Python`) is a separate security scan. Once GitHub has run it
successfully, optionally require this check too. For a solo project, requiring
approvals from another human is impractical unless a collaborator is available.

## Repository security

Go to **Settings → Advanced Security** (or **Code security and analysis**, if
GitHub shows that label). Enable or confirm:

- **Dependency graph** and **Dependabot alerts**.
- **Dependabot security updates** (version-update PRs are configured in
  `.github/dependabot.yml`).
- **Secret scanning** and **push protection**, where available. These are
  important because a browser session accidentally committed to a *public*
  repository must be treated as compromised, even if later deleted.
- **Code scanning**: `.github/workflows/codeql.yml` runs CodeQL for Python.

In **Settings → Actions → General**, keep default workflow-token permissions
read-only; the CodeQL job requests its own `security-events: write` permission.
Only permit necessary third-party Actions, and review Dependabot/CodeQL PRs
before merging. Never store browser cookies or sessions in GitHub Actions
secrets or the repository.

Check **Settings → Rules → Rulesets** afterward and verify `Protect main` is
**Active**. Merely storing this document does not activate branch protection.
