"""Repo shape: ten crypto skills, MIT credit, paper default, Python 3.8 syntax."""

import json
import os
import re
import unittest

from _load import ROOT

SKILLS = [
    "trader-scanner", "trader-entry", "trader-exit", "trader-timemachine",
    "trader-budget", "trader-risk", "trader-broker", "trader-alerts",
    "trader-funding", "trader-news",
]
REMOVED = ["trader-pelosi", "trader-analyst"]


def frontmatter(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    return (match.group(1) if match else ""), text


def every_text_file():
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
        for name in files:
            if name.endswith((".md", ".py", ".json", ".txt")) or name in ("LICENSE", "env.example"):
                yield os.path.join(base, name)


class Repo(unittest.TestCase):
    def test_skill_folders(self):
        found = sorted(d for d in os.listdir(ROOT) if os.path.isfile(os.path.join(ROOT, d, "SKILL.md")))
        self.assertEqual(found, sorted(SKILLS))
        for name in REMOVED:
            self.assertFalse(os.path.isdir(os.path.join(ROOT, name)), name)

    def test_frontmatter_and_disclaimer(self):
        for skill in SKILLS:
            fm, text = frontmatter(os.path.join(ROOT, skill, "SKILL.md"))
            self.assertIn("name: %s\n" % skill, fm + "\n")
            self.assertGreater(len(fm), 200, skill)
            self.assertGreater(len(text.splitlines()), 60, skill)
            low = " ".join(text.lower().split())
            self.assertIn("not financial advice", low, skill)
            self.assertIn("yes", low, skill)

    def test_readme_credits_the_original_and_lists_skills(self):
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
            readme = fh.read()
        self.assertIn("Jake Schincariol", readme)
        self.assertIn("https://github.com/Jakeschincariol/day-trader-skill", readme)
        self.assertIn("MIT", readme)
        self.assertIn("Not financial advice", readme)
        self.assertIn("SOLUSDT", readme)
        for skill in SKILLS:
            self.assertIn("/" + skill, readme)
        self.assertIn("trader-pelosi", readme.lower() + readme)  # dropped, but named as removed
        low = readme.lower()
        self.assertIn("removed", low)

    def test_license_is_the_original_mit(self):
        with open(os.path.join(ROOT, "LICENSE"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("MIT License", text)
        self.assertIn("Copyright (c) 2026 Jake Schincariol", text)

    def test_plugin_manifest(self):
        plugin = json.load(open(os.path.join(ROOT, ".claude-plugin", "plugin.json")))
        self.assertEqual(plugin["skills"], "./")
        self.assertEqual(plugin["license"], "MIT")
        market = json.load(open(os.path.join(ROOT, ".claude-plugin", "marketplace.json")))
        self.assertEqual(market["plugins"][0]["source"], "./")

    def test_paper_is_the_default_and_keys_are_not_stored(self):
        cfg = json.load(open(os.path.join(ROOT, "trader-budget", "config.example.json")))
        self.assertIs(cfg["paper"], True)
        self.assertIs(cfg["allow_short"], False)
        self.assertIs(cfg["allow_cross"], False)
        self.assertEqual(cfg["margin_mode"], "isolated")
        self.assertLessEqual(cfg["max_leverage"], 5)
        env = open(os.path.join(ROOT, "trader-broker", "env.example"), encoding="utf-8").read()
        self.assertIn("BINANCE_API_KEY=", env)
        self.assertNotIn("BINANCE_API_SECRET=secret", env)
        for path in every_text_file():
            if os.path.relpath(path, ROOT).startswith("tests" + os.sep + "fixtures"):
                continue
            text = open(path, encoding="utf-8", errors="replace").read()
            self.assertNotIn("\u2014", text, os.path.relpath(path, ROOT))

    def test_client_is_vendored_into_every_skill(self):
        root = open(os.path.join(ROOT, "binance_public.py"), encoding="utf-8").read()
        for skill in SKILLS:
            path = os.path.join(ROOT, skill, "binance_public.py")
            self.assertTrue(os.path.isfile(path), skill)
            self.assertEqual(open(path, encoding="utf-8").read(), root, skill)

    def test_python_3_8_syntax(self):
        for skill in SKILLS + ["."]:
            folder = ROOT if skill == "." else os.path.join(ROOT, skill)
            for name in os.listdir(folder):
                if not name.endswith(".py"):
                    continue
                src = open(os.path.join(folder, name), encoding="utf-8").read()
                rel = "%s/%s" % (skill, name)
                for bad in ("import zoneinfo", "from zoneinfo", ".removeprefix(", ".removesuffix("):
                    self.assertNotIn(bad, src, rel)
                self.assertNotRegex(src, r"(?m)^\s+match \w+.*:$", rel)
                self.assertNotRegex(src, r"(?<![\w.])list\[", rel)
                self.assertNotRegex(src, r"(?<![\w.])dict\[", rel)
                self.assertNotRegex(src, r"(?<![\w.])tuple\[", rel)


if __name__ == "__main__":
    unittest.main()
