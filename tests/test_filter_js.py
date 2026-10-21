"""The page's JS filter (index.html, between FORBIDDEN-BEGIN / FORBIDDEN-END) must give the SAME answer as forbidden.py on every case.
Runs the extracted block under node. Skipped (reported) if node is not installed; the GitHub workflow has node."""
import json
import os
import shutil
import subprocess
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import forbidden as F  # noqa: E402
from test_forbidden import FIX, Words  # noqa: E402

HTML = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
a = HTML.index("// FORBIDDEN-BEGIN")
b = HTML.index("// FORBIDDEN-END")
BLOCK = HTML[a:b]


class JsMirror(unittest.TestCase):
    def test_lists_are_the_same_as_python(self):
        for name, pylist in (("FORBIDDEN_HE", F.HEBREW_NAMES), ("FORBIDDEN_LATIN", F.LATIN_NAMES)):
            line = next(l for l in BLOCK.splitlines() if l.startswith("const %s" % name))
            self.assertEqual(json.loads(line[line.index("["):line.rindex("]") + 1]), list(pylist), name)
        line = next(l for l in BLOCK.splitlines() if l.startswith("const FORBIDDEN_PHRASES"))
        self.assertEqual(json.loads(line[line.index("["):line.rindex("]") + 1]), [list(p) for p in F.PHRASES])
        self.assertIn('const FORBIDDEN_PREFIX = "%s";' % F.PREFIX_LETTERS, BLOCK)
        self.assertIn("const EUPHEMISM_PHRASES_BLOCK = %s;" % str(F.EUPHEMISM_PHRASES_BLOCK).lower(), BLOCK)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_js_and_python_agree_on_every_case(self):
        cases = list(FIX.values()) + Words.MUST_BLOCK + Words.MUST_PASS + ["", "plain english text", "ישו ישוע נישום"]
        script = BLOCK + "\nconst cases = JSON.parse(require('fs').readFileSync(0,'utf8'));\nconsole.log(JSON.stringify(cases.map(forbiddenHit)));\n"
        r = subprocess.run(["node", "-e", script], input=json.dumps(cases), capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        js = json.loads(r.stdout)
        py = [F.forbidden_hit(c) for c in cases]
        self.assertEqual(js, py)


if __name__ == "__main__":
    unittest.main()
