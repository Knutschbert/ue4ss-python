#!/usr/bin/env bash
# Builds and runs core/tests/*_test.cpp with clang++ (or $CXX). No cmake needed.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cxx="${CXX:-clang++}"
out="$root/build-host"
mkdir -p "$out"

status=0
for src in "$root"/core/tests/*_test.cpp; do
    name="$(basename "$src" .cpp)"
    "$cxx" -std=c++20 -pthread -Wall -Wextra -Werror -I"$root/core/include" -I"$root/core/tests" "$src" -o "$out/$name"
    if "$out/$name"; then
        echo "PASS $name"
    else
        echo "FAIL $name"
        status=1
    fi
done
exit $status
