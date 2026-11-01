#!/bin/sh
set -eu

base="${1:-$(pwd)/meituan}"
skill="$base/skill"
package="$base/runtime/package"
state="$base/runtime/state"

test -f "$skill/paotui.js" || { echo "缺少官方 paotui.js" >&2; exit 2; }
test -f "$skill/config.json" || { echo "缺少官方 config.json" >&2; exit 2; }
test -f "$package/dist/index.js" || { echo "缺少 pt-passport" >&2; exit 2; }
test -f "$package/node_modules/@sec/cliguard/index.js" || { echo "缺少 pt-passport 运行依赖" >&2; exit 2; }

find "$skill" "$package" -type d -exec chmod 0755 {} +
find "$skill" "$package" -type f -exec chmod 0644 {} +
chown -R root:root "$skill" "$package"
install -d -o 10001 -g 10001 -m 0700 "$state" "$state/accounts" "$state/tmp"
chown -R 10001:10001 "$state"
chmod -R go-rwx "$state"

echo "美团官方程序已设为只读；每位 TROPOS 用户的授权状态将写入私有持久化目录。"
