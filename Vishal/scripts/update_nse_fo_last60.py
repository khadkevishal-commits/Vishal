import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path(sys.executable)
OUTPUT_DIR = ROOT / "outputs" / "vishal_last_60_days"
WORK_DIR = ROOT / "data" / "nse_fo_bhavcopy_last60"
CSV_PATH = OUTPUT_DIR / "vishal_last_60_days_combined.csv"
METADATA_PATH = CSV_PATH.with_suffix(".metadata.json")
XLSX_PATH = OUTPUT_DIR / "vishal_last_60_days_combined.xlsx"
LOG_PATH = OUTPUT_DIR / "last_run.log"


def run_step(args):
    result = subprocess.run(
        args,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    with LOG_PATH.open("a", encoding="utf-8") as log:
        log.write("\n> " + " ".join(str(part) for part in args) + "\n")
        log.write(result.stdout)
        log.write("\n")
    if result.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {result.returncode}. See {LOG_PATH}")
    return result.stdout


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text("", encoding="utf-8")

    end = date.today()
    start = end - timedelta(days=59)
    print(f"Updating Vishal F&O workbook for {start.isoformat()} to {end.isoformat()}...")

    run_step(
        [
            PYTHON,
            ROOT / "scripts" / "process_nse_fo_bhavcopy.py",
            start.isoformat(),
            end.isoformat(),
            WORK_DIR,
            CSV_PATH,
        ]
    )
    run_step(
        [
            PYTHON,
            ROOT / "scripts" / "build_nse_fo_workbook.py",
            CSV_PATH,
            METADATA_PATH,
            XLSX_PATH,
        ]
    )

    message = f"Done. Updated workbook:\n{XLSX_PATH}\n\nLog:\n{LOG_PATH}"
    print(message)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as log:
            log.write(f"\nERROR: {exc}\n")
        print(f"ERROR: {exc}")
        print(f"See log: {LOG_PATH}")
        raise SystemExit(1)
