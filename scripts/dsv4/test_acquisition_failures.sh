#!/usr/bin/env bash

set -euo pipefail

readonly script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
readonly verifier="$script_dir/acquire_ud_q3_k_xl.sh"
readonly first_split="DeepSeek-V4-Flash-UD-Q3_K_XL-00001-of-00004.gguf"
readonly first_split_size=5256864
fixture_root=$(mktemp -d)
trap 'rm -rf -- "$fixture_root"' EXIT

expect_failure() {
    local name=$1
    local pattern=$2
    local directory=$3
    local output
    if output=$("$verifier" --verify-only "$directory" 2>&1); then
        echo "error: $name unexpectedly succeeded" >&2
        exit 1
    fi
    grep -F -- "$pattern" <<< "$output" >/dev/null || {
        echo "error: $name did not report '$pattern': $output" >&2
        exit 1
    }
    echo "expected_failure=$name"
}

mkdir "$fixture_root/missing" "$fixture_root/wrong-size" "$fixture_root/wrong-sha"
expect_failure missing "is missing" "$fixture_root/missing"

truncate -s 1 "$fixture_root/wrong-size/$first_split"
expect_failure wrong-size "has size 1, expected $first_split_size" "$fixture_root/wrong-size"

truncate -s "$first_split_size" "$fixture_root/wrong-sha/$first_split"
expect_failure wrong-sha "has SHA-256" "$fixture_root/wrong-sha"

echo "acquisition_failure_tests=pass"
