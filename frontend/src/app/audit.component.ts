import { Component, Input } from '@angular/core';
import { DatePipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { AuditEvent } from './models';

@Component({
  selector: 'app-audit',
  imports: [DatePipe, FormsModule, MatFormFieldModule, MatInputModule, MatSelectModule],
  styles: [`
    :host{display:block}.audit-toolbar{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:18px}.audit-toolbar mat-form-field{min-width:180px;flex:1}.audit-toolbar .event-search{min-width:260px}.audit-note{padding:12px 16px;border-left:3px solid #d58a22;background:#fff8e9;color:#604516;margin-bottom:18px}.audit-note p{margin:4px 0 0;color:inherit}.audit-summary{max-width:460px;white-space:pre-wrap;overflow-wrap:anywhere}.audit-table{min-width:1080px}.audit-table td{vertical-align:top}.audit-table .mono{overflow-wrap:anywhere}.audit-empty{text-align:center;padding:24px;color:var(--muted)}
    @media(max-width:700px){.audit-toolbar{align-items:stretch}.audit-toolbar mat-form-field,.audit-toolbar .event-search{width:100%;min-width:0}}
  `],
  template: `
    <header class="page-head">
      <div>
        <p class="eyebrow">ADMIN AUDIT LOG</p>
        <h1>审计</h1>
        <p>记录操作者/来源、对象和摘要；敏感 token、Authorization、密码和 private key 永不在表格中展示。</p>
      </div>
    </header>
    <section class="audit-note"><strong>安全边界</strong><p>当前审计 API 没有为每条事件单独提供 revision 字段。页面显示后端实际提供的 revision，否则标记“未记录”，不会用当前时间或猜测值填充。</p></section>
    <section class="panel-section">
      <div class="audit-toolbar">
        <mat-form-field appearance="outline" class="event-search"><mat-label>搜索事件、对象或摘要</mat-label><input matInput [(ngModel)]="query" /></mat-form-field>
        <mat-form-field appearance="outline"><mat-label>事件类型</mat-label><mat-select [(ngModel)]="eventType"><mat-option value="all">全部类型</mat-option>@for(type of eventTypes; track type){<mat-option [value]="type">{{ type }}</mat-option>}</mat-select></mat-form-field>
      </div>
      <div class="table-scroll">
        <table class="data-table audit-table">
          <thead><tr><th>时间</th><th>事件</th><th>对象类型</th><th>对象 ID</th><th>操作者/来源</th><th>revision</th><th>摘要</th></tr></thead>
          <tbody>
            @for (event of filteredEvents; track event.id) {
              <tr><td>{{ event.created_at * 1000 | date: 'yyyy-MM-dd HH:mm:ss' }}</td><td class="mono">{{ event.action }}</td><td>{{ objectType(event) }}</td><td class="mono long-value">{{ event.target || '—' }}</td><td class="mono long-value">{{ event.actor || event.remote_ip || '—' }}</td><td class="mono long-value">{{ revision(event) }}</td><td class="audit-summary">{{ summary(event) }}</td></tr>
            } @empty { <tr><td colspan="7" class="audit-empty">暂无匹配的审计事件。</td></tr> }
          </tbody>
        </table>
      </div>
    </section>
  `,
})
export class AuditComponent {
  @Input({ required: true }) events: AuditEvent[] = [];
  query = '';
  eventType = 'all';

  get eventTypes() {
    return Array.from(new Set(this.events.map((event) => this.objectType(event)))).sort();
  }

  get filteredEvents() {
    const query = this.query.trim().toLowerCase();
    return this.events.filter((event) => {
      const type = this.objectType(event);
      const text = `${event.action} ${event.target} ${event.actor} ${event.remote_ip} ${this.summary(event)}`.toLowerCase();
      return (this.eventType === 'all' || type === this.eventType) && (!query || text.includes(query));
    });
  }

  objectType(event: AuditEvent) {
    const value = `${event.action} ${event.target}`.toLowerCase();
    if (value.includes('customer') || value.includes('subscription')) return 'Customer / Subscription';
    if (value.includes('device')) return 'Device';
    if (value.includes('lease')) return 'Lease';
    if (value.includes('grant') || value.includes('source')) return 'Grant / Node';
    return 'System';
  }

  revision(event: AuditEvent) {
    const value = event.details?.['revision'];
    return typeof value === 'string' ? value : '未记录';
  }

  summary(event: AuditEvent) {
    return this.safeJson(event.details) || event.action;
  }

  private safeJson(value: unknown) {
    try {
      return JSON.stringify(this.redact(value));
    } catch {
      return '详情不可读取';
    }
  }

  private redact(value: unknown): unknown {
    const sensitive = /token|authorization|private.?key|wireguard.?private|password|secret|bearer/i;
    if (Array.isArray(value)) return value.map((item) => this.redact(item));
    if (value && typeof value === 'object') {
      return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, sensitive.test(key) ? '[REDACTED]' : this.redact(item)]));
    }
    return value;
  }
}
