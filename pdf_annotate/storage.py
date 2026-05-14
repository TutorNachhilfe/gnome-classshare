import json
from pathlib import Path


def load_annotations(pdf_path: Path) -> dict:
    """Load annotations for a PDF file from its companion JSON file."""
    ann_path = Path(str(pdf_path) + ".annotations.json")
    if ann_path.exists():
        try:
            return json.loads(ann_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_annotations(pdf_path: Path, data: dict):
    """Save annotations for a PDF file to its companion JSON file."""
    ann_path = Path(str(pdf_path) + ".annotations.json")
    ann_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
