activate_script="/home/ubuntu/datagovmy-ai/env/bin/activate"
root_dir="/home/ubuntu/datagovmy-ai/src/assistant"

source "$activate_script"
cd "$root_dir"
python ingest.py
