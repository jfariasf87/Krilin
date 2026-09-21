from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from krilin.jev import JevDecider, build_request, parse_response
from krilin.models import Action, KrilinError

ACTIONS = (Action("wait", "wait"), Action("click:e1", "click", "e1"))
RESPONSE = {
    "model": "typesafe/jev-1.13",
    "answers": {
        "next_action": {"type": "choice", "choice": "click:e1", "confidence": .9,
                        "probabilities": {"wait": .02, "click:e1": .98}},
        "goal_achieved": {"type": "noul", "noul": .01},
    },
    "usage": {"input_tokens": 400, "output_tokens": 20},
}


class JevTests(unittest.TestCase):
    def test_request_uses_typed_questions_and_complete_actions(self):
        req = build_request("typesafe/jev-1.13", {"goal": "Save"}, ACTIONS)
        self.assertEqual(req["questions"]["next_action"]["type"], "choice")
        self.assertEqual(json.loads(req["questions"]["next_action"]["criteria"]["click:e1"]), {"kind": "click", "target": "e1"})
        self.assertEqual(json.loads(req["questions"]["next_action"]["criteria"]["wait"]), {"kind": "wait"})
        self.assertNotIn("messages", req)
        schema = json.loads((Path(__file__).parents[1] / "docs/openrouter-decisions.schema.json").read_text())
        self.assertEqual(set(req), set(schema["schemas"]["DecisionsRequest"]["required"]))

    def test_strict_response(self):
        self.assertEqual(parse_response(RESPONSE, ACTIONS).selected_probability, .98)
        for field, value in [("choice", "invented"), ("confidence", float("nan")),
                             ("confidence", True), ("confidence", 1.1), ("probabilities", {"wait": 1})]:
            with self.subTest(field=field, value=value):
                response = deepcopy(RESPONSE)
                response["answers"]["next_action"][field] = value
                with self.assertRaises(KrilinError):
                    parse_response(response, ACTIONS)

    def test_missing_confidence_fails_closed(self):
        response = deepcopy(RESPONSE)
        del response["answers"]["next_action"]["confidence"]
        with self.assertRaises(KrilinError):
            parse_response(response, ACTIONS)

    def test_inconsistent_distribution_rejected(self):
        for probs in ({"wait": .8, "click:e1": .2}, {"wait": .8, "click:e1": .9}):
            response = deepcopy(RESPONSE)
            response["answers"]["next_action"]["probabilities"] = probs
            with self.assertRaises(KrilinError):
                parse_response(response, ACTIONS)

    def test_rounded_distributions_are_tolerated_but_the_maximum_is_strict(self):
        many = tuple(Action(f"click:e{i}", "click", f"e{i}") for i in range(20))
        response = deepcopy(RESPONSE)
        response["answers"]["next_action"].update(choice="click:e0", probabilities={a.id: .05 for a in many})
        response["answers"]["next_action"]["probabilities"].update({"click:e0": .04, "click:e1": .04})  # Sums to 0.98.
        with self.assertRaisesRegex(KrilinError, "not the maximum"):
            parse_response(response, many)
        response["answers"]["next_action"]["probabilities"].update({"click:e0": .05, "click:e1": .04})  # Sums to 0.99.
        self.assertEqual(parse_response(response, many).selected_probability, .05)
        response["answers"]["next_action"]["probabilities"].update({"click:e1": .0, "click:e2": .0, "click:e3": .0})  # 0.85
        with self.assertRaisesRegex(KrilinError, "sum to"):
            parse_response(response, many)
        two = deepcopy(RESPONSE)
        two["answers"]["next_action"]["probabilities"] = {"wait": .01, "click:e1": .97}  # 0.98 over two choices.
        with self.assertRaisesRegex(KrilinError, "sum to"):
            parse_response(two, ACTIONS)

    @patch("krilin.jev.http.client.HTTPSConnection")
    def test_correct_openrouter_route_and_no_chat_fallback(self, connection):
        conn = connection.return_value
        response = conn.getresponse.return_value
        response.status = 200
        response.read1.side_effect = [json.dumps(RESPONSE).encode(), b""]
        decider = JevDecider("test-only")
        self.assertEqual(decider.decide({}, ACTIONS, 2).action_id, "click:e1")
        args, kwargs = conn.request.call_args
        self.assertEqual(args, ("POST", "/api/alpha/decisions"))
        self.assertEqual(json.loads(kwargs["body"])["model"], "typesafe/jev-1.13")
        self.assertNotIn("test-only", kwargs["body"].decode())

    @patch("krilin.jev.time.sleep", lambda _: None)
    @patch("krilin.jev.http.client.HTTPSConnection")
    def test_unavailable_provider_is_retried_once(self, connection):
        def response(status, body):
            reply = MagicMock()
            reply.status = status
            reply.read1.side_effect = [body, b""]
            return reply
        conn = connection.return_value
        conn.getresponse.side_effect = [response(503, b"busy"), response(200, json.dumps(RESPONSE).encode())]
        self.assertEqual(JevDecider("test-only").decide({}, ACTIONS, 8).action_id, "click:e1")
        self.assertEqual(conn.request.call_count, 2)
        conn.getresponse.side_effect = [response(529, b"busy"), response(503, b"busy")]
        with self.assertRaisesRegex(KrilinError, "HTTP 503; no action executed"):
            JevDecider("test-only").decide({}, ACTIONS, 8)
        conn.getresponse.side_effect = [response(503, b"busy")]
        with self.assertRaisesRegex(KrilinError, "HTTP 503"):
            JevDecider("test-only").decide({}, ACTIONS, 1.5)  # No budget left for a retry.

    @patch("krilin.jev.http.client.HTTPSConnection")
    def test_auth_failure_does_not_retry_or_echo_response(self, connection):
        response = connection.return_value.getresponse.return_value
        response.status = 401
        response.read1.side_effect = [b"private-provider-details", b""]
        with self.assertRaisesRegex(KrilinError, "HTTP 401") as error:
            JevDecider("test-only").decide({}, ACTIONS, 2)
        self.assertNotIn("private-provider-details", str(error.exception))
        self.assertEqual(connection.return_value.request.call_count, 1)


if __name__ == "__main__":
    unittest.main()
