# Contributing

Thanks for your interest in WeiQuiz.

## Development Setup

```bash
cp .env.example .env
docker compose up -d redis postgres
uv sync
uv run uvicorn app.api:app --reload
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

## Before Opening a Pull Request

- Keep changes focused and easy to review.
- Add or update tests when behavior changes.
- Do not commit `.env`, local documents, indexes, generated data, or API keys.
- Run relevant tests with `uv run pytest`.
- For frontend changes, run `npm run build` inside `frontend/`.

## Code Style

- Follow the existing module boundaries.
- Prefer explicit data contracts over ad hoc dictionaries for API and retrieval payloads.
- Keep RAG workflow logic testable without requiring external services.
- Add short comments only where they explain non-obvious decisions.

## Documentation

Update `README.md` for user-facing setup or feature changes. Put deeper implementation notes under `docs/`.
