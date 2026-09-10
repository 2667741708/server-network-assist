import { Component, Input } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { Host, NetworkProfile, ProbeResult } from './models';

@Component({
  selector: 'app-overview',
  imports: [MatButtonModule, MatCardModule],
  template: `
    <header class="page-head">
      <div>
        <p class="eyebrow">OVERVIEW</p>
        <h1>网络态势</h1>
        <p>快速确认哪些主机可达、哪些需要借助出口网络。</p>
      </div>
      <button mat-stroked-button (click)="navigate('network')">打开网络借助</button>
    </header>
    <section class="metric-grid">
      <mat-card
        ><span>已管理主机</span><strong>{{ hosts.length }}</strong
        ><small>{{ onlineCount }} 台已通过公网探测</small></mat-card
      >
      <mat-card
        ><span>活动借网方案</span><strong>{{ enabledCount }}</strong
        ><small>{{ profiles.length }} 个方案已保存</small></mat-card
      >
      <mat-card
        ><span>需关注</span><strong>{{ attentionCount }}</strong
        ><small>SSH 失败、无公网或方案异常</small></mat-card
      >
    </section>
    <section class="panel-section">
      <div class="section-title">
        <div>
          <h2>主机快照</h2>
          <p>探测结果为空时，请在“网络借助”页执行一次检查。</p>
        </div>
      </div>
      <div class="host-grid">
        @for (host of hosts; track host.id) {
          <mat-card class="host-card"
            ><div class="row-between">
              <strong class="long-value">{{ host.name }}</strong
              ><span class="status-dot" [class.good]="probe(host.id)?.internet"></span>
            </div>
            <p class="mono long-value">
              {{ host.username }}&#64;{{ host.address }}:{{ host.port }}
            </p>
            <p>
              {{
                probe(host.id)?.internet
                  ? '公网可用'
                  : probe(host.id)?.ssh
                    ? 'SSH 可达，公网不可用'
                    : '尚未探测或 SSH 不可达'
              }}
            </p>
          </mat-card>
        } @empty {
          <div class="empty-state">尚未添加 SSH 主机。</div>
        }
      </div>
    </section>
  `,
})
export class OverviewComponent {
  @Input({ required: true }) hosts: Host[] = [];
  @Input({ required: true }) profiles: NetworkProfile[] = [];
  @Input({ required: true }) probes: ProbeResult[] = [];
  @Input({ required: true }) navigate!: (page: string) => void;
  probe(id: string) {
    return this.probes.find((value) => value.id === id);
  }
  get onlineCount() {
    return this.probes.filter((value) => value.internet).length;
  }
  get enabledCount() {
    return this.profiles.filter((value) => value.state === 'enabled').length;
  }
  get attentionCount() {
    return (
      this.probes.filter((value) => !value.ssh || !value.internet).length +
      this.profiles.filter((value) => value.state === 'error').length
    );
  }
}
