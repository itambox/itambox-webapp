// Node's bare test discovery treats a file named test.ts as a test module.
// Keep the public import stable while avoiding execution of the Playwright
// fixture module when `node --test` scans the repository.
if (process.argv.includes('--test') || process.env.NODE_TEST_CONTEXT) {
  module.exports = {};
} else {
  module.exports = require('./playwright-fixtures.ts');
}
