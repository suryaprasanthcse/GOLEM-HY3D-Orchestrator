#!/usr/bin/env bash
# The known-answer set: the hackathon's Hyper3D meshes, split by Tencent HY-3D-Component (3 calls) and by
# the hackathon's Hyper3D BANG splits, each run through the whole orchestrator one at a time (one Unity at
# a time), then scored against the artist. Recorded HY-3D-Component jobs are reused, never sent again.
#   bash tools/run_known_answer.sh
set -u
cd "$(dirname "$0")/.."
PY=${PY:-python}
HACK=${HACK:-../golem_oct3/assets/raw}
RAWURL=https://raw.githubusercontent.com/suryaprasanthcse/Golem-tencent-hy4-preview/main/inputs
CABINET="wooden filing cabinet with three drawers, game prop"
VAULT="A heavy steampunk bank vault door with a central locking wheel."
CHEST="wooden treasure chest with a hinged lid, closed, boxy shape, metal corner brackets, game prop"

fresh() {  # clear a run's derived outputs; keep its recorded HY-3D-Component job and parts
  rm -rf "runs/$1/sweep.json" "runs/$1/decisions.json" "runs/$1/choices.json" "runs/$1/colours.json" \
         "runs/$1/views" "runs/$1/split" "runs/$1/proof.json" "runs/$1/caption.txt" "runs/$1/unity.log" \
         "runs/$1/frames" runs/"$1"/*_articulation_demo.mp4
}

tencent() {  # name, prompt
  fresh "$1__tencent"
  $PY run_orchestrator.py "$1" --run "$1__tencent" --split component --raw "$HACK/$1.glb" --fbx "inputs/$1.fbx" \
      --fbx-url "$RAWURL/$1.fbx" --prompt "$2" > "runs/$1__tencent.log" 2>&1
  echo "[known-answer] $1__tencent exit $?"
}

bang() {  # name, prompt
  fresh "$1__bang"
  $PY run_orchestrator.py "$1" --run "$1__bang" --glb "$HACK/$1_bang.glb" \
      --glb-source "hyper3d bang (hackathon, 3 Oct 2026)" --raw "$HACK/$1.glb" --prompt "$2" > "runs/$1__bang.log" 2>&1
  echo "[known-answer] $1__bang exit $?"
}

tencent filing_cabinet "$CABINET"
bang filing_cabinet "$CABINET"
bang vault_door "$VAULT"
tencent vault_door "$VAULT"
tencent chest "$CHEST"
$PY tools/score.py
echo "[known-answer] done"
