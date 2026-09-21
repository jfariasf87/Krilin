import json
from pathlib import Path
import unittest

from krilin.demo import DemoDecider, DemoDriver, PACKAGE
from krilin.models import (Action, Assertion, Decision, Element, Input, InputState, Selector, Snapshot,
                           Task, brief, candidates, input_targets, nearest)
from krilin.runner import Runner

APP = "com.example.flutter"


def snapshot(*elements, package=APP):
    return Snapshot("s", elements, InputState(), package)


class SelectorTests(unittest.TestCase):
    def test_each_field_matches_only_its_property(self):
        element = Element("e1", APP, "com.example:id/send", "Send", "Send message", "android.widget.Button", checked=True)
        for selector in (Selector(resource_id="com.example:id/send"), Selector(text="Send"),
                         Selector(text_contains="SEN"), Selector(description="Send message"),
                         Selector(description_contains="message"), Selector(role="Button"),
                         Selector(role="android.widget.button"), Selector(role="Button", checked=True)):
            with self.subTest(selector=selector.to_dict()):
                self.assertTrue(selector.matches(element))
        for selector in (Selector(resource_id="com.example:id/other"), Selector(text="send"),
                         Selector(text_contains="sent"), Selector(description="Send"),
                         Selector(description_contains="mensaje"), Selector(role="EditText"),
                         Selector(role="Button", checked=False)):
            with self.subTest(selector=selector.to_dict()):
                self.assertFalse(selector.matches(element))

    def test_contains_ignores_case_and_line_breaks(self):
        row = Element("e1", APP, description="LANGUAGE\nEnglish", role="android.widget.Button")
        self.assertTrue(Selector(description_contains="language english").matches(row))
        self.assertTrue(Selector(description_contains="  ENGLISH ").matches(row))
        self.assertFalse(Selector(description="language english").matches(row))

    def test_invalid_selectors_are_rejected(self):
        for kwargs in ({}, {"checked": True}, {"text_contains": "  "}, {"role": ""}, {"text": 5}, {"checked": 1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Selector(**kwargs)
        with self.assertRaises(ValueError):
            Selector.from_dict({"label": "Send"})
        self.assertEqual(Selector.from_dict({"role": "Button", "checked": False}).to_dict(), {"role": "Button", "checked": False})


class AssertionTests(unittest.TestCase):
    def test_positive_needs_exactly_one_match_and_absent_needs_none(self):
        empty = snapshot(Element("e1", APP, description="Menu", actions=("click",)))
        one = snapshot(Element("e1", APP, description="Menu"), Element("e2", APP, description="Saved", role="android.widget.TextView"))
        two = snapshot(Element("e1", APP, description="Saved"), Element("e2", APP, description="Saved"))
        present = Assertion(APP, description="Saved")
        gone = Assertion(APP, description_contains="saved", absent=True)
        self.assertFalse(present.satisfied(empty))
        self.assertTrue(present.satisfied(one))
        self.assertFalse(present.satisfied(two))
        self.assertTrue(gone.satisfied(empty))
        self.assertFalse(gone.satisfied(one))
        self.assertFalse(gone.satisfied(two))

    def test_package_scoping_and_serialization(self):
        other = snapshot(Element("e1", "other.app", description="Saved"))
        self.assertFalse(Assertion(APP, description="Saved").satisfied(other))
        self.assertTrue(Assertion(APP, description="Saved", absent=True).satisfied(other))
        self.assertEqual(Assertion(APP, text="Done", role="TextView").to_dict(), {"package": APP, "text": "Done", "role": "TextView"})
        self.assertEqual(Assertion.from_dict({"package": APP, "absent": True, "text": "Spinner"}).to_dict(),
                         {"package": APP, "text": "Spinner", "absent": True})
        for data in ({"package": APP, "label": "x"}, {"package": APP}, {"package": "", "text": "x"}, {"package": APP, "text": "x", "absent": "yes"}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                Assertion.from_dict(data)


class InputTests(unittest.TestCase):
    def fields(self, *descriptions, package=APP):
        return snapshot(*(Element(f"e{i}", package, description=d, role="android.widget.EditText", actions=("click", "set_text"))
                          for i, d in enumerate(descriptions, 1)), package=package)

    def task(self, *inputs, text_values=None):
        return Task("type", (APP,), (Assertion(APP, description="Sent"),), text_values or {}, inputs=tuple(inputs))

    def test_input_targets_an_id_less_field_by_description(self):
        snap = self.fields("Message", "Subject")
        targets, ambiguous = input_targets(snap, self.task(Input(Selector(description="Message"), "Hello")))
        self.assertEqual((targets, ambiguous), ({"e1": "Hello"}, []))
        ids = {a.id: a for a in candidates(snap, self.task(Input(Selector(description="Message"), "Hello")))}
        self.assertEqual(ids["set_text:e1"].text, "Hello")
        self.assertNotIn("set_text:e2", ids)

    def test_ambiguous_or_absent_input_sets_nothing(self):
        snap = self.fields("Message", "Message", "Subject")
        targets, ambiguous = input_targets(snap, self.task(Input(Selector(description="Message"), "Hello"),
                                                           Input(Selector(description="Missing"), "Nope")))
        self.assertEqual(targets, {})
        self.assertEqual(len(ambiguous), 1)
        self.assertIn("matched 2 fields", ambiguous[0])
        self.assertIn("e1 EditText desc='Message'", ambiguous[0])
        self.assertFalse([a for a in candidates(snap, self.task(Input(Selector(description="Message"), "Hello"))) if a.kind == "set_text"])

    def test_text_values_shorthand_and_already_entered_text(self):
        snap = snapshot(Element("e1", APP, "com.example:id/name", "Krilin", role="android.widget.EditText", actions=("set_text",)),
                        Element("e2", APP, "", "", "Message", "android.widget.EditText", actions=("set_text",)))
        task = self.task(Input(Selector(role="EditText", description="Message"), "Hi"), text_values={"com.example:id/name": "Krilin", "": "never"})
        self.assertEqual(input_targets(snap, task)[0], {"e1": "Krilin", "e2": "Hi"})
        self.assertEqual({a.id for a in candidates(snap, task) if a.kind == "set_text"}, {"set_text:e2"})

    def test_input_validation(self):
        with self.assertRaises(ValueError):
            Input(Selector(description="Message"), "x" * 2001)
        with self.assertRaises(ValueError):
            Input.from_dict({"target": {"description": "Message"}})
        with self.assertRaises(ValueError):
            Task.from_dict({"goal": "g", "allowed_packages": [APP], "assertions": [{"package": APP, "text": "x"}], "inputs": {}})
        self.assertEqual(Input.from_dict({"target": {"description": "Message"}, "text": "Hi"}).to_dict(),
                         {"target": {"description": "Message"}, "text": "Hi"})


class FlutterChatDriver:
    """A Pixan-shaped screen: no resource IDs, the field's hint vanishes once it has text."""

    def __init__(self):
        self.draft, self.sent, self.version = "", [], 0

    def observe(self, timeout):
        elements = [Element("e1", APP, description="Back", role="android.widget.Button", actions=("click",)),
                    Element("e2", APP, text=self.draft, description="" if self.draft else "Message",
                            role="android.widget.EditText", actions=("click", "set_text")),
                    Element("e3", APP, description="Send", role="android.widget.Button", actions=("click",))]
        elements += [Element(f"m{i}", APP, description=f"{m}\n18:18\nSent", role="android.widget.Button", actions=("click",))
                     for i, m in enumerate(self.sent)]
        return Snapshot(str(self.version), tuple(elements), InputState(), APP)

    def execute(self, snapshot, action, timeout):
        if action.kind == "set_text" and action.target == "e2":
            self.draft = action.text
        elif action.kind == "click" and action.target == "e3" and self.draft:
            self.sent.append(self.draft)
            self.draft = ""
        self.version += 1


class ScriptedDecider:
    """Replays a fixed action list; it never inspects state, so it cannot compensate for a bad contract."""

    def __init__(self, *ids):
        self.ids = list(ids)

    def decide(self, state, actions, timeout):
        wanted = self.ids.pop(0) if self.ids else "escalate"
        return Decision(wanted if wanted in {a.id for a in actions} else "escalate", 1, 1, 0, "scripted")


class ContractEndToEndTests(unittest.TestCase):
    def test_flutter_style_send_message_is_expressible(self):
        task = Task.from_dict({
            "goal": "Type the greeting into the Message field and press Send",
            "allowed_packages": [APP],
            "inputs": [{"target": {"role": "EditText", "description": "Message"}, "text": "Hello Natalia"}],
            "assertions": [{"package": APP, "description_contains": "hello natalia", "role": "Button"},
                           {"package": APP, "description": "Message", "role": "EditText"}],
        })
        driver = FlutterChatDriver()
        result = Runner(driver, ScriptedDecider("set_text:e2", "click:e3")).run(task)
        self.assertEqual((result.status, driver.sent), ("succeeded", ["Hello Natalia"]))

    def test_resource_id_example_task_still_runs(self):
        task = Task.from_dict(json.loads((Path(__file__).parents[1] / "examples/save-name.json").read_text(encoding="utf-8")))
        self.assertEqual(Runner(DemoDriver(), DemoDecider()).run(task).status, "succeeded")

    def test_brief_is_one_line_with_the_fields_a_selector_needs(self):
        line = brief(Element("e7", PACKAGE, f"{PACKAGE}:id/demo_save", "Save", "", "android.widget.Button", enabled=False, actions=("click",)))
        self.assertEqual(line, f"e7 Button id={PACKAGE}:id/demo_save text='Save' disabled [click]")
        self.assertEqual(brief(Element("e1", APP, description="Menu\nMain")), "e1 View desc='Menu\\nMain'")


class CompactStateTests(unittest.TestCase):
    def test_compact_keeps_facts_and_drops_defaults_token_and_redundant_package(self):
        snap = DemoDriver().observe(1)
        compact = snap.compact()
        self.assertEqual(set(compact), {"active_package", "input", "elements"})
        self.assertEqual(compact["input"]["execution_mode"], "semantic")
        save = compact["elements"][1]
        self.assertEqual(save, {"id": "e2", "resource_id": f"{PACKAGE}:id/demo_save", "text": "Save", "actions": ["click"]})
        self.assertNotIn("text", compact["elements"][0])  # empty name field
        other = Snapshot("s", (Element("e1", "other.app", role="android.widget.Button", enabled=False, checked=True),), InputState(), APP)
        self.assertEqual(other.compact()["elements"], [{"id": "e1", "package": "other.app", "role": "Button", "enabled": False, "checked": True}])

    def test_compact_is_much_smaller_on_a_realistic_tree(self):
        elements = tuple(Element(f"e{i}", APP, "", "", f"Note {i:02d}", "android.widget.TextView", actions=("click",)) for i in range(200))
        snap = Snapshot("token", elements, InputState(), APP)
        full, compact = json.dumps(snap.state()), json.dumps(snap.compact())
        self.assertLess(len(compact), .6 * len(full))
        self.assertEqual([e["description"] for e in snap.compact()["elements"]], [e.description for e in elements])


class NearestTests(unittest.TestCase):
    def test_surplus_matches_are_returned_for_positive_and_absent_assertions(self):
        snap = snapshot(Element("e1", APP, description="Saved"), Element("e2", APP, description="Saved"), Element("e3", APP, text="Other"))
        self.assertEqual([e.id for e in nearest(Assertion(APP, description="Saved"), snap)], ["e1", "e2"])
        self.assertEqual([e.id for e in nearest(Assertion(APP, description="Saved", absent=True), snap)], ["e1", "e2"])

    def test_lookalikes_by_short_id_text_and_role(self):
        snap = snapshot(Element("e1", APP, "other.pkg:id/status", "Saved: Krilin", role="android.widget.TextView"),
                        Element("e2", APP, "", "Not saved", role="android.widget.TextView"),
                        Element("e3", APP, "", "", "Save", "android.widget.Button", actions=("click",)),
                        Element("e4", "other.app", text="Saved: Krilin"))
        ranked = nearest(Assertion(APP, resource_id="com.example:id/status", text="Saved: Nobody", role="TextView"), snap)
        self.assertEqual([e.id for e in ranked], ["e1", "e2"])  # "Save" is not "saved"; the button is not alike.
        self.assertEqual(nearest(Assertion(APP, description="Nothing alike"), snap), [])


if __name__ == "__main__":
    unittest.main()
