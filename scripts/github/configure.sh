#!/usr/bin/env bash
# Every GitHub setting this repository relies on, applied from files in the tree.
#
#   scripts/github/configure.sh apply [owner/repo]   apply every setting (idempotent)
#   scripts/github/configure.sh check [owner/repo]   read every setting back; exit 1 on
#                                                    any difference or anything unreadable
#
# `apply` needs the repository's Administration permission, so the maintainer runs it
# with their own `gh` login, not an agent token:
#
#   env -u GH_TOKEN -u GITHUB_TOKEN scripts/github/configure.sh apply
#   env -u GH_TOKEN -u GITHUB_TOKEN scripts/github/configure.sh check
#
# `check` also runs with a narrower token and says what that token cannot read. Two
# things only an administrator can see: the Actions settings, and the ruleset's bypass
# list. Without them, `check` requires GitHub's own answer to "can this caller bypass the
# ruleset" to be `never`, and still exits 1 for the unreadable Actions settings.
#
# Needs bash 3.2 or later, gh and jq. A fresh repository gets identical settings by
# running `apply` against it.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RULESET_FILE="$HERE/ruleset-main.json"
MODE="${1:-}"
REPO="${2:-glosswork/glosswork}"

# ---------------------------------------------------------------- the settings

MERGE_SETTINGS='{
  "allow_merge_commit": true,
  "allow_squash_merge": false,
  "allow_rebase_merge": false,
  "allow_auto_merge": false,
  "delete_branch_on_merge": true
}'

ACTIONS_PERMISSIONS='{
  "enabled": true,
  "allowed_actions": "selected",
  "sha_pinning_required": true
}'

SELECTED_ACTIONS='{
  "github_owned_allowed": true,
  "verified_allowed": false,
  "patterns_allowed": ["astral-sh/setup-uv@*"]
}'

WORKFLOW_TOKEN='{
  "default_workflow_permissions": "read",
  "can_approve_pull_request_reviews": false
}'

LABEL='{
  "name": "backlog",
  "color": "c5def5",
  "description": "An observation, recorded. Not a commitment."
}'

# ---------------------------------------------------------------- helpers

# `covers`: every key the wanted document sets has the same value in what GitHub
# returned. Extra keys GitHub adds (ids, links, optional parameters) are ignored. Arrays
# must have the same length and every wanted element must be covered by one returned
# element, in any order.
# A jq program, not shell: the $ signs are jq's.
# shellcheck disable=SC2016
JQ_COVERS='
def covers($want):
  . as $have
  | if ($want | type) == "object" then
      ($have | type) == "object"
      and all($want | keys[]; . as $k | ($have | has($k)) and ($have[$k] | covers($want[$k])))
    elif ($want | type) == "array" then
      ($have | type) == "array" and ($have | length) == ($want | length)
      and all($want[]; . as $w | any($have[]; covers($w)))
    else $have == $want end;
'

FAILED=0

say() { printf '%s\n' "$*"; }
bad() { printf 'DIFFERS  %s\n' "$*"; FAILED=1; }
good() { printf 'ok       %s\n' "$*"; }

# get PATH: on success sets BODY to the response and returns 0. On failure sets BODY to
# nothing and HTTP_STATUS to the status gh reported (or "error"), and returns 1. It sets
# globals rather than printing, so a caller keeps HTTP_STATUS (a $(...) would lose it).
BODY=""
HTTP_STATUS=""
get() {
  local err
  err="$(mktemp)"
  if BODY="$(gh api "$1" 2>"$err")"; then
    HTTP_STATUS=200
    rm -f "$err"
    return 0
  fi
  BODY=""
  HTTP_STATUS="$(sed -n -E 's/.*\(HTTP ([0-9]{3})\).*/\1/p' "$err" | head -1)"
  [ -n "$HTTP_STATUS" ] || HTTP_STATUS=error
  rm -f "$err"
  return 1
}

send() {
  # send METHOD PATH BODY
  printf '%s' "$3" | gh api --method "$1" "$2" --input - >/dev/null
}

compare() {
  # compare LABEL WANT HAVE
  if jq -e -n --argjson want "$2" --argjson have "$3" "$JQ_COVERS"' $have | covers($want)' >/dev/null; then
    good "$1"
  else
    bad "$1"
    jq -n --argjson want "$2" --argjson have "$3" \
      '[$want | to_entries[] | select(.value != $have[.key]) | {setting: .key, want: .value, have: $have[.key]}]'
  fi
}

ruleset_id() {
  # The id of the ruleset named "main", or nothing.
  get "repos/$REPO/rulesets" || return 0
  jq -r '.[] | select(.name == "main") | .id' <<<"$BODY" | head -1
}

# ---------------------------------------------------------------- apply

apply() {
  say "Applying settings to $REPO"
  send PATCH "repos/$REPO" "$MERGE_SETTINGS"
  good "merge methods"
  send PUT "repos/$REPO/actions/permissions" "$ACTIONS_PERMISSIONS"
  good "Actions enabled, selected actions only, SHA pinning required"
  send PUT "repos/$REPO/actions/permissions/selected-actions" "$SELECTED_ACTIONS"
  good "which actions"
  send PUT "repos/$REPO/actions/permissions/workflow" "$WORKFLOW_TOKEN"
  good "workflow token read-only"

  if get "repos/$REPO/labels/backlog"; then
    send PATCH "repos/$REPO/labels/backlog" "$LABEL"
  else
    send POST "repos/$REPO/labels" "$LABEL"
  fi
  good "label backlog"

  local id
  id="$(ruleset_id)"
  if [ -n "$id" ]; then
    send PUT "repos/$REPO/rulesets/$id" "$(cat "$RULESET_FILE")"
  else
    send POST "repos/$REPO/rulesets" "$(cat "$RULESET_FILE")"
  fi
  good "ruleset main"
  say "Applied. Run '$0 check $REPO' to read it all back."
}

# ---------------------------------------------------------------- check

check_simple() {
  # check_simple LABEL PATH WANT
  if get "$2"; then
    compare "$1" "$3" "$BODY"
  else
    bad "$1: unreadable with this token (HTTP $HTTP_STATUS)"
  fi
}

check_ruleset() {
  local want listing id have targets
  want="$(cat "$RULESET_FILE")"
  if ! get "repos/$REPO/rulesets"; then
    bad "ruleset: rulesets unreadable (HTTP $HTTP_STATUS)"
    return
  fi
  listing="$BODY"
  id="$(jq -r '.[] | select(.name == "main") | .id' <<<"$listing" | head -1)"
  if [ -z "$id" ]; then
    bad "ruleset main: missing"
    return
  fi
  if ! get "repos/$REPO/rulesets/$id"; then
    bad "ruleset main: unreadable (HTTP $HTTP_STATUS)"
    return
  fi
  have="$BODY"

  # Everything the file sets except the bypass list, which only a caller with write
  # access to the ruleset can read.
  compare "ruleset main (id $id)" \
    "$(jq 'del(.bypass_actors)' <<<"$want")" \
    "$(jq 'del(.bypass_actors)' <<<"$have")"

  if jq -e 'has("bypass_actors")' <<<"$have" >/dev/null; then
    compare "ruleset main bypass list" \
      "$(jq '{bypass_actors}' <<<"$want")" "$(jq '{bypass_actors}' <<<"$have")"
  else
    say "note     bypass list unreadable with this token"
    local bypass
    bypass="$(jq -r '.current_user_can_bypass // "unset"' <<<"$have")"
    if [ "$bypass" = never ]; then
      good "this caller can never bypass the ruleset"
    else
      bad "this caller can bypass the ruleset: current_user_can_bypass is $bypass"
    fi
  fi

  # No second ruleset on the default branch, and no classic protection on top, either of
  # which could require other checks or allow other merge methods.
  targets=0
  for other in $(jq -r '.[] | select(.target == "branch") | .id' <<<"$listing"); do
    get "repos/$REPO/rulesets/$other" || { bad "ruleset $other: unreadable (HTTP $HTTP_STATUS)"; continue; }
    if jq -e '.conditions.ref_name.include | any(. == "~DEFAULT_BRANCH" or . == "refs/heads/main" or . == "~ALL")' <<<"$BODY" >/dev/null; then
      targets=$((targets + 1))
    fi
  done
  if [ "$targets" -eq 1 ]; then
    good "one ruleset targets the default branch"
  else
    bad "$targets rulesets target the default branch; want 1"
  fi

  if get "repos/$REPO/branches/main/protection"; then
    bad "classic branch protection is set on main; the ruleset is meant to be the only protection"
  elif [ "$HTTP_STATUS" = 404 ]; then
    good "no classic branch protection on main"
  else
    say "note     classic branch protection unreadable with this token (HTTP $HTTP_STATUS)"
  fi
}

check() {
  say "Checking settings on $REPO"
  check_simple "merge methods" "repos/$REPO" "$MERGE_SETTINGS"
  check_simple "Actions permissions" "repos/$REPO/actions/permissions" "$ACTIONS_PERMISSIONS"
  check_simple "which actions" "repos/$REPO/actions/permissions/selected-actions" "$SELECTED_ACTIONS"
  check_simple "workflow token" "repos/$REPO/actions/permissions/workflow" "$WORKFLOW_TOKEN"
  if get "repos/$REPO/labels/backlog"; then
    compare "label backlog" "$(jq '{name, description}' <<<"$LABEL")" "$BODY"
  else
    bad "label backlog: missing (HTTP $HTTP_STATUS)"
  fi
  check_ruleset
  if [ "$FAILED" -eq 0 ]; then
    say "All settings match."
  else
    say "Some settings differ or could not be read."
  fi
  return "$FAILED"
}

case "$MODE" in
  apply) apply ;;
  check) check ;;
  *)
    say "usage: $0 apply|check [owner/repo]" >&2
    exit 2
    ;;
esac
