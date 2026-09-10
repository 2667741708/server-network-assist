import { Component, Input } from '@angular/core';
import { DatePipe, JsonPipe } from '@angular/common';
import { AuditEvent } from './models';

@Component({
  selector: 'app-audit',
  imports: [DatePipe, JsonPipe],
  template: `<header class="page-head">
      <div>
        <p class="eyebrow">AUDIT LOG</p>
        <h1>审计日志</h1>
        <p>记录登录、配置修改、终端打开与网络切换；敏感字段不会写入详情。</p>
      </div>
    </header>
    <section class="panel-section">
      <div class="table-scroll">
        <table class="data-table">
          <thead>
            <tr>
              <th>时间</th>
              <th>操作</th>
              <th>目标</th>
              <th>详情</th>
              <th>来源</th>
            </tr>
          </thead>
          <tbody>
            @for (event of events; track event.id) {
              <tr>
                <td>{{ event.created_at * 1000 | date: 'yyyy-MM-dd HH:mm:ss' }}</td>
                <td class="mono">{{ event.action }}</td>
                <td class="long-value">{{ event.target }}</td>
                <td>
                  <pre class="audit-detail">{{ event.details | json }}</pre>
                </td>
                <td class="mono long-value">{{ event.remote_ip }}</td>
              </tr>
            } @empty {
              <tr>
                <td colspan="5">暂无审计事件</td>
              </tr>
            }
          </tbody>
        </table>
      </div>
    </section>`,
})
export class AuditComponent {
  @Input({ required: true }) events: AuditEvent[] = [];
}
