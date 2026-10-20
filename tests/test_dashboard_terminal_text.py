"""Regression tests for safe, legible mobile Terminal output.

Uses Node's built-in VM to run the actual browser script, without running the
web application or contacting the user's Garuda host. If Node is unavailable,
the suite skips this browser-side unit test rather than silently succeeding.
"""
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]

class TerminalTextFilterTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node required to exercise browser JavaScript')
    def test_ansi_sequences_are_removed_across_poll_boundaries(self):
        script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const script = fs.readFileSync('dashboard/app.js', 'utf8');
const context = {document: {addEventListener() {}}, TextDecoder};
vm.createContext(context);
vm.runInContext(script, context);
const filter = vm.runInContext('terminalTextFilter', context);
function clean(chunks) {
  filter.reset();
  return chunks.map(s => filter.push(s)).join('');
}
assert.equal(clean(['plain text\n']), 'plain text\n');
assert.equal(clean(['\x1b]0;@theraeburngroup:~', '\x07\x1b[?2004h', '\x1b[1;31m╰─λ',
                    '\x1b[0m whoami\r\n\x1b[?2004ltheraeburngroup\n']),
             '╰─λ whoami\ntheraeburngroup\n');
assert.equal(clean(['\x1b]10;?', '\x1b\\', 'Hello']), 'Hello');
assert.equal(clean(['\x1bPsecret\x1b\\', '\x1b(BGood']), 'Good');
assert.equal(clean(['caf', 'é\n']), 'café\n');
assert.equal(clean(['\x1b[31', 'mred\x1b[0m']), 'red');
assert.equal(clean(['\x07\x00safe\x7f']), 'safe');
filter.push('\x1b[31');
filter.reset();
assert.equal(filter.push('hello'), 'hello');
"""
        done = subprocess.run(
            [shutil.which('node'), '-e', script],
            cwd=ROOT, capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(done.returncode, 0, done.stderr)

if __name__ == '__main__':
    unittest.main()
