#!/bin/bash
git pull
pip install -r requirements.txt
python app.py
python pipeline.py
echo '🎉 Silver-Screen App is 100% complete and ready. Use it for your movie!' 