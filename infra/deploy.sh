#!/usr/bin/env bash
# Publish the map (index.html + git-tracked data/) to the S3 bucket behind CloudFront, then
# invalidate the CloudFront cache. Locally uses the gb-grid-map profile; in CI, whatever
# credentials the environment provides.
#
# CloudFront only compresses responses of 1 KB to 10 MB with a listed Content-Type, so:
#   - .geojson is served as application/json (application/geo+json isn't on the list)
#   - .json/.geojson files over 10 MB are uploaded pre-gzipped with Content-Encoding: gzip
set -euo pipefail
cd "$(dirname "$0")/.."

export AWS_REGION=eu-north-1
if [ -z "${GITHUB_ACTIONS:-}" ] && [ -z "${AWS_ACCESS_KEY_ID:-}" ]; then
  export AWS_PROFILE="${AWS_PROFILE:-gb-grid-map}"
fi
STACK=gb-grid-map-hosting
CACHE="public, max-age=3600"
BIG=10000000

out() {
  aws cloudformation describe-stacks --stack-name "$STACK" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}
BUCKET=$(out BucketName)
DIST=$(out DistributionId)

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

# Stage tracked files only (data/raw/ etc. are git-ignored), keeping mtimes so sync skips unchanged files.
big=()
while IFS= read -r -d '' f; do
  mkdir -p "$STAGE/$(dirname "$f")"
  size=$(wc -c <"$f" | tr -d ' ')
  if [ "$size" -gt "$BIG" ] && [[ "$f" == *.json || "$f" == *.geojson ]]; then
    gzip -9 -n -c "$f" >"$STAGE/$f"
    touch -r "$f" "$STAGE/$f"
    big+=("$f")
  else
    cp -p "$f" "$STAGE/$f"
  fi
done < <(git ls-files -z -- index.html data)

excl=()
for f in ${big[@]+"${big[@]}"}; do excl+=(--exclude "$f"); done

echo "Uploading to s3://$BUCKET (${#big[@]} pre-gzipped files)"
# Everything except .geojson, with the CLI's own content types.
aws s3 sync "$STAGE" "s3://$BUCKET" --delete --no-progress --cache-control "$CACHE" \
  --exclude "*.geojson" ${excl[@]+"${excl[@]}"}
# .geojson as application/json.
aws s3 sync "$STAGE" "s3://$BUCKET" --delete --no-progress --cache-control "$CACHE" \
  --content-type application/json --exclude "*" --include "*.geojson" ${excl[@]+"${excl[@]}"}
# Large files, pre-gzipped.
for f in ${big[@]+"${big[@]}"}; do
  aws s3 cp "$STAGE/$f" "s3://$BUCKET/$f" --no-progress --cache-control "$CACHE" \
    --content-type application/json --content-encoding gzip
done

id=$(aws cloudfront create-invalidation --distribution-id "$DIST" --paths "/*" \
  --query Invalidation.Id --output text)
echo "Invalidation $id started; site: $(out SiteUrl)"
