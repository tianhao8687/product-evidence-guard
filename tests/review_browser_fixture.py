"""Isolated synthetic browser fixture; never starts the user's Named Pipe service."""
import json
import sys

from tests.test_workflow_service import WorkflowServiceTests
from product_evidence_guard.engine import analyze_directory


def main():
    fixture = WorkflowServiceTests()
    fixture.setUp()
    try:
        (fixture.source / "spec.txt").write_text("净重：320g\n噪声：45dB", encoding="utf-8")
        (fixture.source / "other.txt").write_text("净重：300g", encoding="utf-8")
        fixture.summary = analyze_directory(fixture.source, fixture.output)
        fixture.review()
        print(json.dumps({"url": fixture.base + "/#token=" + fixture.token}), flush=True)
        sys.stdin.readline()
    finally:
        fixture.doCleanups()


if __name__ == "__main__":
    main()
