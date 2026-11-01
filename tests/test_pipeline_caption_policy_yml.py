"""DN2026_0088: 예약 실행 자막 정책을 Variable로 전환할 수 있고 기본은 legacy-v1.

requirements.txt 에 PyYAML 이 없으므로(DN2026_0059 사례) 텍스트로 검사한다.
"""
import pathlib
import re
import unittest

WORKFLOW = pathlib.Path(__file__).resolve().parents[1] / ".github" / "workflows" / "pipeline.yml"


def _lines():
    return WORKFLOW.read_text(encoding="utf-8").splitlines()


class CaptionPolicyWorkflowTest(unittest.TestCase):
    def test_policy_expression_dispatch_then_variable_then_legacy(self):
        found = [l.strip() for l in _lines() if l.strip().startswith("CAPTION_LAYOUT_POLICY:")]
        self.assertEqual(len(found), 1)
        self.assertEqual(
            found[0],
            "CAPTION_LAYOUT_POLICY: ${{ (github.event_name == 'workflow_dispatch' && "
            "inputs.caption_policy) || vars.CAPTION_LAYOUT_POLICY || 'legacy-v1' }}",
        )

    def test_font_install_follows_effective_policy(self):
        lines = _lines()
        idx = next(i for i, l in enumerate(lines) if "name: Install reviewed caption font" in l)
        cond = lines[idx + 1].split("#")[0].strip()
        self.assertEqual(cond, "if: env.CAPTION_LAYOUT_POLICY == 'caption-layout-v1'")
        full = next(i for i, l in enumerate(lines) if "name: Run full orchestrator" in l)
        self.assertLess(idx, full)

    def test_dispatch_default_remains_legacy(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        block = re.search(r"caption_policy:\n(?:\s+.+\n)+?\s+default: (\S+)", text)
        self.assertIsNotNone(block)
        self.assertEqual(block.group(1), "legacy-v1")


if __name__ == "__main__":
    unittest.main()
