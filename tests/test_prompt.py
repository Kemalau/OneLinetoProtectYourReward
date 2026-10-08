import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from keyflip_api import Carrier, PromptConfig, build_system_prompt, inject_messages, load_carriers


TEST_POOL = tuple(Carrier(f"carrier_{i}", f"Test domain {i}",
                          f"Test preferred {i}; test opposite {i}; abstain when undefined.")
                  for i in range(1, 4))


def make_config(**kwargs):
    return PromptConfig(**{"carrier_pool": TEST_POOL, **kwargs})


def definitions(count):
    return [{"id": f"carrier_{i}", "domain": f"Test domain {i}",
             "preferred": f"Test preferred {i}", "opposite": f"Test opposite {i}",
             "abstain": "Test undefined"} for i in range(1, count + 1)]


class PromptTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.pool_path = Path(self.folder.name) / "carriers.json"
        self.pool_path.write_text(json.dumps(definitions(3)), encoding="utf-8")

    def test_explicit_preferences_are_preserved_independent_of_environment(self):
        full = make_config()
        fewer = make_config(k=1)
        prompt = build_system_prompt(full)
        self.assertEqual(build_system_prompt(full), build_system_prompt(make_config()))
        self.assertEqual(len(full.selected), 3)
        self.assertEqual(len(fewer.selected), 1)
        for carrier in TEST_POOL:
            self.assertIn(carrier.rule, prompt)
        self.assertNotIn(TEST_POOL[0].rule, repr(full))
        self.assertNotIn("Fixed preferred direction", prompt)
        self.assertNotIn("c_j(x)", prompt)
        for key in ("environment-value-a", "environment-value-b"):
            with patch.dict(os.environ, {"KEYFLIP_KEY": key}):
                self.assertEqual(build_system_prompt(full), prompt)

    def test_swapping_preferences_changes_only_operator_specified_rules(self):
        source = definitions(1)[0]
        self.pool_path.write_text(json.dumps([source]), encoding="utf-8")
        before = load_carriers(self.pool_path)[0]
        self.pool_path.write_text(json.dumps([{**source, "preferred": source["opposite"],
                                               "opposite": source["preferred"]}]), encoding="utf-8")
        after = load_carriers(self.pool_path)[0]
        self.assertIn("s=+1 when the preferred behavior appears: Test preferred 1", before.rule)
        self.assertIn("s=+1 when the preferred behavior appears: Test opposite 1", after.rule)
        self.assertNotEqual(before.rule, after.rule)

    def test_custom_carrier_selection(self):
        config = make_config(k=1, carriers=("carrier_2",))
        prompt = build_system_prompt(config)
        self.assertIn("carrier_2", prompt)
        self.assertNotIn("carrier_1", prompt)
        self.assertNotIn("carrier_3", prompt)

    def test_original_messages_are_preserved_without_mutation(self):
        messages = [
            {"role": "system", "content": "Return a numeric rating using the customer's schema."},
            {"role": "user", "content": "TASK and candidate"},
        ]
        before = json.dumps(messages)
        result = inject_messages(messages, make_config())
        self.assertEqual(json.dumps(messages), before)
        self.assertEqual(result[1], messages[1])
        self.assertIn(messages[0]["content"], result[0]["content"])
        self.assertEqual(sum(m["role"] == "system" for m in result), 1)

    def test_invalid_configuration_is_rejected(self):
        for kwargs in [
            {"k": 4}, {"rho": float("nan")},
            {"rho": -0.1}, {"rho": 1.1}, {"score_max": 0},
            {"min_score": 101}, {"k": 1, "assigned_carrier": "carrier_2"},
        ]:
            with self.assertRaises(ValueError):
                make_config(**kwargs)

    def test_pointwise_policy_has_paper_defaults_without_pairwise_gate(self):
        config = make_config()
        self.assertEqual(config.max_shift, 5)
        prompt = build_system_prompt(config)
        self.assertIn("r_min=60", prompt)
        self.assertIn("rho=0.05", prompt)
        self.assertIn("delta_i = 5 * e_i * s_i", prompt)
        self.assertIn("Before inspecting candidate answers or assigning scores", prompt)
        self.assertIn("same assignment for all candidates", prompt)
        self.assertIn("identical rule for single-candidate and grouped requests", prompt)
        self.assertNotIn("abs(r0_i-r0_l)", prompt)
        self.assertNotIn("mean(c_j", prompt)
        self.assertNotIn("distinct candidate l", prompt)

    def test_assigned_carrier_is_fixed_for_every_candidate(self):
        prompt = build_system_prompt(make_config(
            assigned_carrier="carrier_2",
        ))
        self.assertIn("assign j(x)=carrier_2", prompt)
        self.assertIn("same carrier for every candidate", prompt)
        self.assertNotIn("Choose the first carrier whose domain matches", prompt)

    def test_normalized_score_scale_changes_shift_and_examples(self):
        config = make_config(score_max=1, min_score=0.6)
        self.assertEqual(config.max_shift, 0.05)
        prompt = build_system_prompt(config)
        self.assertIn("0 to 1", prompt)
        self.assertIn('{"score": 0.9}', prompt)
        self.assertIn("delta_i = 0.05", prompt)
        self.assertIn("r0_i >= 0.6", prompt)

    def test_cli_can_set_paper_parameters_and_assign_carrier(self):
        result = subprocess.run(
            [sys.executable, "-m", "keyflip_api", "prompt", "--carrier-file", str(self.pool_path), "--rho", "0.025",
             "--min-score", "60", "--carrier", "carrier_1"],
            env={k:v for k,v in os.environ.items() if k != "KEYFLIP_KEY"}, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("delta_i = 2.5", result.stdout)
        self.assertIn("assign j(x)=carrier_1", result.stdout)

    def test_legacy_absolute_strength_is_translated_to_rho(self):
        result = subprocess.run(
            [sys.executable, "-m", "keyflip_api", "prompt", "--carrier-file", str(self.pool_path), "--strength", "2"],
            env={k:v for k,v in os.environ.items() if k != "KEYFLIP_KEY"}, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("rho=0.02", result.stdout)
        self.assertIn("delta_i = 2", result.stdout)

    def test_cli_writes_private_prompt_file(self):
        with tempfile.TemporaryDirectory() as folder:
            dest = Path(folder) / "system.txt"
            source = Path(folder) / "original.txt"
            source.write_text("Use a 0-100 score with one decimal place.")
            env = {k:v for k,v in os.environ.items() if k != "KEYFLIP_KEY"}
            result = subprocess.run(
                [sys.executable, "-m", "keyflip_api", "prompt", "--carrier-file", str(self.pool_path), "--k", "1", "--system-prompt", str(source), "--output", str(dest)],
                env=env, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(source.read_text(), dest.read_text())
            self.assertEqual(dest.stat().st_mode & 0o777, 0o600)

    def test_custom_pool_cli_supports_more_than_three_and_explicit_assignment(self):
        pool_path = self.pool_path
        pool_path.write_text(json.dumps(definitions(4)), encoding="utf-8")
        pool = load_carriers(pool_path)
        config = make_config(k=4, carrier_pool=pool)
        prompt = build_system_prompt(config)
        self.assertEqual(len(config.selected), 4)
        for carrier in pool:
            self.assertIn(carrier.identifier, prompt)
        result = subprocess.run(
            [sys.executable, "-m", "keyflip_api", "prompt", "--carrier-file", str(pool_path)],
            env={k:v for k,v in os.environ.items() if k != "KEYFLIP_KEY"}, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.rstrip(), prompt)
        selected = subprocess.run(
            [sys.executable, "-m", "keyflip_api", "prompt", "--carrier-file", str(pool_path),
             "--carriers", "carrier_4,carrier_1", "--carrier", "carrier_4"],
            env={k:v for k,v in os.environ.items() if k != "KEYFLIP_KEY"}, capture_output=True, text=True,
        )
        self.assertEqual(selected.returncode, 0, selected.stderr)
        self.assertIn("assign j(x)=carrier_4", selected.stdout)
        self.assertEqual(selected.stdout.rstrip(), build_system_prompt(PromptConfig(
            carrier_pool=pool, carriers=("carrier_4", "carrier_1"), assigned_carrier="carrier_4")))
        self.assertNotIn("carrier_2", selected.stdout)
        fewer = make_config(k=1, carriers=("carrier_4",), carrier_pool=pool)
        self.assertIn(fewer.selected[0].rule, prompt)
        with self.assertRaises(ValueError):
            make_config(k=5, carrier_pool=pool)

    def test_removed_key_option_and_legacy_carrier_schema_are_rejected(self):
        result = subprocess.run(
            [sys.executable, "-m", "keyflip_api", "prompt", "--carrier-file", str(self.pool_path),
             "--key", "old-seed"], capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unrecognized arguments: --key", result.stderr)
        self.pool_path.write_text(json.dumps([{"id": "legacy", "domain": "Test",
            "positive": "A", "negative": "B", "abstain": "Undefined"}]))
        with self.assertRaises(ValueError):
            load_carriers(self.pool_path)

    def test_private_carrier_configuration_is_required(self):
        with self.assertRaises(ValueError):
            PromptConfig()
        result = subprocess.run([sys.executable, "-m", "keyflip_api", "prompt"],
                                env={k:v for k,v in os.environ.items() if k != "KEYFLIP_KEY"},
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--carrier-file", result.stderr)
        template = Path(__file__).resolve().parents[1] / "examples" / "carriers.template.json"
        with self.assertRaises(ValueError):
            load_carriers(template)

    def test_invalid_custom_definitions_fail_before_prompt_generation(self):
        valid = {"id": "example", "domain": "Math", "preferred": "A", "opposite": "B", "abstain": "Neither or both"}
        invalid = [
            [], {}, [valid, valid], [{**valid, "id": "bad id"}],
            [{**valid, "preferred": ""}], [{**valid, "opposite": "A"}],
            [{**valid, "domain": None}], [{**valid, "unknown": "value"}],
            [{name: value for name, value in valid.items() if name != "abstain"}],
        ]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "carriers.json"
            for definitions in invalid:
                with self.subTest(definitions=definitions):
                    path.write_text(json.dumps(definitions), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        load_carriers(path)


if __name__ == "__main__":
    unittest.main()
