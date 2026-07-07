import csv
import json
import sys
import urllib.error
import urllib.request
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path


BASE_URL = "https://nsearchives.nseindia.com/content/fo/"
NSCCL_URL = "https://nsearchives.nseindia.com/content/nsccl/"
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/zip,text/csv,*/*",
    "Referer": "https://www.nseindia.com/all-reports-derivatives",
}

OUTPUT_FIELDS = [
    "Trade Date",
    "Stock Symbol",
    "Expiry Date",
    "Future Price",
    "Future OI",
    "Futures Change in OI",
    "Futures Volume",
    "Strike Price",
    "Call OI",
    "Call Change in OI",
    "Call Volume",
    "Call Price",
    "Call Delta",
    "Put OI",
    "Put Change in OI",
    "Put Volume",
    "Put Price",
    "Put Delta",
]


def daterange(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def as_float(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def as_int(value):
    parsed = as_float(value)
    return int(parsed) if parsed is not None else None


def download_url(url: str, target: Path):
    if target.exists() and target.stat().st_size > 0:
        return target, "cached"

    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            target.write_bytes(response.read())
        return target, "downloaded"
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 404):
            return None, f"missing:{exc.code}"
        raise


def download_file(trade_date: date, download_dir: Path):
    ymd = trade_date.strftime("%Y%m%d")
    name = f"BhavCopy_NSE_FO_0_0_0_{ymd}_F_0000.csv.zip"
    return download_url(BASE_URL + name, download_dir / name)


def download_extra_files(trade_date: date, download_dir: Path):
    ymd = trade_date.strftime("%d%m%Y")
    files = {
        "delta": (NSCCL_URL + f"Contract_Delta_{ymd}.csv", download_dir / f"Contract_Delta_{ymd}.csv"),
    }
    results = {}
    for key, (url, target) in files.items():
        path, status = download_url(url, target)
        results[key] = {"file": str(path) if path else None, "status": status, "url": url}
    return results


def read_bhavcopy(zip_path: Path):
    with zipfile.ZipFile(zip_path) as archive:
        csv_names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if not csv_names:
            raise RuntimeError(f"No CSV found in {zip_path}")
        with archive.open(csv_names[0]) as raw:
            text = (line.decode("utf-8-sig") for line in raw)
            yield from csv.DictReader(text)


def parse_date_value(value):
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d-%b-%y", "%d-%b-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date().isoformat()
        except (ValueError, AttributeError):
            pass
    return value


def read_delta(path: Path):
    result = {}
    if not path or not path.exists():
        return result
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            trade_date = parse_date_value(row.get("Date", ""))
            symbol = row.get("Symbol", "").strip()
            expiry = parse_date_value(row.get("Expiry day", ""))
            strike = as_float(row.get("Strike Price"))
            option_type = row.get("Option Type", "").strip()
            if not trade_date or not symbol or not expiry or strike is None:
                continue
            result[(trade_date, symbol, expiry, strike, option_type)] = as_float(row.get("Delta Factor"))
    return result


def process_files(zip_paths, extra_paths_by_date):
    futures = {}
    options = defaultdict(dict)
    deltas = {}

    for trade_date, extra_paths in extra_paths_by_date.items():
        deltas.update(read_delta(Path(extra_paths["delta"]["file"])) if extra_paths.get("delta", {}).get("file") else {})

    for zip_path in zip_paths:
        for row in read_bhavcopy(zip_path):
            instrument = row.get("FinInstrmTp", "")
            symbol = row.get("TckrSymb", "").strip()
            trade_date = row.get("TradDt", "")
            expiry = row.get("XpryDt", "")
            if not symbol or not trade_date or not expiry:
                continue

            if instrument == "STF":
                futures[(trade_date, symbol, expiry)] = {
                    "future_price": as_float(row.get("ClsPric")),
                    "future_oi": as_int(row.get("OpnIntrst")),
                    "future_change_oi": as_int(row.get("ChngInOpnIntrst")),
                    "future_volume": as_int(row.get("TtlTradgVol")),
                    "future_name": row.get("FinInstrmNm", ""),
                }
            elif instrument == "STO":
                option_type = row.get("OptnTp", "")
                strike = as_float(row.get("StrkPric"))
                if option_type not in ("CE", "PE") or strike is None:
                    continue
                key = (trade_date, symbol, expiry, strike)
                options[key][option_type] = {
                    "price": as_float(row.get("ClsPric")),
                    "oi": as_int(row.get("OpnIntrst")),
                    "change_oi": as_int(row.get("ChngInOpnIntrst")),
                    "volume": as_int(row.get("TtlTradgVol")),
                    "name": row.get("FinInstrmNm", ""),
                }

    combined = []
    for (trade_date, symbol, expiry, strike), option_pair in options.items():
        future = futures.get((trade_date, symbol, expiry))
        if future is None:
            continue
        call = option_pair.get("CE", {})
        put = option_pair.get("PE", {})
        call_oi = call.get("oi")
        put_oi = put.get("oi")
        if not call_oi and not put_oi:
            continue
        combined.append(
            {
                "Trade Date": trade_date,
                "Stock Symbol": symbol,
                "Expiry Date": expiry,
                "Future Price": future["future_price"],
                "Future OI": future["future_oi"],
                "Futures Change in OI": future["future_change_oi"],
                "Futures Volume": future["future_volume"],
                "Strike Price": strike,
                "Call OI": call_oi,
                "Call Change in OI": call.get("change_oi"),
                "Call Volume": call.get("volume"),
                "Call Price": call.get("price"),
                "Call Delta": deltas.get((trade_date, symbol, expiry, strike, "CE")),
                "Put OI": put_oi,
                "Put Change in OI": put.get("change_oi"),
                "Put Volume": put.get("volume"),
                "Put Price": put.get("price"),
                "Put Delta": deltas.get((trade_date, symbol, expiry, strike, "PE")),
            }
        )

    combined.sort(key=lambda row: (row["Trade Date"], row["Stock Symbol"], row["Expiry Date"], row["Strike Price"]))
    return combined


def main():
    if len(sys.argv) != 5:
        raise SystemExit("Usage: process_nse_fo_bhavcopy.py START_DATE END_DATE WORK_DIR OUTPUT_CSV")

    start = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
    end = datetime.strptime(sys.argv[2], "%Y-%m-%d").date()
    work_dir = Path(sys.argv[3])
    output_csv = Path(sys.argv[4])
    download_dir = work_dir / "downloads"
    extra_dir = work_dir / "extras"
    download_dir.mkdir(parents=True, exist_ok=True)
    extra_dir.mkdir(parents=True, exist_ok=True)
    output_csv.parent.mkdir(parents=True, exist_ok=True)

    downloaded = []
    extra_downloaded = {}
    skipped = []
    for trade_date in daterange(start, end):
        if trade_date.weekday() >= 5:
            skipped.append({"date": trade_date.isoformat(), "reason": "weekend"})
            continue
        zip_path, status = download_file(trade_date, download_dir)
        if zip_path is None:
            skipped.append({"date": trade_date.isoformat(), "reason": status})
        else:
            downloaded.append({"date": trade_date.isoformat(), "status": status, "file": str(zip_path)})
            extra_downloaded[trade_date.isoformat()] = download_extra_files(trade_date, extra_dir)

    rows = process_files([Path(item["file"]) for item in downloaded], extra_downloaded)
    fieldnames = OUTPUT_FIELDS
    with output_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    metadata = {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "source_url": BASE_URL,
        "downloaded": downloaded,
        "extra_downloaded": extra_downloaded,
        "skipped": skipped,
        "row_count": len(rows),
        "source_notes": [
            "Change in OI and volume are from the F&O-UDiFF Common Bhavcopy Final file.",
            "Delta is from NSE F&O-NCL Contract Delta.",
            "Rows where both Call OI and Put OI are zero or blank are excluded.",
        ],
    }
    (output_csv.with_suffix(".metadata.json")).write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(json.dumps({"rows": len(rows), "files": len(downloaded), "skipped": len(skipped)}, indent=2))


if __name__ == "__main__":
    main()
