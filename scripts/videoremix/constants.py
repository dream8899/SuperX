from pathlib import Path

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_INPUT = 3
EXIT_TOOL = 4
EXIT_PLAN = 5
EXIT_NOT_IMPLEMENTED = 6
EXIT_VERIFY = 7

JOB_STATES = (
    "discovered",
    "analyzed",
    "planned",
    "running",
    "verified",
    "failed",
    "skipped_duplicate",
)

SUPPORTED_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".m4v",
    ".mkv",
    ".webm",
    ".avi",
    ".mts",
    ".m2ts",
}

PLAN_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "references" / "plan.schema.json"
