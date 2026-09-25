const fs = require('fs');
const vm = require('vm');

const html = fs.readFileSync('index.html', 'utf8');

// Find all <script>...</script> blocks
const scriptRegex = /<script\b([^>]*)>([\s\S]*?)<\/script>/gi;
let match;
let count = 0;
let errors = 0;

while ((match = scriptRegex.exec(html)) !== null) {
    const attrs = match[1];
    const content = match[2].trim();
    if (!attrs.includes('src=') && content.length > 0) {
        count++;
        try {
            new vm.Script(content, { filename: `script_block_${count}.js` });
            console.log(`[PASS] Script block ${count} (length: ${content.length} chars) compiled successfully.`);
        } catch (e) {
            console.error(`[FAIL] Script block ${count}:`, e.message);
            errors++;
        }
    }
}

console.log(`Total inline script blocks checked: ${count}`);
if (errors > 0) {
    process.exit(1);
}
