# Genus

Asset Intelligence Copilot for the FlytBase Hack Day (Hacktoberfest 2026).

## Run

The model address and API key stay in `backend/.env`, which is not committed. Copy the example and fill it in:

```
copy backend\.env.example backend\.env
```

Backend, from the repository root:

```
pip install -e backend
python -m uvicorn aicopilot.api:app --app-dir backend/src --host 127.0.0.1 --port 8080
```

The first start loads `sample-data/` into a local DuckDB file under `backend/data/`. That folder is not committed.

Frontend, in another terminal:

```
cd frontend
npm install
npm run dev
```

Open the address Vite prints. The chat calls the backend at `http://127.0.0.1:8080`.

## Challenge

Challenge repository for the FlytBase Hack Day (Hacktoberfest 2026).

Build a chat copilot that answers operators' plain-English questions about an oil field, correctly and with evidence, using records, sensor readings, manuals, repair reports and notes.

## Start here

| Document | What it is |
| :--- | :--- |
| [`01-problem-statement.md`](01-problem-statement.md) | The challenge, what to build, what to submit and how we judge |
| [`02-dataset-guide.md`](02-dataset-guide.md) | Every file and field in the sample dataset |
| [`03-evaluation-queries.md`](03-evaluation-queries.md) | Example questions with expected answers, to check your progress |
| [`04-prerequisites.md`](04-prerequisites.md) | What to set up before the event: coding agent and model access |

## Sample data

`sample-data/` holds a fictional oil field: 200 wells in 4 areas, with 4 years of history.

- `sample-data/fleet/`: areas, teams and people, well-type manuals, and operating procedures
- `sample-data/assets/WELL-0001/` to `WELL-0200/`: one folder per well

All data is synthetic and created for this challenge. Manuals and procedures are simplified, fictional documents, not real operating or legal guidance.

## License

[MIT](LICENSE)
