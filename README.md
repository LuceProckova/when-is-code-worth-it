# When Is Code Worth It?

Code, data and logs for my bachelor thesis *When Is Code Worth It? A Framework for Replacing LLM Steps in Local Assistant Workflows* (IU Internationale Hochschule, Computer Science, 2026).

The thesis builds a framework that decides for each step of a workflow whether a model, code or the person should do it. It is tested on a calendar workflow from my assistant PaperTrAIl, which turns a request like "dentist next Wednesday at half 10" into a calendar event. The workflow runs in 3 versions on Qwen2.5 7B and 72B: one prompt, every step done by a model, and the framework's split where code takes the steps with one right answer.

## What is where

| Folder | What it holds |
| --- | --- |
| `app/` | the PaperTrAIl code that runs the workflows (`papertrail.py`, the steps in `steps/`) |
| `data/workflows/` | the 2 workflow versions, with code (`thesis-create-events.json`) and all model (`thesis-create-events-allmodel.json`) |
| `data/knowledge/thesis/` | the rule tables of the made-up person Alex: calendars, people, places, durations, naming |
| `one-prompt/` | the one prompt version, for Qwen and the hosted Claude models |
| `runner/` | runs the test requests, measures energy with Zeus and saves a hash of every file it used |
| `test-data/` | the 100 test requests and the answer key |
| `results/` | the logs of every measured run |
| `analysis/` | scoring and the statistics in the thesis |
| `dev-log/` | what changed in every development round |
| `labelling/` | the skill that lets a large model label every step as person, code or model |

## Running it

You need Python 3.14 with numpy, scipy and python-dateutil, [Ollama](https://ollama.com) with `qwen2.5:7b` and an NVIDIA GPU for the energy readings.

```
python runner/run_tests.py --flow thesis-create-events --reps 3
python runner/run_tests.py --flow thesis-create-events-allmodel --reps 3
python one-prompt/run_baseline.py --backend ollama --model qwen2.5:7b
python analysis/analyse.py --gold test-data/answer-key.csv --requests test-data/requests.csv results/<log>.jsonl
```

The Claude runs and the labelling need an Anthropic API key in `ANTHROPIC_API_KEY`.

## Licence

The test data is shared under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). All names, companies and addresses in it are made up.
