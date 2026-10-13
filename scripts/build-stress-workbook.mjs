import fs from 'node:fs/promises';
import path from 'node:path';
import { FileBlob, SpreadsheetFile } from '@oai/artifact-tool';

const [inputPath, outputPath, rowCountText = '20'] = process.argv.slice(2);
if (!inputPath || !outputPath) {
  throw new Error('Usage: node build-stress-workbook.mjs <input.xlsx> <output.xlsx> [rows]');
}

const rowCount = Number(rowCountText);
if (!Number.isInteger(rowCount) || rowCount < 2 || rowCount > 200 || rowCount % 2 !== 0) {
  throw new Error('Row count must be an even integer from 2 to 200.');
}

const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(inputPath));
const sheet = workbook.worksheets.getItemAt(0);
const seeds = sheet.getRange('A2:B3').values;
if (seeds.length !== 2 || seeds.some((row) => !String(row[0] ?? '').trim())) {
  throw new Error('Source workbook must contain two trademark rows at A2:B3.');
}
const rows = Array.from({ length: rowCount }, (_, index) => [...seeds[index % 2]]);
sheet.getRange(`A2:B${rowCount + 1}`).values = rows;
await fs.mkdir(path.dirname(outputPath), { recursive: true });
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
console.log(JSON.stringify({ inputPath, outputPath, rowCount, marks: seeds.map((row) => row[0]) }));
