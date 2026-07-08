#!/usr/bin/env bash
# ============================================================================
# run_night.sh — повний нічний цикл для розділів з config.json -> run.chapters:
#
#   підготовка (детекція -> переклад -> ремонт)  [локально, GPU]
#   -> бекап translations.json
#   -> підстановки з пам'яті перекладів (TM)
#   -> АІ-ревю (ТІЛЬКИ правки JSON; провайдер з config.json -> review.provider:
#      "codex" | "claude" | "none"; падіння АІ НЕ зупиняє ніч)
#   -> єдиний рендер + TTS + енкод
#   -> цикл якості: verify --fix -> дорендер (макс. review.max_fix_loops раз)
#   -> поповнення пам'яті перекладів
#   -> підсумкова таблиця
#
# Можна стопати (Ctrl+C) у будь-який момент і перезапускати тією ж командою:
# завершені стадії пропускаються через state-файл, кеші рендера/озвучки
# докручують решту. State живе в temp/.night_state_<chapters> і видаляється
# після повного успіху.
#
# ВАЖЛИВО: не включай у run.chapters розділи, які ВЖЕ пройшли ревю раніше —
# повторна детекція може скинути відревьювані переклади.
# ============================================================================
set -uo pipefail
cd "$(dirname "$0")"

PY=.venv/bin/python
mkdir -p logs
LOG="logs/night_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$LOG") 2>&1

cfg() { $PY -c "import json;c=json.load(open('config.json'));print(c$1)"; }

CHAPTERS=$(cfg "['run']['chapters']")
PROVIDER=$(cfg "['review'].get('provider','none')")
MAX_FIX=$(cfg "['review'].get('max_fix_loops',2)")
STATE="temp/.night_state_$(echo "$CHAPTERS" | tr -c 'A-Za-z0-9' '_')"
REVIEW_FAILED=0

log() { echo "[night $(date +%H:%M:%S)] $*"; }

stage() {  # stage <name> <command...> — пропускає, якщо вже виконано
    local name=$1; shift
    if [ -f "$STATE" ] && grep -qx "$name" "$STATE"; then
        log "SKIP $name (вже виконано, state: $STATE)"
        return 0
    fi
    log "=== $name"
    if "$@"; then
        echo "$name" >> "$STATE"
        return 0
    fi
    log "!!! $name FAILED (exit $?)"
    return 1
}

log "Нічний цикл: розділи $CHAPTERS, ревю: $PROVIDER, лог: $LOG"

# --- 1. Підготовка (локально) ------------------------------------------------
stage prep_detect    $PY scripts/step1_extract.py    || exit 1
stage prep_translate $PY scripts/step2_translate.py  || exit 1
stage prep_repair    $PY scripts/step2b_repair.py    || exit 1

# --- 2. Бекап + пам'ять перекладів -------------------------------------------
stage backup   $PY scripts/night_tools.py backup    || exit 1
stage tm_apply $PY scripts/night_tools.py tm-apply  || exit 1

# --- 3. АІ-ревю (тільки JSON; збій не зупиняє ніч) ----------------------------
run_review() {
    local cmd
    case "$PROVIDER" in
        codex)  cmd=$(cfg "['review']['codex_cmd']") ;;
        claude) cmd=$(cfg "['review']['claude_cmd']") ;;
        none)   log "ревю вимкнено (provider=none)"; return 0 ;;
        *)      log "невідомий provider '$PROVIDER' — пропускаю ревю"; return 1 ;;
    esac
    log "АІ-ревю через: $cmd"
    eval "$cmd \"\$(cat scripts/prompt_review.txt)\""
}
if [ -f "$STATE" ] && grep -qx review "$STATE"; then
    log "SKIP review (вже виконано)"
elif run_review; then
    echo review >> "$STATE"
else
    REVIEW_FAILED=1
    log "!!! АІ-ревю не завершилось — продовжую БЕЗ нього (рендер піде з поточними перекладами; ревю можна догнати вдень: перезапусти скрипт — виконається лише ревю і точковий дорендер)"
fi

# --- 4. Рендер + озвучка + енкод (кеші самі пропустять готове) ----------------
log "=== render"
$PY scripts/step3_render.py || exit 1

# --- 5. Цикл якості: verify --fix -> дорендер (обмежений) ---------------------
FINAL_RC=0
for i in $(seq 1 "$MAX_FIX"); do
    log "=== verify (прохід $i/$MAX_FIX)"
    $PY scripts/step3b_verify.py --fix
    FINAL_RC=$?
    [ $FINAL_RC -eq 0 ] && break
    if [ $FINAL_RC -eq 3 ] || [ $FINAL_RC -eq 2 ]; then
        log "verify запросив дорендер (rc=$FINAL_RC)"
        $PY scripts/step3_render.py || exit 1
        [ $FINAL_RC -eq 2 ] && [ "$i" -eq "$MAX_FIX" ] && break
    else
        log "!!! verify упав (rc=$FINAL_RC)"; break
    fi
done

# --- 6. Пам'ять перекладів поповнюється рішеннями цього ревю ------------------
if [ $REVIEW_FAILED -eq 0 ]; then
    $PY scripts/night_tools.py tm-harvest || true
else
    log "tm-harvest пропущено (ревю не завершилось)"
fi

# --- 7. Підсумок ---------------------------------------------------------------
log "=== summary"
$PY scripts/night_tools.py summary || true

if [ $REVIEW_FAILED -eq 1 ]; then
    log "СТАТУС: відео готові, але АІ-ревю НЕ виконано — перезапусти run_night.sh, коли ліміт відновиться (виконається лише ревю + точковий дорендер)."
    exit 4
fi
if [ $FINAL_RC -ne 0 ]; then
    log "СТАТУС: залишились leftover-English (див. summary і render_qa.json) — переглянь ці сторінки."
    exit $FINAL_RC
fi
rm -f "$STATE"
log "СТАТУС: батч $CHAPTERS повністю готовий. Відео у video_output/."
