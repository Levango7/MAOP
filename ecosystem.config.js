/**
 * PM2 Process Configuration for MAOP
 *
 * P1 fix (2026-09-17): provides standardized process management for bare-metal / VM deployment.
 * Aligns with Docker Compose ports and environment variables.
 *
 * Usage:
 *   pm2 start ecosystem.config.js --env production
 *   pm2 save && pm2 startup   # enable auto-restart on boot
 *
 * Environment:
 *   Copy .env.example to .env and configure before starting.
 *   PM2 will load .env automatically via env option.
 */

const fs = require('fs');
const path = require('path');

// Load .env if dotenv not installed
function loadEnv(envPath) {
  if (!fs.existsSync(envPath)) return {};
  const result = {};
  for (const line of fs.readFileSync(envPath, 'utf8').split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const eqIdx = trimmed.indexOf('=');
    if (eqIdx < 0) continue;
    const key = trimmed.slice(0, eqIdx).trim();
    const val = trimmed.slice(eqIdx + 1).trim().replace(/^["']|["']$/g, '');
    if (key) result[key] = val;
  }
  return result;
}

const env = loadEnv(path.join(__dirname, '.env'));

module.exports = {
  apps: [
    {
      name: 'maop-dashboard',
      script: 'py/maop/dashboard/server.py',
      interpreter: process.env.MAOP_PYTHON || 'python',
      cwd: __dirname,
      instances: parseInt(env.MAOP_WORKERS || '4', 10),
      exec_mode: 'cluster',
      max_restarts: 10,
      exp_backoff_restart_delay: 200,
      max_memory_restart: '2G',
      env: {
        MAOP_ENV: 'production',
        MAOP_DASH_PORT: env.MAOP_DASH_PORT || '9079',
        MAOP_DATA_DIR: env.MAOP_DATA_DIR || './data',
        ...env,
      },
      error_file: './logs/dashboard-err.log',
      out_file: './logs/dashboard-out.log',
      log_date_format: 'YYYY-MM-DD HH:mm:ss',
      merge_logs: true,
      time: true,
    },
    {
      name: 'maop-agent-exec',
      script: 'py/maop/worker/agent_exec.py',
      interpreter: process.env.MAOP_PYTHON || 'python',
      cwd: __dirname,
      instances: parseInt(env.MAOP_AGENT_EXEC_WORKERS || '2', 10),
      exec_mode: 'cluster',
      max_restarts: 10,
      exp_backoff_restart_delay: 200,
      max_memory_restart: '1G',
      env: {
        MAOP_ENV: 'production',
        MAOP_DATA_DIR: env.MAOP_DATA_DIR || './data',
        ...env,
      },
      error_file: './logs/agent-exec-err.log',
      out_file: './logs/agent-exec-out.log',
      log_date_format: 'YYYY-MM-DD HH:mm:ss',
      merge_logs: true,
      time: true,
    },
    {
      name: 'maop-queue-worker',
      script: 'py/maop/worker/queue_worker.py',
      interpreter: process.env.MAOP_PYTHON || 'python',
      cwd: __dirname,
      instances: 1,
      exec_mode: 'fork',
      max_restarts: 10,
      exp_backoff_restart_delay: 500,
      max_memory_restart: '512M',
      env: {
        MAOP_ENV: 'production',
        MAOP_DATA_DIR: env.MAOP_DATA_DIR || './data',
        ...env,
      },
      error_file: './logs/queue-worker-err.log',
      out_file: './logs/queue-worker-out.log',
      log_date_format: 'YYYY-MM-DD HH:mm:ss',
      merge_logs: true,
      time: true,
    },
  ],
};