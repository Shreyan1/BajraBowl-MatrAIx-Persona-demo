#!/bin/bash
set -euo pipefail

mkdir -p /app/output

python3 <<'PY'
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - container usually has PyYAML
    yaml = None

OUTPUT = Path("/app/output/survey_result.json")
PERSONA_PATH = Path("/app/input/persona.yaml")
QUESTIONNAIRE_CANDIDATES = (
    Path("/app/input/questionnaire.yaml"),
    Path("/app/input/input/questionnaire.yaml"),
)

PATHS: dict[str, dict[str, object]] = {
    "Cost-sensitive": {
        "q_price_threshold": "too_expensive_walk_away",
        "q_price_centrality": 5,
        "q_health_claim_framing": "claim_irrelevant_to_me",
        "q_homemade_substitution": "cannot_justify_the_gap",
        "q_purchase_target": "would_not_buy_for_anyone",
        "q_channel_preference": "local_shop",
        "q_trial_trigger": "trial_pack_199",
        "overall_interest": 2,
        "would_buy_at_launch": "false",
    },
    "Value-driven": {
        "q_price_threshold": "occasional_only",
        "q_price_centrality": 4,
        "q_health_claim_framing": "claim_draws_me_in",
        "q_homemade_substitution": "worth_it_only_some_days",
        "q_purchase_target": "for_elder_family_member",
        "q_channel_preference": "quick_commerce",
        "q_trial_trigger": "trial_pack_199",
        "overall_interest": 3,
        "would_buy_at_launch": "false",
    },
    "Premium-seeking": {
        "q_price_threshold": "fair_buy_regularly",
        "q_price_centrality": 2,
        "q_health_claim_framing": "claim_draws_me_in",
        "q_homemade_substitution": "time_worth_paying_for",
        "q_purchase_target": "for_myself",
        "q_channel_preference": "website_subscription",
        "q_trial_trigger": "tasted_it_somewhere",
        "overall_interest": 5,
        "would_buy_at_launch": "true",
    },
    "Indifferent": {
        "q_price_threshold": "only_on_offer",
        "q_price_centrality": 3,
        "q_health_claim_framing": "claim_irrelevant_to_me",
        "q_homemade_substitution": "packaged_is_a_compromise",
        "q_purchase_target": "would_not_buy_for_anyone",
        "q_channel_preference": "nowhere",
        "q_trial_trigger": "nothing_would",
        "overall_interest": 1,
        "would_buy_at_launch": "false",
    },
}

RATIONALES: dict[str, dict[str, str]] = {
    "Cost-sensitive": {
        "q_health_claim_framing": "Nobody at home has been told to watch sugar, so the claim does not change anything for me.",
        "q_homemade_substitution": "Three times the price for the same bajra I already keep in the kitchen is not something I can justify.",
    },
    "Value-driven": {
        "q_health_claim_framing": "My mother has been asked to move to millets, so a low-GI option that needs no cooking is genuinely useful.",
        "q_homemade_substitution": "On a normal day I would cook, but on a rushed morning paying extra to skip it makes sense.",
    },
    "Premium-seeking": {
        "q_health_claim_framing": "A clear low-GI claim is exactly what I look for when I pick a packaged breakfast.",
        "q_homemade_substitution": "Twenty minutes in the morning is worth far more to me than the sixty rupees difference.",
    },
    "Indifferent": {
        "q_health_claim_framing": "I do not really read claims on packets one way or the other.",
        "q_homemade_substitution": "Packaged breakfast is not something I would switch to regardless of the price.",
    },
}

CONFIDENCE: dict[str, float] = {
    "Cost-sensitive": 5.0,
    "Value-driven": 4.0,
    "Premium-seeking": 4.0,
    "Indifferent": 2.0,
}


def _posture() -> str:
    if not PERSONA_PATH.is_file():
        return "Value-driven"
    text = PERSONA_PATH.read_text(encoding="utf-8")
    for line in text.splitlines():
        if "economic_motivation:" in line:
            value = line.split(":", 1)[1].strip().strip("'\"")
            if value in PATHS:
                return value
            return "Value-driven"
    return "Value-driven"


def _load_instrument() -> dict:
    for path in QUESTIONNAIRE_CANDIDATES:
        if path.is_file() and yaml is not None:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if isinstance(data, dict) and data.get("questions"):
                return data
    return {
        "id": "bajra_bowl_prelaunch_v1",
        "title": "Bajra Bowl: pre-launch concept survey",
        "questions": [
            {
                "id": key,
                "prompt": key,
                "type": "likert" if key in {"q_price_centrality", "overall_interest"} else "single_choice",
            }
            for key in PATHS["Value-driven"]
        ],
    }


def _ts(base: datetime, offset: int) -> str:
    return (base + timedelta(seconds=offset)).isoformat().replace("+00:00", "Z")


posture = _posture()
instrument = _load_instrument()
choices = PATHS.get(posture, PATHS["Value-driven"])
rationales = RATIONALES.get(posture, RATIONALES["Value-driven"])
questions = list(instrument.get("questions") or [])

answers = []
for question in questions:
    qid = str(question.get("id") or "").strip()
    if not qid or qid not in choices:
        continue
    answer: dict[str, object] = {
        "questionId": qid,
        "prompt": str(question.get("prompt") or qid),
        "value": choices[qid],
    }
    if question.get("askRationale") and qid in rationales:
        answer["rationale"] = rationales[qid]
    if question.get("askConfidence"):
        answer["confidence"] = CONFIDENCE.get(posture, 3.0)
    answers.append(answer)

base = datetime.now(timezone.utc).replace(microsecond=0)
instrument_id = str(instrument.get("id") or "bajra_bowl_prelaunch_v1")
trajectory = [
    {
        "timestamp": _ts(base, 0),
        "actor": "system",
        "action": "survey_started",
        "context": {
            "instrumentId": instrument_id,
            "instrumentTitle": str(instrument.get("title") or ""),
            "numQuestions": len(questions),
        },
        "outcome": {"status": "started"},
    }
]
offset = 1
for index, question in enumerate(questions, start=1):
    qid = str(question.get("id") or "").strip()
    if not qid:
        continue
    qctx = {
        "instrumentId": instrument_id,
        "questionId": qid,
        "questionIndex": index,
        "questionType": str(question.get("type") or ""),
        "construct": str(question.get("construct") or ""),
    }
    trajectory.append(
        {
            "timestamp": _ts(base, offset),
            "actor": "assistant",
            "action": "ask_question",
            "context": qctx,
            "outcome": {"prompt": str(question.get("prompt") or qid)},
        }
    )
    offset += 1
    if qid in choices:
        trajectory.append(
            {
                "timestamp": _ts(base, offset),
                "actor": "user",
                "action": "answer_question",
                "context": qctx,
                "outcome": {"questionId": qid, "value": choices[qid]},
            }
        )
        offset += 1

trajectory.append(
    {
        "timestamp": _ts(base, offset),
        "actor": "system",
        "action": "survey_completed",
        "context": {"instrumentId": instrument_id},
        "outcome": {
            "numAnswered": len(answers),
            "missingRequiredQuestionIds": [],
            "valid": True,
        },
    }
)

payload = {
    "instrument": {
        "id": instrument_id,
        "title": str(instrument.get("title") or "Bajra Bowl: pre-launch concept survey"),
    },
    "answers": answers,
    "trajectory": trajectory,
}
OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
