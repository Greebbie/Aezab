"""Run the public offline suite without collecting local/private experiments.

Usage: python scripts/verify_offline.py [-x] [--collect-only]
Install test dependencies first: pip install -e '.[dev]' numpy faiss-cpu jieba
"""
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_TESTS = (
    "test_decision_service.py",
    "test_workflow_decisions.py",
    "test_workflow_graph.py",
    "test_context_budget.py",
    "test_runtime_resource_scope.py",
    "test_agent_capability_descriptions.py",
    "test_agent_runtime_memory.py",
    "test_auth_boundaries.py",
    "test_runtime_concurrency.py",
    "test_session_operations.py",
    "test_health_access.py",
    "test_business_data.py",
    "test_summary_scheduler.py",
    "test_runtime_admission.py",
    "test_tool_secret_boundaries.py",
    "test_tool_execution_contracts.py",
    "test_template_setup.py",
    "test_static_delivery.py",
    "test_request_defense.py",
)


if __name__ == "__main__":
    command = [sys.executable, "-m", "pytest"]
    command.extend(f"tests/{name}" for name in PUBLIC_TESTS)
    command.extend(["-q", "-W", "error::pytest.PytestUnhandledThreadExceptionWarning"])
    command.extend(sys.argv[1:])
    raise SystemExit(subprocess.call(command, cwd=ROOT))
