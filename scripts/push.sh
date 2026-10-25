#!/usr/bin/env bash
# InboxPilot: safely commit and push B's frontend work to a feature branch.
# Usage:
#   ./scripts/push.sh "feat(ui): build InboxPilot frontend"
#   ./scripts/push.sh                 # automatic dated commit message
#   ./scripts/push.sh --all "chore: update project"  # opt in: stage entire repo
set -Eeuo pipefail

fail() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }
info() { printf '\n==> %s\n' "$*"; }

STAGE_ALL=false
MESSAGE=""
for arg in "$@"; do
  case "$arg" in
    --all) STAGE_ALL=true ;;
    -h|--help)
      printf 'Usage: %s [--all] ["commit message"]\n' "$0"
      printf 'Default: only stage frontend/ and this script, plus optional B test files.\n'
      printf 'Use --all to stage every nonignored project change.\n'
      exit 0 ;;
    -*) fail "Unknown option: $arg" ;;
    *) [[ -z "$MESSAGE" ]] || fail 'Pass one commit message in quotes.'; MESSAGE="$arg" ;;
  esac
done
MESSAGE="${MESSAGE:-feat(ui): update InboxPilot $(date '+%Y-%m-%d %H:%M')}"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null)" \
  || fail 'Put this script inside an existing Git repository (scripts/push.sh).'
cd "$ROOT"

command -v git >/dev/null 2>&1 || fail 'Git is not installed.'
[[ -f .gitignore ]] || fail 'Expected .gitignore; make sure you are in the InboxPilot repository.'
git remote get-url origin >/dev/null 2>&1 || fail 'Git remote "origin" is not set.'

BRANCH="$(git symbolic-ref --quiet --short HEAD 2>/dev/null)" \
  || fail 'Detached HEAD: switch to a branch before committing.'
if [[ "$BRANCH" == main || "$BRANCH" == master ]]; then
  TARGET=feature/agent-ui
  if git show-ref --verify --quiet "refs/heads/$TARGET"; then
    fail "You are on $BRANCH, and $TARGET already exists. Run: git switch $TARGET"
  fi
  info "Protecting the shared $BRANCH branch: creating $TARGET"
  git switch -c "$TARGET"
  BRANCH="$TARGET"
fi

info "Repository: $ROOT"
info "Branch: $BRANCH"

if "$STAGE_ALL"; then
  info 'Staging ALL nonignored project changes (--all requested)'
  git add -A
else
  info 'Staging B-owned frontend code and upload script'
  git add -A -- frontend/ scripts/push.sh
  if [[ -d tests/fixtures ]]; then git add -A -- tests/fixtures/; fi
  # Explicitly add optional agent-oriented test files if they exist.
  for f in tests/test_agent.py tests/test_analyzer.py tests/test_planner.py; do
    if [[ -e "$f" ]]; then git add -A -- "$f"; fi
  done
fi

# gitignore doesn't protect secrets that were already tracked or force-added.
# Block commonly dangerous filenames before creating the commit.
while IFS= read -r -d '' file; do
  case "$file" in
    .env.example|*/.env.example) ;;
    .env|.env.*|*/.env|*/.env.*|*credentials.json|*token.json|*secrets.toml|*.pem|*.p12|*.pfx|*.key|*.sqlite|*.sqlite3|*.db)
      fail "Sensitive file is staged: $file. Unstage it before proceeding." ;;
  esac
done < <(git diff --cached --name-only -z)

if ! git diff --cached --quiet; then
  info 'Files ready for commit:'
  git diff --cached --stat
  git diff --cached --check || fail 'Staged changes contain whitespace errors; fix before committing.'
  git config user.name >/dev/null 2>&1 || fail 'Configure Git author with: git config user.name "Your Name"'
  git config user.email >/dev/null 2>&1 || fail 'Configure Git email with: git config user.email "you@example.com"'
  git commit -m "$MESSAGE"
else
  info 'No new staged changes; will try pushing any existing local commits.'
fi

info "Pushing branch $BRANCH to origin"
if git push -u origin "$BRANCH"; then
  info 'Push succeeded.'
  printf 'Branch URL: %s/tree/%s\n' \
    'https://github.com/CaoLongyang/inboxpilot' "$BRANCH"
  printf 'If appropriate, open a pull request to merge this branch into main.\n'
else
  printf '\nPush failed. Local commit(s) are safe and still on branch %s.\n' "$BRANCH" >&2
  printf 'Possible causes: missing collaborator write access, GitHub authentication, or remote divergence.\n' >&2
  printf 'Ask the repository owner to grant Write access, or push to a fork and create a PR.\n' >&2
  exit 1
fi
