.PHONY: install run test lint assets clean

install:
	pip install -r requirements-dev.txt

run:
	streamlit run app.py

test:
	pytest

lint:
	ruff check .
	mypy --ignore-missing-imports .

assets:
	python scripts/generate_assets.py

clean:
	rm -rf __pycache__ .pytest_cache .ruff_cache .mypy_cache data
