#!/bin/bash
set -e

# Usage:
#   ./reconstruct.sh [coil_out]
#   coil_out = "yes" → export individual coil images as coil_0.png, coil_1.png, …

coil_out="${1:-no}"

IN=/work/bart           # bart.cfl + bart.hdr
OUT=/work/RSSrecon.png
TMP=/tmp/bart_recon
mkdir -p "$TMP" "$(dirname "$OUT")"

# 1) Compute bitmasks
mask_xy=$(bart bitmask 0 1)   # dims 0+1 → frequency & phase
mask_c=$(bart bitmask 3)      # dim 3   → coils

# 2) Inverse FFT (auto-shifted) on freq & phase
bart fft -u -i $mask_xy "$IN" "$TMP/img_coils"


# 3) Root‑sum‑of‑squares across coils
bart rss $mask_c "$TMP/img_coils" "$TMP/img_rss"

# 4) Remove dummy slice axis (dim 2 = 1)
bart squeeze "$TMP/img_rss" "$TMP/img"

# 5) Export exactly one magnitude image
bart toimg "$TMP/img" "$OUT"

echo " Done — RSS image: $OUT"
    
# Optional: export each coil image
if [ "$coil_out" = "yes" ]; then
  echo "→ Exporting individual coil images…"
    bart toimg  "$TMP/img_coils" "/work/img_coils" 
fi
