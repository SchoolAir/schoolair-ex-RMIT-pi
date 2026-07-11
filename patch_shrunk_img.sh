#!/usr/bin/env bash
# Fix a pishrink'd image whose partition was shrunk but superblock wasn't updated.
#
# pishrink shrinks the partition table entry but can silently skip the
# resize2fs step (e.g. when the filesystem was dirty), leaving the superblock
# claiming a much larger size than the actual partition.  This script patches
# the superblock to match, then runs e2fsck to repair any other inconsistencies.
#
# Usage:  bash patch_shrunk_img.sh <image.img>

set -euo pipefail

BOLD='\033[1m'; GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'
ok()  { echo -e "  ${GREEN}✓${NC}  $*"; }
die() { echo -e "\n${RED}${BOLD}Error:${NC} $*" >&2; exit 1; }

IMG="${1:-}"
[ -n "$IMG" ] || die "Usage: $0 <image.img>"
[ -f "$IMG" ] || die "File not found: $IMG"

echo -e "${BOLD}━━━ SchoolAir: patch shrunk image ━━━${NC}"
echo "  Image: $IMG"

# Mount the image so the partitions appear as block devices
LOOP=$(sudo losetup -fP --show "$IMG")
ok "Mounted as ${LOOP}  (rootfs → ${LOOP}p2)"

cleanup() { sudo losetup -d "$LOOP" 2>/dev/null || true; }
trap cleanup EXIT

# Derive the correct block count from the actual partition size
# blockdev --getsz returns 512-byte sectors; ext4 uses 4 KB blocks
BLOCKS=$(( $(sudo blockdev --getsz "${LOOP}p2") / 8 ))
ok "Correct block count: ${BLOCKS}"

# Patch the primary superblock
echo "set_super_value blocks_count ${BLOCKS}" | sudo debugfs -w "${LOOP}p2" 2>/dev/null
ok "Superblock blocks_count set to ${BLOCKS}"

# Repair the filesystem (also updates backup superblocks to match)
sudo e2fsck -fy "${LOOP}p2"
ok "Filesystem check and repair complete"

echo
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  ${GREEN}${BOLD}Done.${NC}  $IMG is ready to flash."
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
