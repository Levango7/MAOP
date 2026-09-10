// Run vitest and capture output
const { execSync } = require('child_process');
try {
  const output = execSync('npx vitest run', {
    cwd: 'F:\\Nexus\\MAOP\\dashboard-enterprise',
    encoding: 'utf8',
    timeout: 300000,
    stdio: ['pipe', 'pipe', 'pipe']
  });
  // Get last 2000 chars of stdout
  console.log(output.slice(-2000));
  console.log('\n=== EXIT CODE: 0 ===');
} catch (err) {
  console.log('=== STDOUT (last 2000 chars) ===');
  console.log((err.stdout || '').slice(-2000));
  console.log('=== STDERR (last 2000 chars) ===');
  console.log((err.stderr || '').slice(-2000));
  console.log(`\n=== EXIT CODE: ${err.status} ===`);
}