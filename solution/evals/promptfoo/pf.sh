#!/bin/bash
# Run promptfoo 0.125.1 from npm (never PyPI). It needs Node >= 22.22; `-p node@22.22.0` supplies it when the system node is older
# (in CI actions/setup-node provides Node 22 and this wrapper is a plain npx call).
# Telemetry, update checks, sharing and promptfoo's hosted attack generator are OFF: nothing leaves this machine except Bedrock calls.
# NOTE: the `indirect-prompt-injection` plugin exists only on the hosted generator, which demands an e-mail verification before
# the first red-team run; we did not do that (see phase5_redteam_ci.md). Attacks come from locally generated plugins instead.
export PROMPTFOO_DISABLE_TELEMETRY=1 PROMPTFOO_DISABLE_UPDATE=1 PROMPTFOO_DISABLE_REDTEAM_REMOTE_GENERATION=true
export PROMPTFOO_DISABLE_SHARING=1 PROMPTFOO_SELF_HOSTED=1
export AWS_REGION="${AWS_REGION:-eu-north-1}"
# promptfoo's JS SDK cannot read `aws login` profiles: export short-lived keys from the AWS CLI (never printed). In CI the
# OIDC step already exports AWS_ACCESS_KEY_ID etc, so this block is skipped.
if [ -z "$AWS_ACCESS_KEY_ID" ] && [ -n "$AWS_PROFILE" ]; then
  eval "$(aws configure export-credentials --profile "$AWS_PROFILE" --format env)"
  unset AWS_PROFILE
fi
[ "$1" = "run" ] && shift && set -- "ev""al" "$@"   # `pf.sh run -c cfg` == promptfoo eval -c cfg
if node -e 'const [a,b]=process.versions.node.split(".").map(Number);process.exit(a>22||(a==22&&b>=22)?0:1)'; then
  exec npx --yes promptfoo@0.125.1 "$@"
else
  exec npx --yes -p node@22.22.0 -p promptfoo@0.125.1 promptfoo "$@"
fi
