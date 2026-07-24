#!/usr/bin/env bash
set -euo pipefail

TRIVY_VERSION=0.72.0
TRIVY_SHA256=bbb64b9695866ce4a7a8f5c9592002c5961cab378577fa3f8a040df362b9b2ea
GITLEAKS_VERSION=8.30.1
GITLEAKS_SHA256=551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb

if [[ $# -lt 2 ]]; then
    echo "usage: $0 OUTPUT_DIR TOOL [TOOL...]" >&2
    exit 2
fi

output_dir=$1
shift
mkdir -p "$output_dir"
work_dir=$(mktemp -d)
trap 'rm -rf "$work_dir"' EXIT

install_trivy() {
    local archive="trivy_${TRIVY_VERSION}_Linux-64bit.tar.gz"
    curl --fail --silent --show-error --location \
        "https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/${archive}" \
        --output "$work_dir/$archive"
    printf '%s  %s\n' "$TRIVY_SHA256" "$work_dir/$archive" | sha256sum --check --status
    tar -xzf "$work_dir/$archive" -C "$work_dir" trivy
    install -m 0755 "$work_dir/trivy" "$output_dir/trivy"
    "$output_dir/trivy" --version | sed -n '1p'
}

install_gitleaks() {
    local archive="gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz"
    curl --fail --silent --show-error --location \
        "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/${archive}" \
        --output "$work_dir/$archive"
    printf '%s  %s\n' "$GITLEAKS_SHA256" "$work_dir/$archive" | sha256sum --check --status
    tar -xzf "$work_dir/$archive" -C "$work_dir" gitleaks
    install -m 0755 "$work_dir/gitleaks" "$output_dir/gitleaks"
    "$output_dir/gitleaks" version
}

for tool in "$@"; do
    case "$tool" in
        trivy) install_trivy ;;
        gitleaks) install_gitleaks ;;
        *) echo "unsupported security tool: $tool" >&2; exit 2 ;;
    esac
done
