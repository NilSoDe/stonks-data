#!/usr/bin/env bash
# Publica el informe web del día en la rama gh-pages (GitHub Pages) y comprueba que la página ya responde.
# Uso (desde el workflow):  bash scripts/publish_site.sh us|eu [ruta/signals.json]
# Si la página responde con la versión recién publicada, escribe "url=..." en $GITHUB_OUTPUT y render_mail.py
# pone el botón "Ver informe completo". Si algo falla (Pages sin activar, sin red...), no escribe nada y el
# mail sale como siempre, sin botón: nunca se manda un enlace roto.
set -euo pipefail

kind="$1"
src="${2:-}"
repo="${GITHUB_REPOSITORY:?falta GITHUB_REPOSITORY}"
owner="${repo%%/*}"
base="https://${owner,,}.github.io/${repo#*/}"
site="${RUNNER_TEMP:-/tmp}/stonks-site"

git config user.name "stonks-bot"
git config user.email "stonks-bot@users.noreply.github.com"

for i in 1 2 3 4 5; do
  git worktree remove --force "$site" 2>/dev/null || true
  rm -rf "$site"
  git worktree prune
  if git fetch -q --depth=1 origin gh-pages 2>/dev/null; then
    git worktree add -q --detach "$site" FETCH_HEAD
  else  # primera vez: rama vacía
    git worktree add -q --detach "$site" HEAD
    git -C "$site" checkout -q --orphan gh-pages
    git -C "$site" rm -rfq .
  fi
  out="$(python scripts/publish_site.py "$kind" "$site" "$src")"
  path="$(echo "$out" | sed -n 1p)"
  build="$(echo "$out" | sed -n 2p)"
  git -C "$site" add -A
  if git -C "$site" diff --cached --quiet; then
    break  # ya estaba publicado tal cual
  fi
  git -C "$site" commit -q -m "Informe $path"
  if git -C "$site" push -q origin HEAD:gh-pages; then
    break
  fi
  [ "$i" = 5 ] && { echo "AVISO: no se ha podido publicar en gh-pages"; exit 1; }
  sleep $((i * 5))
done

url="$base/$path"
for _ in $(seq 1 "${PAGES_TRIES:-16}"); do  # GitHub Pages tarda del orden de 1 minuto en servir un push
  if curl -fsSL --max-time 20 "$url" 2>/dev/null | grep -q "content=\"$build\""; then
    echo "Informe web publicado: $url"
    [ -n "${GITHUB_OUTPUT:-}" ] && echo "url=$url" >> "$GITHUB_OUTPUT"
    exit 0
  fi
  sleep 15
done
echo "AVISO: $url no responde con la versión nueva (¿GitHub Pages activado en la rama gh-pages?). El mail sale sin botón."
exit 0
