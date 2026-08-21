#!/usr/bin/env python3
"""Turn finished survey jobs into a launch review, written by a local model.

This script does two separate things and it is worth keeping them apart in
your head. First it walks the job directories and pulls out what actually
happened: who answered, what they picked, what they said about it. That part
is mechanical and you can check it. Second it hands that evidence to a model
running on Ollama and asks for a review. That part is a judgement call made by
a language model, and the report says so on its face.

Nothing here is hardcoded. Change the survey and the review changes with it.

Runs against Ollama by default. Export ZAI_API_KEY and it uses z.ai instead,
which is worth doing here: the review is a single call, so the slowness that
rules GLM out for the survey itself does not matter, and it follows the output
format more reliably.

Usage:
    python tools/analyst_report.py jobs/bajra-gemma4cloud-n8
    ZAI_API_KEY=... python tools/analyst_report.py jobs/my-job --model glm-4.6
    python tools/analyst_report.py jobs/bajra-india jobs/bajra-uk --label market
    python tools/analyst_report.py jobs/my-job --model gemma3-4b-ctx16k --out review.md
"""

import argparse
import collections
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.request

OLLAMA = "http://localhost:11434/api/chat"

# Any OpenAI-compatible endpoint works too. z.ai is the one I tested.
ZAI = "https://api.z.ai/api/paas/v4/chat/completions"

# What we ask the model to fill in. Ollama enforces this, so we either get
# something with these keys or we get an error, never half a report.
SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["launch", "launch_with_changes", "do_not_launch_yet"],
        },
        "headline": {"type": "string"},
        "failures": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "what": {"type": "string"},
                    "why": {"type": "string"},
                    "evidence": {"type": "string"},
                    "severity": {"type": "string", "enum": ["high", "medium", "low"]},
                },
                "required": ["what", "why", "evidence", "severity"],
            },
        },
        "strengths": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "what": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["what", "evidence"],
            },
        },
        "where_it_can_win": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "segment": {"type": "string"},
                    "reasoning": {"type": "string"},
                    "what_to_do": {"type": "string"},
                },
                "required": ["segment", "reasoning", "what_to_do"],
            },
        },
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["action", "rationale"],
            },
        },
        "evidence_gaps": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "verdict",
        "headline",
        "failures",
        "strengths",
        "where_it_can_win",
        "actions",
        "evidence_gaps",
    ],
}

BRIEFING = """You are reviewing a pre-launch concept survey for a startup founder \
who has to decide what to do next. You did not run the survey and you have no \
stake in the product succeeding.

Write the review the founder needs rather than the one they want. Say plainly \
where the product fails and why, using the numbers you were given. Say where it \
could still work and who for. If the evidence does not support a conclusion, put \
that in evidence_gaps instead of guessing.

Rules you must follow:
- Every claim in failures and strengths cites a number or a quote from the data.
- Do not invent figures. If you did not see it below, you do not know it.
- The sample is small. Treat direction as informative and magnitude as noise.
- Be specific. "Improve messaging" is not an action. "Drop the claim from the \
front of pack and test price-led creative instead" is.
- Plain language. No consultancy filler.

Answer with a single JSON object and nothing else. No prose before it, no
markdown fence around it. This is the shape:

{"verdict": "launch" | "launch_with_changes" | "do_not_launch_yet",
 "headline": "one or two sentences a founder can repeat in a meeting",
 "failures": [{"what": "...", "why": "...", "evidence": "...",
               "severity": "high" | "medium" | "low"}],
 "strengths": [{"what": "...", "evidence": "..."}],
 "where_it_can_win": [{"segment": "...", "reasoning": "...",
                       "what_to_do": "..."}],
 "actions": [{"action": "...", "rationale": "..."}],
 "evidence_gaps": ["..."]}"""

# Persona fields worth showing the reviewer. Everything else is noise here.
PERSONA_FIELDS = [
    "region",
    "age_bracket",
    "gender_identity",
    "socioeconomic_band",
    "urbanicity",
    "economic_motivation",
    "primary_language",
    "health_dietary_restriction",
]


def read_persona(path):
    """Pull a handful of top-level fields out of a persona YAML.

    Deliberately not using PyYAML. These files are flat enough that a regex
    does the job, and it keeps this script free of dependencies.
    """
    out = {}
    try:
        text = pathlib.Path(path).read_text()
    except OSError:
        return out
    for field in PERSONA_FIELDS:
        found = re.search(rf"^\s*{field}:\s*(.+?)\s*$", text, re.M)
        if found:
            out[field] = found.group(1).strip().strip("'\"")
    return out


def read_question_labels(job_dir):
    """Map option ids back to the words respondents actually saw.

    Falls back to the raw ids if the questionnaire is not sitting next to the
    job, which is fine, just less readable.
    """
    labels = {}
    for candidate in pathlib.Path(job_dir).rglob("questionnaire.yaml"):
        text = candidate.read_text()
        for value, label in re.findall(
            r"^\s*-\s*value:\s*(\S+)\s*\n\s*label:\s*[\"'](.+?)[\"']\s*$", text, re.M
        ):
            labels.setdefault(value, label)
        break
    return labels


def collect(job_dirs):
    """Walk the job directories and build the evidence bundle."""
    trials = []
    for job_dir in job_dirs:
        root = pathlib.Path(job_dir)
        if not root.is_dir():
            sys.exit(f"not a directory: {job_dir}")
        for result_path in sorted(root.glob("*/artifacts/app/output/survey_result.json")):
            trial_dir = result_path.parents[3]
            result = json.loads(result_path.read_text())
            meta_path = trial_dir / "persona_meta.json"
            meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
            trials.append(
                {
                    "job": root.name,
                    "persona_id": meta.get("persona_id", "unknown"),
                    "persona": read_persona(meta.get("persona_path", "")),
                    "answers": result.get("answers", []),
                }
            )

    if not trials:
        sys.exit("no completed trials found. Did the job finish?")

    distributions = collections.defaultdict(collections.Counter)
    quotes = []
    for trial in trials:
        for answer in trial["answers"]:
            qid = answer.get("questionId")
            if qid is None:
                continue
            distributions[qid][str(answer.get("value"))] += 1
            said = (answer.get("rationale") or "").strip()
            if said:
                quotes.append(
                    {
                        "persona": trial["persona_id"],
                        "question": qid,
                        "answer": str(answer.get("value")),
                        "said": said,
                    }
                )

    return {
        "trials": trials,
        "distributions": {q: dict(c) for q, c in distributions.items()},
        "quotes": quotes,
        "labels": read_question_labels(job_dirs[0]),
    }


def format_evidence(evidence, brief_text):
    """Lay the evidence out as text for the model. Numbers first, then voices."""
    labels = evidence["labels"]

    def say(value):
        return labels.get(value, value)

    lines = []
    if brief_text:
        lines += ["## The product brief respondents read", "", brief_text.strip(), ""]

    lines += [f"## Answer distributions ({len(evidence['trials'])} respondents)", ""]
    for question, counts in sorted(evidence["distributions"].items()):
        parts = [
            f"{say(v)} = {n}"
            for v, n in sorted(counts.items(), key=lambda kv: -kv[1])
        ]
        lines.append(f"- {question}: " + "; ".join(parts))

    lines += ["", "## Who answered", ""]
    for trial in evidence["trials"]:
        who = ", ".join(f"{k}={v}" for k, v in trial["persona"].items()) or "no profile"
        lines.append(f"- persona {trial['persona_id']} [{trial['job']}]: {who}")

    if evidence["quotes"]:
        lines += ["", "## What they said in their own words", ""]
        for quote in evidence["quotes"]:
            lines.append(
                f"- persona {quote['persona']} on {quote['question']} "
                f"(chose {say(quote['answer'])}): \"{quote['said']}\""
            )
    return "\n".join(lines)


def ask_ollama(model, evidence_text, timeout, attempts=3):
    """Ask for the review, and keep asking if the model wanders off into prose.

    The `format` field is meant to force valid JSON. Bigger hosted models
    honour it; several do not, especially on long prompts, so we ask again
    with a blunter instruction rather than falling over on the first miss.
    """
    messages = [
        {"role": "system", "content": BRIEFING},
        {"role": "user", "content": evidence_text},
    ]
    api_key = os.environ.get("ZAI_API_KEY", "")
    use_zai = bool(api_key)
    url = ZAI if use_zai else OLLAMA
    last_reply = ""

    for attempt in range(1, attempts + 1):
        temperature = 0.2 if attempt == 1 else 0.0
        if use_zai:
            payload = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": 4000,
                # GLM reasoning models spend their whole budget thinking and
                # return empty content. Turn it off; the review does not need it.
                "thinking": {"type": "disabled"},
            }
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            }
        else:
            payload = {
                "model": model,
                "messages": messages,
                "format": SCHEMA,
                "stream": False,
                "options": {"temperature": temperature, "num_ctx": 16384},
            }
            headers = {"Content-Type": "application/json"}

        request = urllib.request.Request(
            url, data=json.dumps(payload).encode(), headers=headers
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = json.loads(response.read())
        except urllib.error.HTTPError as err:
            sys.exit(f"{url} returned HTTP {err.code}: {err.read()[:300].decode(errors='replace')}")
        except urllib.error.URLError as err:
            sys.exit(f"could not reach {url}: {err}")
        if body.get("error"):
            sys.exit(f"the provider refused: {body['error']}")

        if use_zai:
            last_reply = body["choices"][0]["message"].get("content") or ""
        else:
            last_reply = body.get("message", {}).get("content", "")
        parsed = parse_json(last_reply)
        if parsed is not None and all(key in parsed for key in SCHEMA["required"]):
            return parsed

        print(
            f"attempt {attempt}: model did not return usable JSON, asking again",
            file=sys.stderr,
        )
        messages = messages[:2] + [
            {"role": "assistant", "content": last_reply[:400]},
            {
                "role": "user",
                "content": (
                    "That was prose. Send the same review as one JSON object "
                    "with the keys listed earlier. Start your reply with { and "
                    "end it with }. No other text."
                ),
            },
        ]

    sys.exit(
        f"{model} would not produce the report as JSON after {attempts} tries.\n"
        f"Last reply began:\n{last_reply[:400]}"
    )


def parse_json(content):
    """Get an object out of the reply even when the model gets creative.

    Passing a schema in `format` is supposed to guarantee bare JSON. Some
    models wrap it in a markdown fence anyway, so strip that before parsing
    and fall back to the outermost braces if there is still prose around it.
    Returns None when there is nothing usable, so the caller can retry.
    """
    text = content.strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


# Models like typographic punctuation. It survives copy and paste badly and
# marks a document as unedited machine output, so flatten it on the way out.
PUNCTUATION = {
    "\u2014": ", ",
    "\u2013": "-",
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u2026": "...",
    "\u2192": "->",
    "\u00a0": " ",
}


def flatten_punctuation(text):
    for fancy, plain in PUNCTUATION.items():
        text = text.replace(fancy, plain)
    return text


VERDICT_WORDS = {
    "launch": "Launch",
    "launch_with_changes": "Launch, but not as it stands",
    "do_not_launch_yet": "Do not launch yet",
}


def render(report, evidence, model):
    n = len(evidence["trials"])
    jobs = sorted({t["job"] for t in evidence["trials"]})
    out = [
        "# Launch review: Bajra Bowl",
        "",
        f"**{VERDICT_WORDS.get(report['verdict'], report['verdict'])}**",
        "",
        report["headline"],
        "",
        f"Written by `{model}` from {n} survey responses across "
        f"{', '.join(f'`{j}`' for j in jobs)}. The numbers are pulled out of the "
        "job output and you can check them. The judgement is the model's, and it "
        "is worth as much as you think a sample this size and a model like this "
        "are worth.",
        "",
        "## Where it fails",
        "",
    ]
    # Only Ollama enforces the schema. Over an OpenAI-compatible endpoint the
    # model can drop a nested field, and losing a whole report to one missing
    # key would be a silly way to fail.
    def field(item, key, fallback=""):
        value = item.get(key, fallback) if isinstance(item, dict) else str(item)
        return str(value).strip()

    for item in report["failures"]:
        out += [
            f"### {field(item, 'what', 'Unnamed problem')}"
            + (f" ({field(item, 'severity')} severity)" if field(item, "severity") else ""),
            "",
            field(item, "why"),
            "",
            f"*Evidence:* {field(item, 'evidence', 'none given')}",
            "",
        ]

    out += ["## What is working", ""]
    for item in report.get("strengths", []):
        out.append(f"- **{field(item, 'what')}** {field(item, 'evidence')}")

    out += ["", "## Where it could still win", ""]
    for item in report.get("where_it_can_win", []):
        out += [f"### {field(item, 'segment', 'Unnamed segment')}", "", field(item, "reasoning"), ""]
        todo = field(item, "what_to_do")
        if todo:
            out += [f"*Do this:* {todo}", ""]

    out += ["## Before you launch", ""]
    for i, item in enumerate(report.get("actions", []), 1):
        out.append(f"{i}. **{field(item, 'action')}** {field(item, 'rationale')}")

    out += ["", "## What this survey cannot tell you", ""]
    for gap in report.get("evidence_gaps", []):
        out.append(f"- {gap}")

    out += ["", "---", "", "Generated from job output. Rerun to regenerate.", ""]
    return flatten_punctuation("\n".join(out))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("jobs", nargs="+", help="job directories to review")
    parser.add_argument(
        "--model",
        default="gemma4:31b-cloud",
        help="model name. Set ZAI_API_KEY to use z.ai instead of Ollama, "
        "and pass something like glm-4.6 here.",
    )
    parser.add_argument("--brief", help="product brief to give the reviewer as context")
    parser.add_argument("--out", default="report.md")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument(
        "--dump-evidence",
        help="write the extracted evidence here too, so the report can be checked",
    )
    args = parser.parse_args()

    evidence = collect(args.jobs)
    brief_text = pathlib.Path(args.brief).read_text() if args.brief else ""
    evidence_text = format_evidence(evidence, brief_text)

    if args.dump_evidence:
        pathlib.Path(args.dump_evidence).write_text(evidence_text)

    print(
        f"{len(evidence['trials'])} responses, {len(evidence['quotes'])} quotes. "
        f"Asking {args.model}...",
        file=sys.stderr,
    )
    report = ask_ollama(args.model, evidence_text, args.timeout)
    pathlib.Path(args.out).write_text(render(report, evidence, args.model))
    print(f"wrote {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
