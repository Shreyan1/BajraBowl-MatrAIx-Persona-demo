# BajraBowl MatrAIx Persona demo

A worked example of testing a product on simulated people before you launch it.

The product is made up. Bajra Bowl is an instant pearl-millet breakfast cup, Rs 79 a serving, sold direct to consumers and pitched at people who want something quick that won't spike their blood sugar. None of it is real. The survey around it is, and so is every number in this repo.

I built this as a webinar demo, then kept going because the results got more interesting than the demo needed them to be. The readout is here: [Bajra Bowl Readout](https://claude.ai/code/artifact/78fb5714-4b1f-4b58-ac3e-cd533f485853).

## What came back

Five of seven simulated respondents liked the diabetic-friendly claim. Six of seven refused to buy anyway.

That split is the whole point. A team running an ordinary claim test would have read the first number, concluded the positioning worked, and shipped into a wall. Price was rated 5 out of 5 for importance by five respondents and 4 by the other two, so there wasn't a single person in the cast for whom convenience quietly beat cost. The one thing that moved anybody was the Rs 199 trial pack.

Tightening the cohort to Hindi-native South Asians only made it sharper. Four out of four liked the claim, four out of four wouldn't buy, and two said they'd buy it for an elderly parent rather than themselves.

## You can't run this on its own

This repo is a task directory, not an application. It's meant to be dropped into a [MatrAIx](https://github.com/MatrAIx2026) checkout, which supplies the runtime, the persona pool, and the job generator.

```bash
git clone https://github.com/MatrAIx2026/MatrAIx-Persona-8B
cd MatrAIx-Persona-8B
git clone https://github.com/Shreyan1/BajraBowl-MatrAIx-Persona-demo /tmp/bajra
cp -R /tmp/bajra/task application/tasks/survey_bajra-bowl-d2c-launch
```

## Running it

Any OpenAI-compatible endpoint works. I used Ollama, because the whole point was not needing an API key.

```bash
export OPENAI_API_KEY=ollama OPENAI_BASE_URL=http://localhost:11434/v1
export MATRIX_SURVEY_TASK_PATH=application/tasks/survey_bajra-bowl-d2c-launch

uv run python application/scripts/generate_application_job.py \
  --task application/tasks/survey_bajra-bowl-d2c-launch \
  --execution-mode auto --model-name openai/gemma4:31b-cloud

uv run matraix run -c configs/jobs/application-task-job-recipe/<generated>.yaml
uv run matraix results jobs/<job-name>
```

Eight personas take about a minute. Saved output from every run in this repo is under `results/`, so you can read what happened without running anything.

### Pick the model carefully

I tried three and only one was usable.

| Model | Per persona | Completed | What it did |
|---|---|---|---|
| `gemma4:31b-cloud` | ~8s | 7/8 | Spread across price positions. Income ordering is noisy. |
| `gemma3:4b` | ~48s | 7/8 | Collapsed. Six of seven identical, and one persona flipped between runs. |
| `qwen3:4b` | ~9 min | 0/2 | Times out. Burns the request budget on reasoning tokens before it emits JSON. |

The gemma3 failure is worth keeping as a slide. It had a low-income retiree calling Rs 79 a cup "fair, I'd buy regularly", which is exactly the kind of confident nonsense persona simulation is supposed to catch.

One warning before you present. `gemma4:31b-cloud` returns a malformed answer object roughly one trial in eight, so run it before you're on stage rather than live.

### Local models need more context

Ollama defaults `num_ctx` to 4096. The persona block is around 5,300 tokens, so it gets silently truncated and the model answers confidently on half a person.

```bash
printf 'FROM gemma3:4b\nPARAMETER num_ctx 16384\n' > Modelfile
ollama create gemma3-4b-ctx16k -f Modelfile
```

## Finding Indian personas

There's no country field. The finest geography in the schema is `region`, and India shares its bucket with seven neighbours, so everything below is a proxy.

| Filter | Rows | Basis |
|---|---:|---|
| `region = South Asia` | 160,744 | 18.38% of the 874,653 rows where region is known |
| plus `lang_hindi` Native or Fluent | ~83,600 | 52% of the region, measured |
| plus `cult_india = Native` | ~40,000 | 83% precision, 28% recall |

A defensible India cohort is somewhere between forty and eighty-five thousand people. If you see 252,000 quoted anywhere, that's the calibration target rather than the result, and the gap between those two is the most important thing in this section.

`region` is the worst-calibrated dimension in the dataset. It targets 25.23% South Asia and lands at 18.38%. It targets 4.80% North America and lands at 18.81%, which is a fourteen point miss. Age and gender land within 0.08% of target, so this isn't sloppiness in general, it's specific: the human-grounded sources are Wikipedia, Stack Overflow and Amazon reviews, and those are overwhelmingly American.

Pin income yourself:

```json
"dimensionFilters": {
  "region": ["South Asia"],
  "primary_language": ["Hindi"],
  "lang_hindi": ["Native", "Fluent"],
  "socioeconomic_band": ["Low income", "Lower-middle", "Middle"],
  "age_bracket": ["18-24", "25-34", "35-44", "45-54", "55-64"]
}
```

## Four markets at once

A market is a filter, so a market is a file. There's one per market sitting in `task/markets/`, you pick which one with `--strategy`, and each run writes to its own job directory so nothing collides.

Build the job for one market:

```bash
uv run python application/scripts/generate_application_job.py \
  --task application/tasks/survey_bajra-bowl-d2c-launch \
  --strategy application/tasks/survey_bajra-bowl-d2c-launch/markets/uk.json \
  --job-name bajra-uk --out configs/jobs/market-uk.yaml \
  --model-name openai/gemma4:31b-cloud --seed 42
```

Then run it:

```bash
uv run matraix run -c configs/jobs/market-uk.yaml
```

Do the same for `india.json`, `us.json` and `uae.json`. If you want them going at the same time, the jobs are separate processes writing to separate directories, so you can just background each one and wait:

```bash
uv run matraix run -c configs/jobs/market-india.yaml &
uv run matraix run -c configs/jobs/market-uk.yaml &
uv run matraix run -c configs/jobs/market-us.yaml &
uv run matraix run -c configs/jobs/market-uae.yaml &
wait
```

Sixteen respondents across four markets, forty seconds, no exceptions. Against a local Ollama the four jobs fight over one GPU and you lose the gain, so only do this against a hosted endpoint.

| Market | Buys at launch | Price reaction | Against home-cooked | Health claim |
|---|---|---|---|---|
| India | No, 3/4 | Only on offer | Cannot justify the gap | Appeals, 2/4 |
| UK | Yes, 3/4 | Occasional treat | Time worth paying for | Irrelevant, 4/4 |
| US | Yes, 3/4 | Occasional treat | Worth it some days | Irrelevant, 2/4 |
| Dubai | Yes, 4/4 | Occasional treat, 4/4 | Time worth paying for | Irrelevant, 3/4 |

The price objection is India-only. So is the health claim, which was the strongest hook in India and landed as irrelevant everywhere else.

Now the part you have to say out loud before someone else does. The brief is written in rupees against Indian competition, and Rs 79 is about 75 pence, so a British respondent is reacting to a foreign number that reads as pocket change. This measures how people respond to an Indian product brief. It does not measure market entry. To do that properly you'd localise `input/context.md` per market and hold `questionnaire.yaml` fixed so the instrument stays comparable.

### Where the files in task/markets came from

Somebody always asks this, so here it is up front. I wrote them by hand.

There's no tool in the repo that reads a country name and hands you back a cohort, and building one would be guesswork wearing a nicer coat. What I did instead was check every value I typed against `persona/schema/dimensions.json`, which is the schema the dataset is generated from. That matters more than it sounds like it should, because a filter value that doesn't exist matches nothing at all and doesn't complain about it. You get an empty cohort and no error.

The `region` dimension has exactly ten legal values:

```
North America, Latin America, Western Europe, Eastern Europe,
Sub-Saharan Africa, MENA, South Asia, East Asia,
Southeast Asia, Oceania
```

Mapping a market onto one of those ten is a judgement call rather than a lookup. India becomes South Asia because nothing finer exists. The UK becomes Western Europe, which also holds France, Germany and Sweden. Dubai becomes MENA, a bucket running from Morocco to Iran, and that mapping is as rough as it looks.

So: the structure is hand-written, the vocabulary is checked against the schema, and the numbers came out of the data afterwards. Cohort sizes in the tables above were counted off `sample/sample.parquet` and the build's published results, not estimated, which is exactly why they disagree with the calibration targets.

One more piece of history worth having. These files originally stratified on `urbanicity`, and three of the four markets refused to run:

```
Incomplete stratify coverage: 'urbanicity=Dense urban' has 0,
need sample_size_per_value_group=1
```

Urbanicity is filled for eleven of twelve South Asian adults in the dev sample and two of eighteen Western Europeans. The stratification I'd tuned against India was quietly wrong everywhere else, and the generator caught it before I did. I dropped the stratification and the files now sample at random inside their filters.

### Country targeting

There are 40 `cult_<country>` dimensions, graded Native, Lived there, Visited, Studied, Unfamiliar. Intersect one with `region` and you get much closer than either does alone, at the cost of throwing away most of your pool. That trade is usually worth taking.

| Market | Region cohort | `cult_X = Native` | Precision | Recall |
|---|---|---:|---:|---:|
| India | South Asia, 160,744 | ~48,000 | 83% | 28% |
| US | North America, 164,483 | ~56,000 | 80% | 27% |
| UK | Western Europe, 102,594 | ~18,000 | 67% | 14% |
| Dubai | MENA, 46,991 | ~12,000 | 33% | 9% |

Dubai can't be done properly and I'd rather say so than fudge it. Every number in that row is the worst of the four, and the real problem isn't the numbers. The UAE is around 88% expatriate, so an honest Dubai panel is mostly Indian, Pakistani and Filipino people, and `region: MENA` filters exactly those people out. Build it as a deliberate blend of source-country cohorts and call it a construction rather than a sample.

## Things that will bite you

**Cuisine is not nationality.** `cuis_indian = Love` looks like an India filter. Its parents in the graph are dietary restriction and interest in cooking, and region isn't among them. Eight personas in the sample love Indian food and one of them is South Asian.

**Culture is precise but narrow.** `cult_india = Native` is genuinely region-bound, so precision holds up. Recall doesn't. It catches under a third of South Asians, so use it to tighten a cohort and never as your only filter.

**The 1,290 dimensions are a schema, not a fill.** Mean populated attributes per persona is 656. The dev sample that ships with MatrAIx is much thinner at a median of 176, so filters you develop against it behave differently on the real coreset. Country-culture fields are known on roughly 57% to 63% of rows, which means filtering on one throws away 40% of your pool on missingness before it does anything useful.

**Stratification doesn't travel.** `urbanicity` is filled for 11 of 12 South Asian adults in the dev sample and 2 of 18 Western Europeans. Stratifying on it works for India and fails outright for the UK with `Incomplete stratify coverage`. The failure is loud, which is the right behaviour, but check fill rate per market before you reuse a stratification.

**Cells starve quietly.** Filters expand to a Cartesian product and the feasibility check is a catalog check, not a density check. Inside South Asia, Hindi crossed with Hindi-native holds 74,193 rows and Bengali crossed with Hindi-native holds 102. Under `equalTotal` those draw the same headcount, and you end up sampling a handful of people over and over and reading it as a segment.

## Getting the coreset

The dataset is 6.8 GB and a bare `hf download` pulls all of it, including a 2.6 GB index most people don't need. Take the sample first.

```bash
hf download MatrAIx2026/MatrAIx_Persona_1M sample/sample.parquet \
  --repo-type dataset --local-dir persona/datasets/matraix-persona-1m
```

| Path | Size | For |
|---|---:|---|
| `sample/sample.parquet` | 0.6 MB | Working out your filter |
| `data/*.parquet`, ten of them | 4.2 GB | Running against real cohorts |
| `indexes/postings.sqlite` | 2.6 GB | Fast lookup at scale |

Shards are uneven. Shard 0000 alone is 1.1 GB while 0006 through 0009 are 38 MB each, so grabbing a few to save time gives you a biased slice rather than a smaller random one. All ten, or the sample.

One more thing about the shards. They store attributes as packed 4-bit codes, two to a byte, which means the `datasets` library can't open them at all and you'll want pyarrow plus a decode against `persona_codes.schema.json`.

## What this doesn't establish

Everything here is a lead, not a finding. The useful output is "price looks like it dominates the claim, go test that with real people", and not "62% of Indians won't buy this".

The persona pool isn't a census-weighted Indian panel and doesn't resolve state, caste, language community or urban tier. Sample sizes in this repo run from four to eight, which is enough to demonstrate a pipeline and nowhere near enough to support a decision. Some of the dev-sample personas have display names that contradict their own `gender_identity` field, so don't project a raw persona card on a screen without reading it first.

Whether a language model roleplaying a demographic profile tells you anything real about that demographic is still an open question, and I don't think anyone has answered it properly yet. This repo won't settle it. What it does show is that the answer depends heavily on which model you point at it, which is at least a tractable thing to test.
