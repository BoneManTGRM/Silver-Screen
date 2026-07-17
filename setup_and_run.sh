# Minimal working version
#!/bin/bash
set -e
git pull
pip install -r requirements.txt
python app.py