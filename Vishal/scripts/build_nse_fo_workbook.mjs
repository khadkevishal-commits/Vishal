import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";


const __filename = fileURLToPath(import.meta.url);
const repoRoot = path.resolve(path.dirname(__filename), "..");

const inputCsv = process.argv[2];
const metadataPath = process.argv[3];
const outputPath = process.argv[4];
const previewPath = process.argv[5];

if (!inputCsv || !metadataPath || !outputPath || !previewPath) {
  throw new Error("Usage: build_nse_fo_workbook.mjs INPUT_CSV METADATA_JSON OUTPUT_XLSX PREVIEW_PNG");
}

function columnName(index) {
  let value = index + 1;
  let name = "";
  while (value > 0) {
    const remainder = (value - 1) % 26;
    name = String.fromCharCode(65 + remainder) + name;
    value = Math.floor((value - 1) / 26);
  }
  return name;
}

const csvText = await fs.readFile(path.resolve(repoRoot, inputCsv), "utf8");
const metadata = JSON.parse(await fs.readFile(path.resolve(repoRoot, metadataPath), "utf8"));
const workbook = await Workbook.fromCSV(csvText, { sheetName: "Combined Data" });
const dataSheet = workbook.worksheets.getItem("Combined Data");
dataSheet.showGridLines = false;

const rowCount = metadata.row_count + 1;
const colCount = 10;
const lastCol = columnName(colCount - 1);
const dataRange = `A1:${lastCol}${rowCount}`;

dataSheet.freezePanes.freezeRows(1);
dataSheet.getRange("A1:J1").format = {
  fill: "#174E63",
  font: { bold: true, color: "#FFFFFF" },
  wrapText: true,
};
dataSheet.getRange(`A2:A${rowCount}`).format.numberFormat = "yyyy-mm-dd";
dataSheet.getRange(`C2:C${rowCount}`).format.numberFormat = "yyyy-mm-dd";
dataSheet.getRange(`D2:D${rowCount}`).format.numberFormat = "#,##0.00";
dataSheet.getRange(`E2:E${rowCount}`).format.numberFormat = "#,##0";
dataSheet.getRange(`F2:F${rowCount}`).format.numberFormat = "#,##0.00";
dataSheet.getRange(`G2:G${rowCount}`).format.numberFormat = "#,##0";
dataSheet.getRange(`H2:H${rowCount}`).format.numberFormat = "#,##0.00";
dataSheet.getRange(`I2:I${rowCount}`).format.numberFormat = "#,##0";
dataSheet.getRange(`J2:J${rowCount}`).format.numberFormat = "#,##0.00";

dataSheet.getRange("A:A").format.columnWidth = 12;
dataSheet.getRange("B:B").format.columnWidth = 16;
dataSheet.getRange("C:C").format.columnWidth = 12;
dataSheet.getRange("D:J").format.columnWidth = 14;

const summary = workbook.worksheets.add("Summary");
summary.showGridLines = false;
summary.getRange("A1:J1").merge();
summary.getRange("A1").values = [["NSE F&O Bhavcopy"]];
summary.getRange("A1").format = {
  fill: "#174E63",
  font: { bold: true, color: "#FFFFFF", size: 16 },
};

const downloadedDates = metadata.downloaded.map((item) => item.date);
const summaryRows = [
  ["Field", "Value"],
  ["Included period", `${metadata.start_date} to ${metadata.end_date}`],
  ["Trading-day files included", metadata.downloaded.length],
  ["Combined data rows", metadata.row_count],
  ["Latest included trade date", downloadedDates[downloadedDates.length - 1] || ""],
  ["Source archive folder", metadata.source_url],
  ["Source file pattern", "BhavCopy_NSE_FO_0_0_0_YYYYMMDD_F_0000.csv.zip"],
  ["Join logic", "STF futures joined to STO CE/PE rows by Trade Date + Stock Symbol + Expiry Date + Strike Price"],
  ["Price field", "Close price (ClsPric)"],
  ["OI field", "Open interest (OpnIntrst)"],
];
summary.getRange(`A3:B${summaryRows.length + 2}`).values = summaryRows;
summary.getRange("A3:B3").format = {
  fill: "#E0F2FE",
  font: { bold: true, color: "#0F172A" },
};
summary.getRange(`A4:A${summaryRows.length + 2}`).format = {
  fill: "#F8FAFC",
  font: { bold: true, color: "#334155" },
};
summary.getRange(`A3:B${summaryRows.length + 2}`).format.borders = {
  preset: "all",
  style: "thin",
  color: "#CBD5E1",
};
summary.getRange("A:A").format.columnWidth = 28;
summary.getRange("B:B").format.columnWidth = 96;
summary.getRange("B8:B10").format.wrapText = true;

const dateHeaderRow = [["Included Trade Dates"]];
const dateRows = downloadedDates.map((dateValue) => [dateValue]);
summary.getRange("D3:D3").values = dateHeaderRow;
summary.getRange(`D4:D${dateRows.length + 3}`).values = dateRows;
summary.getRange("D3").format = {
  fill: "#E0F2FE",
  font: { bold: true, color: "#0F172A" },
};
summary.getRange(`D3:D${dateRows.length + 3}`).format.borders = {
  preset: "all",
  style: "thin",
  color: "#CBD5E1",
};
summary.getRange("D:D").format.columnWidth = 20;

const skipped = metadata.skipped.map((item) => [item.date, item.reason]);
summary.getRange("F3:G3").values = [["Skipped Dates", "Reason"]];
if (skipped.length > 0) {
  summary.getRange(`F4:G${skipped.length + 3}`).values = skipped;
}
summary.getRange("F3:G3").format = {
  fill: "#E0F2FE",
  font: { bold: true, color: "#0F172A" },
};
summary.getRange(`F3:G${Math.max(skipped.length + 3, 3)}`).format.borders = {
  preset: "all",
  style: "thin",
  color: "#CBD5E1",
};
summary.getRange("F:F").format.columnWidth = 18;
summary.getRange("G:G").format.columnWidth = 18;

const inspect = await workbook.inspect({
  kind: "table",
  range: "Combined Data!A1:J8",
  include: "values,formulas",
  tableMaxRows: 8,
  tableMaxCols: 10,
});
console.log(inspect.ndjson);

const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 50 },
  summary: "final formula error scan",
});
console.log(errors.ndjson);

const preview = await workbook.render({
  sheetName: "Summary",
  autoCrop: "all",
  scale: 1,
  format: "png",
});
await fs.mkdir(path.dirname(path.resolve(repoRoot, previewPath)), { recursive: true });
await fs.writeFile(path.resolve(repoRoot, previewPath), new Uint8Array(await preview.arrayBuffer()));

await fs.mkdir(path.dirname(path.resolve(repoRoot, outputPath)), { recursive: true });
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(path.resolve(repoRoot, outputPath));
