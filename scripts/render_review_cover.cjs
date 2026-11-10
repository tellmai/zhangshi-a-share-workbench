// Optional CLI rasterizer. Core app also exports PNG in the browser without Node.
// NODE_PATH may point to a locally installed sharp package; no network downloads.
const fs = require('node:fs');
const path = require('node:path');
const sharp = require('sharp');
async function main() {
  const file = process.argv[2];
  if (!file || path.extname(file).toLowerCase() !== '.svg') throw new Error('Pass a saved cover.svg path');
  const output = path.join(path.dirname(path.resolve(file)), 'cover.png');
  await sharp(fs.readFileSync(file)).png().toFile(output);
  const metadata = await sharp(output).metadata();
  if (metadata.width !== 1920 || metadata.height !== 1080) throw new Error('Cover must be 1920x1080');
  console.log(JSON.stringify({path: output, width: metadata.width, height: metadata.height}));
}
main().catch(error => {console.error(error.message); process.exitCode = 1;});
