# TruffleHog evaluation

Eligible PRs run TruffleHog 3.99.4 across their Git changes. Secret scans apply to all file types. Credential verification and container networking are disabled. Only candidate counts are reported; raw credential material is neither printed nor uploaded. Scanner failures are advisory and Gitleaks remains the existing gate. Docker is required; the workflow contains the complete invocation.

Run from the repository root with the workspace environment available:

```bash
git diff --stat origin/master...HEAD
```
