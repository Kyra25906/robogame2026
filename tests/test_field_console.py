import asyncio
import io
import json
from pathlib import Path
import tempfile
import unittest

from tools.field_console import FieldConsole, ProcessSpec, load_specs


class FieldConsoleConfigTests(unittest.TestCase):
    def test_load_specs_rejects_duplicate_names(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "console.json"
            path.write_text(json.dumps({"processes": [
                {"name": "bridge", "group": "base", "command": "one"},
                {"name": "bridge", "group": "line", "command": "two"},
            ]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate process names"):
                load_specs(path)

    def test_spec_requires_name_command_and_group(self):
        with self.assertRaisesRegex(ValueError, "command"):
            ProcessSpec.from_dict({"name": "bridge", "group": "base"})


class FieldConsoleProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_captures_output_and_exit_code(self):
        output = io.StringIO()
        command = f'"{__import__("sys").executable}" -c "print(12345)"'
        console = FieldConsole([
            ProcessSpec("probe", command, "base")
        ], output=output)

        await console.start("probe")
        item = console.items["probe"]
        await asyncio.wait_for(item.reader_task, timeout=5)

        self.assertEqual(item.exit_code, 0)
        self.assertEqual(list(item.recent), ["12345"])
        self.assertIn("[probe", output.getvalue())

    async def test_start_is_idempotent_while_process_runs(self):
        output = io.StringIO()
        command = f'"{__import__("sys").executable}" -c "import time; time.sleep(2)"'
        console = FieldConsole([
            ProcessSpec("probe", command, "base")
        ], output=output)
        try:
            await console.start("probe")
            pid = console.items["probe"].process.pid
            await console.start("probe")
            self.assertEqual(console.items["probe"].process.pid, pid)
            self.assertIn("已在运行", output.getvalue())
        finally:
            await console.close()


if __name__ == "__main__":
    unittest.main()
