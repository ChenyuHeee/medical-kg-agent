#!/bin/bash
# Launch all 7 books in parallel, each with its own worker pool.
# Resume-safe: marker files prevent re-processing.
cd "$(dirname "$0")/.."
source .venv/bin/activate
# Required env vars (set them externally, do NOT commit your key):
#   export MODELSCOPE_API_KEY='<your-key>'
#   export MODELSCOPE_BASE_URL='https://api-inference.modelscope.cn/v1'
#   export MODELSCOPE_MODEL='Qwen/Qwen3-235B-A22B-Instruct-2507'
: "${MODELSCOPE_API_KEY:?need MODELSCOPE_API_KEY}"
: "${MODELSCOPE_BASE_URL:=https://api-inference.modelscope.cn/v1}"
: "${MODELSCOPE_MODEL:=Qwen/Qwen3-235B-A22B-Instruct-2507}"
export MODELSCOPE_BASE_URL MODELSCOPE_MODEL

mkdir -p data/logs
for f in data/chunks/*.json; do
  name=$(basename "$f" .json)
  echo "launching $name..."
  nohup python -m src.kg.extract "$f" --workers 6 > "data/logs/extract_${name}.log" 2>&1 &
done
wait
echo "all done"
