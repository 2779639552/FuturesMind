# Contributing to FuturesMind

Thanks for your interest in contributing! Here's how to get started.

## Development Setup

```bash
git clone https://github.com/2779639552/FuturesMind.git
cd FuturesMind
python -m venv venv
venv\Scripts\pip install -e ".[dev]"
cp .env.example .env
# Edit .env with your API key
```

## Project Structure

- `tradingagents/` — Core library (agents, dataflows, graph, LLM clients)
- `cli/` — CLI tools
- `web_app.py` — Flask web dashboard
- `commodity_demo.py` — Main analysis entry point
- `tests/` — Test suite

## What to Work On

### Good First Issues
- Add a new commodity variety to `tradingagents/dataflows/commodity_futures.py`
- Improve documentation or add docstrings
- Add tests for untested functionality

### Medium
- Integrate a new data source (news API, social platform)
- Add a new trading strategy to `signal_analyzer.py`
- Improve the web dashboard UI

### Advanced
- Add a new analyst agent type
- Integrate a new LLM provider
- Optimize the debate framework

## Code Style

- Python 3.10+ with type hints where practical
- Follow existing patterns in the codebase
- Run `ruff check .` before submitting

## Pull Request Process

1. Fork the repo and create a feature branch
2. Add tests if applicable
3. Update documentation if needed
4. Submit a PR with a clear description

## Questions?

Open an issue or start a discussion!
