from dataclasses import asdict
from importlib.resources import files
import json
from urllib.request import Request, urlopen

from .spec import DOCS, SUPPORTED_PATHS


DIAGNOSES = {
    "typeorm-upgrade-path": "The application reported the documented TypeORM upgrade path error",
    "observed-failure": "A check failed at the cited stage",
    "insufficient-evidence": "The supplied evidence leaves the underlying cause undetermined",
}
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["failure_stage", "evidence_ids", "documentation_links", "next_path", "diagnosis"],
    "properties": {
        "failure_stage": {"type": "string"},
        "diagnosis": {"type": "string", "enum": list(DIAGNOSES)},
        **{field: {"type": "array", "items": {"type": "string"}}
           for field in ("evidence_ids", "documentation_links", "next_path")},
    },
}


def gpt_response(api_key, model, prompt, message):
    body = {"model": model, "instructions": prompt, "input": json.dumps(message), "store": False,
            "max_output_tokens": 1000,
            "text": {"format": {"type": "json_schema", "name": "rehearsal_analysis", "strict": True, "schema": SCHEMA}}}
    request = Request("https://api.openai.com/v1/responses", data=json.dumps(body).encode(),
                      headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
    with urlopen(request, timeout=45) as response:
        payload = json.load(response)
    texts = [content["text"] for item in payload["output"] if item["type"] == "message"
             for content in item["content"] if content["type"] == "output_text"]
    return json.loads("".join(texts))


def analyze_failure(report, path, candidates, respond):
    failure = path.first_failure
    evidence = [report.evidence_for(stage) for stage in path.stages]
    allowed = tuple(candidate for candidate in candidates if candidate in SUPPORTED_PATHS and candidate != path.versions)
    payload = {"path": path.versions, "stages": [asdict(stage) for stage in path.stages],
               "sanitized_log_evidence": [asdict(item) for item in evidence], "documents": DOCS, "candidates": allowed}
    try:
        prompt = files(__package__).joinpath("agent_prompt.txt").read_text(encoding="utf-8")
        output = respond(prompt, payload)
        if not isinstance(output, dict) or output.keys() != set(SCHEMA["required"]):
            raise ValueError
        diagnosis = output["diagnosis"]
        ids, links, next_path = (output[field] for field in ("evidence_ids", "documentation_links", "next_path"))
        if any(not isinstance(array, list) or any(not isinstance(value, str) for value in array)
               for array in (ids, links, next_path)):
            raise ValueError
        if (output["failure_stage"] != failure.name or diagnosis not in DIAGNOSES
                or failure.evidence_id not in ids or not set(ids) <= {item.id for item in evidence}
                or not set(links) <= set(DOCS.values()) or (next_path and tuple(next_path) not in allowed)):
            raise ValueError
        if diagnosis == "typeorm-upgrade-path" and report.evidence_for(failure).code != "invalid-upgrade-path":
            raise ValueError
        return {"mode": "GPT", "diagnosis": DIAGNOSES[diagnosis], "failure_stage": failure.name,
                "evidence_ids": list(dict.fromkeys(ids)), "documentation_links": list(dict.fromkeys(links)),
                "next_path": next_path}
    except Exception:
        return {"mode": "GPT fallback", "diagnosis": "The model was unavailable or its response failed validation",
                "failure_stage": failure.name, "evidence_ids": [failure.evidence_id], "next_path": None}
