#!/usr/bin/env bash

set -euo pipefail

readonly REPOSITORY="unsloth/DeepSeek-V4-Flash-GGUF"
readonly REVISION="85ce4196ab6e82852e25dfec2b7e2beaae56f5f1"
readonly VARIANT="UD-Q3_K_XL"
readonly RESERVE_BYTES=$((55 * 1024 * 1024 * 1024))
readonly ARTIFACT_BYTES=129448242976

readonly -a FILES=(
    "DeepSeek-V4-Flash-UD-Q3_K_XL-00001-of-00004.gguf|5256864|951458825be77e285141adb8a71bcb72abf26ab33a39bbdead9eb7d73ef7b396"
    "DeepSeek-V4-Flash-UD-Q3_K_XL-00002-of-00004.gguf|49350774208|63c873e288a2ab222bf902cfda53105cdf37fd714f0aa939070f8106fdda3242"
    "DeepSeek-V4-Flash-UD-Q3_K_XL-00003-of-00004.gguf|49189072672|9c2c9878beb485d3553fe272edcc13f5959c31ec371f5df947fa0514b83cd4dc"
    "DeepSeek-V4-Flash-UD-Q3_K_XL-00004-of-00004.gguf|30903139232|2deb9faaa22707d4af983955f517f961d7e939e169a11ec129066186918a13ea"
)

usage() {
    echo "usage: $0 [--verify-only] MODEL_DIRECTORY" >&2
}

fail() {
    echo "error: $*" >&2
    exit 1
}

file_size() {
    stat --format='%s' "$1"
}

verify_file() {
    local path=$1
    local expected_size=$2
    local expected_sha256=$3
    local actual_size
    local actual_sha256

    actual_size=$(file_size "$path")
    [[ "$actual_size" == "$expected_size" ]] || fail "$path has size $actual_size, expected $expected_size"
    actual_sha256=$(sha256sum "$path" | cut -d ' ' -f 1)
    [[ "$actual_sha256" == "$expected_sha256" ]] || fail "$path has SHA-256 $actual_sha256, expected $expected_sha256"
}

verify_only=false
if [[ ${1:-} == --verify-only ]]; then
    verify_only=true
    shift
fi
[[ $# -eq 1 ]] || { usage; exit 2; }
command -v sha256sum >/dev/null || fail "sha256sum is required"

readonly destination=$1
if [[ $verify_only == true ]]; then
    [[ -d $destination ]] || fail "$destination is not a directory"
    for entry in "${FILES[@]}"; do
        IFS='|' read -r name expected_size expected_sha256 <<< "$entry"
        final_path="$destination/$name"
        [[ -f $final_path ]] || fail "$final_path is missing"
        verify_file "$final_path" "$expected_size" "$expected_sha256"
        echo "verified=$name"
    done
    echo "verified_artifact_bytes=$ARTIFACT_BYTES"
    exit 0
fi

if command -v aria2c >/dev/null; then
    readonly downloader=aria2c
elif command -v curl >/dev/null; then
    readonly downloader=curl
else
    fail "aria2c or curl is required"
fi

mkdir -p "$destination"

existing_bytes=0
for entry in "${FILES[@]}"; do
    IFS='|' read -r name expected_size expected_sha256 <<< "$entry"
    final_path="$destination/$name"
    partial_path="$final_path.part"

    if [[ -e "$final_path" ]]; then
        verify_file "$final_path" "$expected_size" "$expected_sha256"
        existing_bytes=$((existing_bytes + expected_size))
    elif [[ -e "$partial_path" ]]; then
        partial_size=$(file_size "$partial_path")
        (( partial_size <= expected_size )) || fail "$partial_path exceeds its expected size"
        partial_allocated=$(du --block-size=1 "$partial_path" | cut -f 1)
        (( partial_allocated <= expected_size )) || partial_allocated=$expected_size
        existing_bytes=$((existing_bytes + partial_allocated))
    fi
done

available_bytes=$(df --block-size=1 --output=avail "$destination" | tail -n 1 | tr -d ' ')
required_bytes=$((ARTIFACT_BYTES - existing_bytes + RESERVE_BYTES))
(( available_bytes >= required_bytes )) || fail "available bytes $available_bytes are below required bytes $required_bytes"

echo "repository=$REPOSITORY"
echo "revision=$REVISION"
echo "variant=$VARIANT"
echo "artifact_bytes=$ARTIFACT_BYTES"
echo "existing_bytes=$existing_bytes"
echo "available_bytes=$available_bytes"
echo "required_bytes=$required_bytes"
echo "reserve_bytes=$RESERVE_BYTES"
echo "downloader=$downloader"

for entry in "${FILES[@]}"; do
    IFS='|' read -r name expected_size expected_sha256 <<< "$entry"
    final_path="$destination/$name"
    partial_path="$final_path.part"
    url="https://huggingface.co/$REPOSITORY/resolve/$REVISION/$VARIANT/$name?download=true"

    if [[ -e "$final_path" ]]; then
        echo "verified=$name"
        continue
    fi

    if [[ "$downloader" == aria2c ]]; then
        aria_attempt=1
        while ! aria2c \
                --allow-overwrite=false \
                --auto-file-renaming=false \
                --connect-timeout=30 \
                --console-log-level=warn \
                --continue=true \
                --dir="$destination" \
                --file-allocation=none \
                --lowest-speed-limit=65536 \
                --max-connection-per-server=16 \
                --max-tries=0 \
                --min-split-size=16M \
                --out="$name.part" \
                --retry-wait=5 \
                --split=16 \
                --summary-interval=60 \
                --timeout=120 \
                "$url"; do
            (( aria_attempt < 32 )) || fail "aria2c exhausted 32 resumable attempts for $name"
            echo "resume=$name attempt=$aria_attempt"
            aria_attempt=$((aria_attempt + 1))
        done
    else
        curl \
            --fail \
            --location \
            --continue-at - \
            --output "$partial_path" \
            --retry 20 \
            --retry-all-errors \
            --retry-delay 5 \
            --speed-limit 1048576 \
            --speed-time 120 \
            "$url"
    fi

    verify_file "$partial_path" "$expected_size" "$expected_sha256"
    mv "$partial_path" "$final_path"
    echo "verified=$name"
done

final_bytes=$(df --block-size=1 --output=avail "$destination" | tail -n 1 | tr -d ' ')
(( final_bytes >= RESERVE_BYTES )) || fail "post-artifact available bytes $final_bytes are below reserve $RESERVE_BYTES"
echo "post_artifact_available_bytes=$final_bytes"
