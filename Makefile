.PHONY: install dev test check run health smoke

install:
	python -m pip install -r requirements.txt

dev:
	python -m pip install -r requirements-dev.txt

test:
	python -m pytest -q

check:
	python -m compileall -q silver_screen streamlit_app.py
	python -m pytest -q

run:
	streamlit run streamlit_app.py

health:
	python -m silver_screen health --json

smoke:
	python -m silver_screen run --premise "A technician discovers that repaired machines remember their failures." --genre scifi --media off --no-persist --json
