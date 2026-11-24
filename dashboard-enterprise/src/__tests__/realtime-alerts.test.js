// Tests for alertToastFromMessage — WS 告警广播 → store toast 的纯函数桥。
//
// 后端契约(observability_service._ws_broadcast_alert):命中即
// broadcaster(alert.model_dump()) → /ws 原样 send_json,无 type 信封;
// 快照心跳带 type:'snapshot'。识别键 = alert_id + rule_id 双键。

import { describe, it, expect } from 'vitest';
import { alertToastFromMessage } from '../stores/realtime.js';

describe('alertToastFromMessage', () => {
  it('maps an alert broadcast to rule: message text and warn tone', () => {
    const msg = {
      alert_id: 'a1', rule_id: 'r1', severity: 'warning',
      message: 'cpu-hot fired', metadata: { rule_name: 'cpu-hot' },
    };
    expect(alertToastFromMessage(msg)).toEqual({ text: 'cpu-hot: cpu-hot fired', tone: 'warn' });
  });

  it('falls back to rule_id and bare rule name when metadata/message absent', () => {
    const msg = { alert_id: 'a2', rule_id: 'r9', severity: 'info' };
    expect(alertToastFromMessage(msg)).toEqual({ text: 'r9', tone: 'info' });
  });

  it('maps critical/error severities to the error tone', () => {
    expect(alertToastFromMessage({ alert_id: 'a', rule_id: 'r', severity: 'critical' }).tone).toBe('error');
    expect(alertToastFromMessage({ alert_id: 'a', rule_id: 'r', severity: 'error' }).tone).toBe('error');
  });

  it('rejects snapshot heartbeats and non-alert messages', () => {
    expect(alertToastFromMessage({ type: 'snapshot', ts: 1, live: [] })).toBeNull();
    expect(alertToastFromMessage({})).toBeNull();
    expect(alertToastFromMessage(null)).toBeNull();
    expect(alertToastFromMessage('raw string')).toBeNull();
    expect(alertToastFromMessage({ alert_id: 'a' })).toBeNull();
  });
});
