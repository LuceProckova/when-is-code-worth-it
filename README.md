# When Is Code Worth It?

Code, data and logs for my bachelor thesis *When Is Code Worth It? A Framework for Replacing LLM Steps in Local Assistant Workflows* (IU Internationale Hochschule, Computer Science, 2026).

The thesis builds a framework that decides for each step of a workflow whether a model, code or the person should do it. It is tested on a calendar workflow from my assistant PaperTrAIl, which turns a request like "dentist next Wednesday at half 10" into a calendar event. The workflow runs in 3 versions on Qwen2.5 7B and 72B: one prompt, every step done by a model, and the framework's split where code takes the steps with one right answer.

## What is where

| Folder | What it holds |
| --- | --- |
| `versions/one-prompt/` | the whole request in 1 prompt, for Qwen and the hosted Claude models, with its results |
| `versions/all-model/` | the workflow where a model does every step, with its results |
| `versions/with-code/` | the workflow where code takes the steps with one right answer, with its results |
| `runner/` | runs the 2 workflows, measures energy with Zeus and saves a hash of every file it used |
| `app/` | the PaperTrAIl code that carries out the workflow steps (`papertrail.py`, the steps in `steps/`) |
| `test-data/` | the 100 test requests, the answer key and in `tables/` the rule tables of the made-up person Alex: calendars, people, places, durations, naming |
| `analysis/` | scoring and the statistics in the thesis |
| `labelling/` | the skill that lets a large model label every step as person, code or model, run on Claude Opus 5.5 and on 5 open models through Ollama |

## Running it

You need Python 3.14 with numpy, scipy and python-dateutil, [Ollama](https://ollama.com) with `qwen2.5:7b` and an NVIDIA GPU for the energy readings.

```
python runner/run_tests.py --flow thesis-create-events --reps 3
python runner/run_tests.py --flow thesis-create-events-allmodel --reps 3
python versions/one-prompt/run_baseline.py --backend ollama --model qwen2.5:7b
python analysis/analyse.py --gold test-data/answer-key.csv --requests test-data/requests.csv versions/with-code/results/<log>.jsonl
```

The Claude runs and the labelling need an Anthropic API key in `ANTHROPIC_API_KEY`. The open-model labelling (`label_steps_local.py`) only needs an Ollama server, local or on a rented GPU.

## Licence

The test data is shared under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). All names, companies and addresses in it are made up. The code is © 2026 Lucia Pročková, all rights reserved.
