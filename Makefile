.PHONY: install dev lint format typecheck test cov run sample clean

install:
	pip install -e .

dev:
	pip install -e ".[dev]"

lint:
	ruff check src tests scripts

format:
	black src tests scripts
	ruff check --fix src tests scripts

typecheck:
	mypy

test:
	pytest -m "not slow"

cov:
	pytest --cov --cov-report=term-missing

run:
	streamlit run src/smartmis/ui/app.py

sample:
	python scripts/generate_sample_data.py

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage build dist
