"""Validate the assistant payload built by provision_vapi.py against Vapi's published OpenAPI spec.

Vapi rejects unknown properties with HTTP 400, and its API evolves (e.g. `serverUrl` -> `server`,
`silenceTimeoutSeconds` -> hooks). Run this before provisioning to catch drift without needing an account:

    pip install jsonschema
    python -m voice_agent.check_payload
"""
import copy
import os
import sys

import httpx
from jsonschema import Draft7Validator

os.environ.setdefault("PUBLIC_BASE_URL", "https://example.invalid")
os.environ.setdefault("VAPI_WEBHOOK_SECRET", "check")

from voice_agent.provision_vapi import build_assistant_payload  # noqa: E402


def _normalise(node):
    """Make the OpenAPI doc strict: oneOf -> anyOf (Vapi's string-or-enum unions are ambiguous), JS regex
    delimiters stripped, and unknown properties disallowed (what the API actually does)."""
    if isinstance(node, dict):
        if "oneOf" in node:
            node["anyOf"] = node.pop("oneOf")
        pattern = node.get("pattern")
        if isinstance(pattern, str) and pattern.startswith("/") and pattern.endswith("/"):
            node["pattern"] = pattern[1:-1]
        if node.get("type") == "object" and "properties" in node and "additionalProperties" not in node and "allOf" not in node:
            node["additionalProperties"] = False
        for value in list(node.values()):
            _normalise(value)
    elif isinstance(node, list):
        for value in node:
            _normalise(value)


def main() -> int:
    spec = httpx.get("https://api.vapi.ai/api-json", timeout=60, follow_redirects=True).json()
    components = copy.deepcopy(spec["components"])
    _normalise(components)

    def check(schema_name: str, data: dict, label: str) -> bool:
        schema = {"$ref": f"#/components/schemas/{schema_name}", "components": components}
        errors = list(Draft7Validator(schema).iter_errors(data))
        print(f"{'OK  ' if not errors else 'FAIL'} {label}")
        for err in errors[:5]:
            print("     ", "/".join(map(str, err.path)), "->", err.message[:160])
        return not errors

    payload = build_assistant_payload()
    ok = True
    for tool in payload["model"]["tools"]:
        if tool["type"] == "function":
            ok &= check("CreateFunctionToolDTO", tool, f"tool {tool['function']['name']}")
        else:
            ok &= check("CreateEndCallToolDTO", tool, f"tool {tool['type']}")
    for hook in payload["hooks"]:
        ok &= check("CallHookCustomerSpeechTimeout", hook, "hook customer.speech.timeout")
    rest = copy.deepcopy(payload)
    rest["model"]["tools"] = []
    server_messages = rest.pop("serverMessages")  # Vapi's spec declares this array with an array-level enum quirk
    allowed = spec["components"]["schemas"]["CreateAssistantDTO"]["properties"]["serverMessages"]["enum"]
    ok &= check("CreateAssistantDTO", rest, "assistant (everything else)")
    valid_messages = all(m in allowed for m in server_messages)
    print(f"{'OK  ' if valid_messages else 'FAIL'} serverMessages values")
    return 0 if ok and valid_messages else 1


if __name__ == "__main__":
    sys.exit(main())
