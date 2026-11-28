#!/usr/bin/env bash
# Back up models/ (about 5.5 GB, too large for GitHub) to a private Hugging Face model repo.
# The upload is resumable: run it again after an interruption, or after training a new model,
# and only what is missing is sent.
#
# Once, in your own terminal (the token needs write access, from huggingface.co/settings/tokens):
#   uv run hf auth login
# Then:
#   scripts/sync_models.sh                      # repo <your username>/fyp-guardrail-models
#   scripts/sync_models.sh someone/other-name   # or a repo of your choice
# Restore on another machine:
#   uv run hf download <repo> --local-dir models
set -euo pipefail
cd "$(dirname "$0")/.."

user=$(uv run python -c "from huggingface_hub import whoami; print(whoami()['name'])" 2>/dev/null) || {
  echo "Not logged in to Hugging Face: run  uv run hf auth login  first." >&2
  exit 1
}
repo="${1:-$user/fyp-guardrail-models}"
echo "Uploading models/ to $repo (private)"
uv run hf upload "$repo" models --repo-type model --private
echo "Done: https://huggingface.co/$repo"
