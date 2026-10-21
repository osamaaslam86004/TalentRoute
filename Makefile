.PHONY: install test lint run worker migrate up down logs
install:
	python -m pip install -e '.[dev]'
test:
	pytest -q
lint:
	ruff check app tests migrations
run:
	uvicorn app.main:app --reload
worker:
	celery -A app.workers.celery_app:celery_app worker --loglevel=INFO
migrate:
	alembic upgrade head
up:
	docker compose up --build -d
down:
	docker compose down
logs:
	docker compose logs -f api worker
obs-up:
	docker compose -f docker-compose.yml -f docker-compose.observability.yml up --build -d
obs-down:
	docker compose -f docker-compose.yml -f docker-compose.observability.yml down
